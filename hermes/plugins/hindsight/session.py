"""Session lifecycle management for Hindsight provider."""

from __future__ import annotations

import contextlib
import logging
import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from agent.secret_scope import UnscopedSecretError, get_secret
from hermes_cli.config import cfg_get
from hermes_constants import get_hermes_home
from hermes_time import now as _hermes_now

from .client import HindsightClientManager
from .embedded import (
    _build_embedded_profile_env,
    _check_local_runtime,
    _load_simple_env,
    _local_runtime_hint,
    _materialize_embedded_profile_env,
    _may_rewrite_profile_env,
)
from .health import HealthChecker
from .recall import RecallManager
from .retain import RetainManager
from .settings import (
    _DEFAULT_API_URL,
    _DEFAULT_IDLE_TIMEOUT,
    _DEFAULT_LOCAL_URL,
    _DEFAULT_RETAIN_SOURCE,
    _DEFAULT_TIMEOUT,
    _PROVIDER_DEFAULT_MODELS,
    _SYSTEM_PROMPT_TAILS,
    _VALID_BUDGETS,
    _load_config,
    _maybe_upgrade_client,
    _normalize_observation_scopes,
    _normalize_retain_tags,
    _parse_int_setting,
    _resolve_bank_id_template,
)
from .tools import ToolHandler

logger = logging.getLogger(__name__)

_KNOWN_CONFIG_KEYS = {
    "mode", "api_key", "apiKey", "api_url", "apiUrl", "llm_provider", "llm_base_url", "llm_model", "llm_api_key",
    "bank_id", "bank_id_template", "bank_mission", "bank_retain_mission", "recall_budget", "budget",
    "memory_mode", "recall_prefetch_method", "prefetch_method", "retain_tags", "observation_scopes",
    "retain_source", "retain_user_prefix", "retain_assistant_prefix", "recall_tags", "recall_tags_match",
    "recall_types", "auto_recall", "recall_sync", "recall_indicator", "retain_indicator", "auto_retain",
    "retain_every_n_turns", "retain_async", "prefetch_waits_for_retain", "prefetch_retain_drain_timeout",
    "retain_context", "recall_max_tokens", "recall_max_input_chars", "recall_prompt_preamble",
    "timeout", "idle_timeout", "port_health_grace_timeout", "profile", "banks",
}

_SESSION_KWARGS = (
    "platform", "user_id", "user_name", "chat_id", "chat_name", "chat_type",
    "thread_id", "agent_identity", "agent_workspace",
)
_METADATA_ATTRS = (
    "session_id", "platform", "user_id", "user_name", "chat_id", "chat_name",
    "chat_type", "thread_id", "agent_identity",
)


class SessionManager:
    """Manages the full session lifecycle: initialize, switch, shutdown."""

    def __init__(
        self,
        client_manager: HindsightClientManager,
        retain_manager: RetainManager,
        recall_manager: RecallManager,
        tool_handler: ToolHandler,
        health_checker: HealthChecker,
    ):
        self._client_manager = client_manager
        self._retain_manager = retain_manager
        self._recall_manager = recall_manager
        self._tool_handler = tool_handler
        self._health_checker = health_checker

        # Session state
        self._session_id = ""
        self._parent_session_id = ""
        self._document_id = ""
        self._status_callback: Callable[[str], None] | None = None
        self._warning_callback: Callable[[str], None] | None = None
        self._platform = "cli"

        # Config-derived state
        self._config: dict = {}
        self._mode = "cloud"
        self._api_url = _DEFAULT_API_URL
        self._llm_base_url = ""
        self._timeout = _DEFAULT_TIMEOUT
        self._idle_timeout = _DEFAULT_IDLE_TIMEOUT
        self._bank_id = "hermes"
        self._budget = "mid"
        self._bank_id_template = ""
        self._bank_mission = ""
        self._bank_retain_mission = None
        self._memory_mode = "hybrid"
        self._prefetch_method = "recall"
        self._retain_tags: list[str] = []
        self._tags: list[str] | None = None
        self._observation_scopes: Any = None
        self._retain_source = _DEFAULT_RETAIN_SOURCE
        self._retain_user_prefix = "User"
        self._retain_assistant_prefix = "Assistant"
        self._auto_retain = True
        self._retain_every_n_turns = 1
        self._retain_context = "conversation between Hermes Agent and the User"
        self._retain_async = True
        self._prefetch_waits_for_retain = True
        self._prefetch_retain_drain_timeout = 10.0
        self._retain_indicator = True
        self._recall_tags: list[str] | None = None
        self._recall_tags_match = "any"
        self._auto_recall = True
        self._recall_sync = False
        self._recall_max_tokens = 4096
        self._recall_max_input_chars = 800
        self._recall_types: list[str] = ["observation"]
        self._recall_prompt_preamble = ""
        self._recall_indicator = True

        # Turn state
        self._turn_counter = 0
        self._turn_index = 0
        self._session_turns: list[str] = []
        self._last_retained_turn_count = 0

        # Bind methods for sub-managers
        self._bind_getters()

    def _bind_getters(self) -> None:
        """Bind getter functions for sub-managers."""
        # Retain manager getters
        self._retain_manager._get_session_id = lambda: self._session_id
        self._retain_manager._get_parent_session_id = lambda: self._parent_session_id
        self._retain_manager._get_bank_id = lambda: self._bank_id
        self._retain_manager._get_retain_async = lambda: self._retain_async
        self._retain_manager._get_retain_context = lambda: self._retain_context
        self._retain_manager._get_retain_tags = lambda: self._retain_tags
        self._retain_manager._get_observation_scopes = lambda: self._observation_scopes
        self._retain_manager._get_retain_source = lambda: self._retain_source
        self._retain_manager._get_retain_user_prefix = lambda: self._retain_user_prefix
        self._retain_manager._get_retain_assistant_prefix = lambda: self._retain_assistant_prefix
        self._retain_manager._get_turn_index = lambda: self._turn_index
        self._retain_manager._get_session_turns = lambda: self._session_turns
        self._retain_manager._get_metadata_attrs = lambda: [
            (name, getattr(self, f"_{name}")) for name in _METADATA_ATTRS if getattr(self, f"_{name}")
        ]
        self._retain_manager._get_event_timestamp = self._event_timestamp
        self._retain_manager._is_shutting_down = self._retain_manager.shutting_down_event.is_set

        # Recall manager getters
        self._recall_manager._get_bank_id = lambda: self._bank_id
        self._recall_manager._get_budget = lambda: self._budget
        self._recall_manager._get_recall_max_tokens = lambda: self._recall_max_tokens
        self._recall_manager._get_recall_tags = lambda: self._recall_tags
        self._recall_manager._get_recall_tags_match = lambda: self._recall_tags_match
        self._recall_manager._get_recall_types = lambda: self._recall_types
        self._recall_manager._get_recall_prompt_preamble = lambda: self._recall_prompt_preamble
        self._recall_manager._get_prefetch_method = lambda: self._prefetch_method
        self._recall_manager._get_auto_recall = lambda: self._auto_recall
        self._recall_manager._get_recall_sync = lambda: self._recall_sync
        self._recall_manager._get_recall_max_input_chars = lambda: self._recall_max_input_chars
        self._recall_manager._get_memory_mode = lambda: self._memory_mode
        self._recall_manager._get_recall_indicator = lambda: self._recall_indicator
        self._recall_manager._is_shutting_down = self._retain_manager.shutting_down_event.is_set
        self._recall_manager._get_prefetch_waits_for_retain = lambda: self._prefetch_waits_for_retain
        self._recall_manager._get_prefetch_retain_drain_timeout = lambda: self._prefetch_retain_drain_timeout

        # Tool handler getters
        self._tool_handler._get_mode = lambda: self._mode
        self._tool_handler._get_bank_id = lambda: self._bank_id
        self._tool_handler._get_api_key = lambda: self._client_manager.api_key
        self._tool_handler._get_api_url = lambda: self._client_manager.api_url
        self._tool_handler._get_config = lambda: self._config

        # Health checker getters
        self._health_checker._get_mode = lambda: self._mode
        self._health_checker._get_bank_id = lambda: self._bank_id
        self._health_checker._get_client = self._client_manager.get_client
        self._health_checker._get_config = lambda: self._config

    def _event_timestamp(self) -> str:
        event_time = _hermes_now()
        if event_time.tzinfo is None or event_time.utcoffset() is None:
            event_time = event_time.astimezone()
        return event_time.isoformat(timespec="seconds")

    def _apply_connection_settings(self, cfg: dict) -> None:
        self._api_url = cfg.get("api_url") or get_secret("HINDSIGHT_API_URL", "") or self._client_manager.get_default_url(self._mode)
        self._llm_base_url = cfg.get("llm_base_url", "")

        banks = cfg_get(cfg, "banks", "hermes", default={})
        self._bank_id_template = cfg.get("bank_id_template", "") or ""
        self._bank_id = _resolve_bank_id_template(
            self._bank_id_template,
            fallback=cfg.get("bank_id") or banks.get("bankId", "hermes"),
            profile=self._agent_identity,
            workspace=self._agent_workspace,
            platform=self._platform,
            user=self._user_id,
            session=self._session_id,
        )
        budget = cfg.get("recall_budget") or cfg.get("budget") or banks.get("budget", "mid")
        self._budget = budget if budget in _VALID_BUDGETS else "mid"
        memory_mode = cfg.get("memory_mode", "hybrid")
        self._memory_mode = memory_mode if memory_mode in _SYSTEM_PROMPT_TAILS else "hybrid"
        prefetch_method = cfg.get("recall_prefetch_method") or cfg.get("prefetch_method", "recall")
        self._prefetch_method = prefetch_method if prefetch_method in {"recall", "reflect"} else "recall"
        self._bank_mission = cfg.get("bank_mission", "")
        self._bank_retain_mission = cfg.get("bank_retain_mission") or None

    def _apply_retain_settings(self, cfg: dict) -> None:
        def _cfg_or_env(key: str, env_var: str, default: str = "") -> Any:
            return cfg.get(key) or self._scoped_setting(env_var, default)

        self._retain_tags = _normalize_retain_tags(_cfg_or_env("retain_tags", "HINDSIGHT_RETAIN_TAGS"))
        self._tags = self._retain_tags or None
        self._observation_scopes = _normalize_observation_scopes(
            _cfg_or_env("observation_scopes", "HINDSIGHT_RETAIN_OBSERVATION_SCOPES")
        )
        self._retain_source = str(
            _cfg_or_env("retain_source", "HINDSIGHT_RETAIN_SOURCE", _DEFAULT_RETAIN_SOURCE)
        ).strip()
        self._retain_user_prefix = (
            str(_cfg_or_env("retain_user_prefix", "HINDSIGHT_RETAIN_USER_PREFIX", "User")).strip() or "User"
        )
        self._retain_assistant_prefix = (
            str(_cfg_or_env("retain_assistant_prefix", "HINDSIGHT_RETAIN_ASSISTANT_PREFIX", "Assistant")).strip()
            or "Assistant"
        )
        self._apply_retain_policy(cfg)

    def _apply_retain_policy(self, cfg: dict) -> None:
        self._auto_retain = cfg.get("auto_retain", True)
        self._retain_every_n_turns = max(1, int(cfg.get("retain_every_n_turns", 1)))
        self._retain_context = cfg.get("retain_context", "conversation between Hermes Agent and the User")
        self._retain_async = cfg.get("retain_async", True)
        self._retain_indicator = bool(cfg.get("retain_indicator", True))
        self._prefetch_waits_for_retain = cfg.get("prefetch_waits_for_retain", True)
        self._prefetch_retain_drain_timeout = float(cfg.get("prefetch_retain_drain_timeout", 10.0))

    def _apply_recall_settings(self, cfg: dict) -> None:
        self._recall_tags = cfg.get("recall_tags") or None
        self._recall_tags_match = cfg.get("recall_tags_match", "any")
        self._auto_recall = cfg.get("auto_recall", True)
        self._recall_sync = bool(cfg.get("recall_sync", False))
        self._recall_max_tokens = int(cfg.get("recall_max_tokens", 4096))
        self._recall_max_input_chars = int(cfg.get("recall_max_input_chars", 800))
        configured_types = cfg.get("recall_types")
        if isinstance(configured_types, str):
            self._recall_types = [t.strip() for t in configured_types.split(",") if t.strip()]
        else:
            self._recall_types = list([] if configured_types is None else configured_types) or ["observation"]
        self._recall_prompt_preamble = cfg.get("recall_prompt_preamble", "")
        self._recall_indicator = bool(cfg.get("recall_indicator", True))

    def _scoped_setting(self, name: str, default: str = "") -> str:
        try:
            value = get_secret(name, default)
        except UnscopedSecretError:
            return default
        return default if value is None else value

    def initialize(self, session_id: str, **kwargs) -> None:
        self._session_id = str(session_id or "").strip()
        self._parent_session_id = str(kwargs.get("parent_session_id", "") or "").strip()
        if callable(kwargs.get("status_callback")):
            self._status_callback = kwargs["status_callback"]
        self._warning_callback = kwargs.get("warning_callback") if callable(kwargs.get("warning_callback")) else None
        self._platform = str(kwargs.get("platform") or "cli")

        self._document_id = self._mint_document_id(self._session_id)
        _maybe_upgrade_client()

        self._config = cfg = _load_config()
        for name in _SESSION_KWARGS:
            setattr(self, f"_{name}", str(kwargs.get(name) or "").strip())
        self._turn_index = self._last_retained_turn_count = 0
        self._session_turns = []
        self._mode = cfg.get("mode", "cloud")
        self._timeout = self._int_setting("timeout", "HINDSIGHT_TIMEOUT", _DEFAULT_TIMEOUT)
        self._idle_timeout = self._int_setting("idle_timeout", "HINDSIGHT_IDLE_TIMEOUT", _DEFAULT_IDLE_TIMEOUT)
        if self._mode == "local":
            self._mode = "local_embedded"

        if self._mode == "local_embedded":
            self._client_manager.prepare_embedded_start()
            available, reason = _check_local_runtime()
            if not available:
                logger.warning(
                    "Hindsight local mode disabled because its runtime could not be imported: %s.%s",
                    reason,
                    _local_runtime_hint(reason),
                )
                self._mode = "disabled"
                return

        self._apply_connection_settings(cfg)
        self._apply_retain_settings(cfg)
        self._apply_recall_settings(cfg)

        client_version = "unknown"
        with contextlib.suppress(Exception):
            from importlib.metadata import version as pkg_version
            client_version = pkg_version("hindsight-client")

        logger.info(
            "Hindsight initialized: mode=%s, api_url=%s, bank=%s, budget=%s, memory_mode=%s, prefetch_method=%s, client=%s",
            self._mode,
            self._api_url,
            self._bank_id,
            self._budget,
            self._memory_mode,
            self._prefetch_method,
            client_version,
        )
        if self._bank_id_template:
            logger.debug(
                "Hindsight bank resolved from template %r: profile=%s workspace=%s platform=%s user=%s -> bank=%s",
                self._bank_id_template,
                self._agent_identity,
                self._agent_workspace,
                self._platform,
                self._user_id,
                self._bank_id,
            )

        if self._mode == "local_embedded":
            self._start_embedded_daemon()

    def _start_embedded_daemon(self) -> None:
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            msg = (
                "Hindsight local_embedded mode cannot run as root "
                "(PostgreSQL initdb refuses root). Skipping the embedded "
                "memory daemon. Run Hermes as a non-root user, or switch "
                "to cloud / local_external mode via 'hermes memory setup'."
            )
            logger.warning(msg)
            with contextlib.suppress(Exception):
                if self._warning_callback is not None:
                    self._warning_callback(msg)
                else:
                    from gateway.warning_notifications import render_notification
                    render_notification(
                        lambda: print(f"  ⚠ {msg}", file=sys.stderr, flush=True),
                        platform=self._platform,
                    )
            self._mode = "disabled"
            return
        from agent.memory_provider import spawn_context_thread
        spawn_context_thread(self._daemon_start_worker, name="hindsight-daemon-start").start()

    def _daemon_start_worker(self) -> None:
        import traceback

        log_path = get_hermes_home() / "logs" / "hindsight-embed.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)

        def _log(text: str) -> None:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(text)

        try:
            import hindsight_embed.daemon_embed_manager as dem
            from rich.console import Console

            dem.console = Console(file=open(log_path, "a", encoding="utf-8"), force_terminal=False)

            client = self._client_manager.get_client()
            profile = self._config.get("profile", "hermes")
            if _load_simple_env(self._embedded_profile_env_path(self._config)) != _build_embedded_profile_env(self._config):
                if _may_rewrite_profile_env(self._config):
                    _materialize_embedded_profile_env(self._config)
                    if client._manager.is_running(profile):
                        _log("\n=== Config changed, restarting daemon ===\n")
                        client._manager.stop(profile)
                else:
                    logger.warning(
                        "Hindsight profile env for %r holds an LLM API key this process cannot see "
                        "(no secret scope); leaving the file untouched so the daemon keeps its key.",
                        profile,
                    )
                    _log("\n=== Profile env has a key this process cannot see; left untouched ===\n")
            client._ensure_started()
            _log("\n=== Daemon started successfully ===\n")
        except Exception as e:
            _log(f"\n=== Daemon startup failed: {e} ===\n" + traceback.format_exc())

    def _embedded_profile_env_path(self, config: dict) -> Path:
        from .embedded import _embedded_profile_env_path
        return _embedded_profile_env_path(config)

    def _int_setting(self, key: str, env_var: str, default: int) -> int:
        value = self._config.get(key)
        return _parse_int_setting(os.environ.get(env_var) if value is None else value, default)

    def _mint_document_id(self, session_id: str) -> str:
        return f"{session_id}-{__import__('datetime').datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"

    def system_prompt_block(self) -> str:
        mode = self._memory_mode if self._memory_mode in _SYSTEM_PROMPT_TAILS else "hybrid"
        label = "" if mode == "hybrid" else f" ({mode} mode)"
        return f"# Hindsight Memory\nActive{label}. Bank: {self._bank_id}, budget: {self._budget}.\n{_SYSTEM_PROMPT_TAILS[mode]}"

    def on_session_switch(
        self, new_session_id: str, *, parent_session_id: str = "", reset: bool = False, **kwargs
    ) -> None:
        new_id = str(new_session_id or "").strip()
        if not new_id:
            return

        # 1. Flush buffered turns under the OLD identifiers
        self._retain_manager.flush_on_switch()

        # 2. Drain the old session's in-flight prefetch and drop its result
        self._recall_manager.join_prefetch_on_switch(3.0)

        # 3. Rotate to the new session
        if parent_session_id:
            self._parent_session_id = str(parent_session_id).strip()
        self._session_id, self._document_id = new_id, self._mint_document_id(new_id)
        self._session_turns = []
        self._turn_counter = self._turn_index = self._last_retained_turn_count = 0
        logger.debug(
            "Hindsight on_session_switch: new_session=%s parent=%s reset=%s doc=%s",
            self._session_id,
            self._parent_session_id,
            reset,
            self._document_id,
        )

    def sync_turn(self, user_content: str, assistant_content: str, *, session_id: str = "") -> None:
        if session_id:
            self._session_id = str(session_id).strip()
        turn_counter = [self._turn_counter]
        last_retained_turn_count = [self._last_retained_turn_count]
        document_id = [self._document_id]
        self._retain_manager.sync_turn(
            user_content,
            assistant_content,
            session_id=session_id,
            retain_every_n_turns=self._retain_every_n_turns,
            auto_retain=self._auto_retain,
            turn_counter=turn_counter,
            last_retained_turn_count=last_retained_turn_count,
            session_turns=self._session_turns,
            document_id=document_id,
        )
        self._turn_counter = self._turn_index = turn_counter[0]
        self._last_retained_turn_count = last_retained_turn_count[0]
        self._document_id = document_id[0]

    def shutdown(self) -> None:
        logger.debug("Hindsight shutdown: stopping writer + waiting for background threads")
        self._retain_manager.shutdown()
        self._recall_manager._join_prefetch(5.0)
        self._client_manager.close()

    # Delegate to sub-managers
    def get_tool_schemas(self) -> list[dict[str, Any]]:
        self._tool_handler.set_memory_mode(self._memory_mode)
        return self._tool_handler.get_schemas()

    def handle_tool_call(self, tool_name: str, args: dict, **kwargs) -> str:
        return self._tool_handler.handle_tool_call(tool_name, args, **kwargs)

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        return self._recall_manager.prefetch(query, session_id=session_id)

    def recall_status(self):
        return self._recall_manager.recall_status()

    def queue_prefetch(self, query: str, *, session_id: str = "") -> None:
        self._recall_manager.queue_prefetch(query, session_id=session_id)

    def health_check(self) -> dict:
        return self._health_checker.health_check()

    def is_available(self) -> bool:
        return self._health_checker.is_available()

    def unavailable_reason(self) -> str:
        return self._health_checker.unavailable_reason()

    def backup_paths(self) -> list[str]:
        with contextlib.suppress(Exception):
            return [str(Path.home() / ".hindsight")]
        return []

    def save_config(self, values: dict, hermes_home: str) -> None:
        from utils import atomic_json_write
        config_path = Path(hermes_home) / "hindsight" / "config.json"
        from utils import read_json_or_empty
        atomic_json_write(config_path, {**read_json_or_empty(config_path), **values}, mode=0o600)

    def post_setup(self, hermes_home: str, config: dict) -> None:
        from .setup import run_setup
        run_setup(self, hermes_home, config)

    def get_config_schema(self):
        return [
            {
                "key": "mode",
                "description": "Connection mode",
                "default": "cloud",
                "choices": ["cloud", "local_embedded", "local_external"],
            },
            {
                "key": "api_url",
                "description": "Hindsight Cloud API URL",
                "default": _DEFAULT_API_URL,
                "when": {"mode": "cloud"},
            },
            {
                "key": "api_key",
                "description": "Hindsight Cloud API key",
                "secret": True,
                "env_var": "HINDSIGHT_API_KEY",
                "url": "https://ui.hindsight.vectorize.io",
                "when": {"mode": "cloud"},
            },
            {
                "key": "api_url",
                "description": "Hindsight API URL",
                "default": _DEFAULT_LOCAL_URL,
                "when": {"mode": "local_external"},
            },
            {
                "key": "api_key",
                "description": "API key (optional)",
                "secret": True,
                "env_var": "HINDSIGHT_API_KEY",
                "when": {"mode": "local_external"},
            },
            {
                "key": "llm_provider",
                "description": "LLM provider",
                "default": "openai",
                "choices": [
                    "openai", "anthropic", "gemini", "groq", "openrouter",
                    "minimax", "ollama", "lmstudio", "openai_compatible",
                ],
                "when": {"mode": "local_embedded"},
            },
            {
                "key": "llm_base_url",
                "description": "Endpoint URL (e.g. http://192.168.1.10:8080/v1)",
                "default": "",
                "when": {"mode": "local_embedded", "llm_provider": "openai_compatible"},
            },
            {
                "key": "llm_api_key",
                "description": "LLM API key (optional for openai_compatible)",
                "secret": True,
                "env_var": "HINDSIGHT_LLM_API_KEY",
                "when": {"mode": "local_embedded"},
            },
            {
                "key": "llm_model",
                "description": "LLM model",
                "default": "gpt-4o-mini",
                "default_from": {"field": "llm_provider", "map": _PROVIDER_DEFAULT_MODELS},
                "when": {"mode": "local_embedded"},
            },
            {
                "key": "bank_id",
                "description": "Memory bank name (static fallback when bank_id_template is unset)",
                "default": "hermes",
            },
            {
                "key": "bank_id_template",
                "description": "Optional template to derive bank_id dynamically. Placeholders: {profile}, {workspace}, {platform}, {user}, {session}. Example: hermes-{profile}",
                "default": "",
            },
            {"key": "bank_mission", "description": "Mission/purpose description for the memory bank"},
            {"key": "bank_retain_mission", "description": "Custom extraction prompt for memory retention"},
            {
                "key": "recall_budget",
                "description": "Recall thoroughness",
                "default": "mid",
                "choices": ["low", "mid", "high"],
            },
            {
                "key": "memory_mode",
                "description": "Memory integration mode",
                "default": "hybrid",
                "choices": ["hybrid", "context", "tools"],
            },
            {
                "key": "recall_prefetch_method",
                "description": "Auto-recall method",
                "default": "recall",
                "choices": ["recall", "reflect"],
            },
            {
                "key": "retain_tags",
                "description": "Default tags applied to retained memories (comma-separated)",
                "default": "",
            },
            {
                "key": "observation_scopes",
                "description": "How observations are scoped during consolidation",
                "default": "",
            },
            {
                "key": "retain_source",
                "description": "Metadata source value attached to retained memories",
                "default": _DEFAULT_RETAIN_SOURCE,
            },
            {
                "key": "retain_user_prefix",
                "description": "Label used before user turns in retained transcripts",
                "default": "User",
            },
            {
                "key": "retain_assistant_prefix",
                "description": "Label used before assistant turns in retained transcripts",
                "default": "Assistant",
            },
            {
                "key": "recall_tags",
                "description": "Tags to filter when searching memories (comma-separated)",
                "default": "",
            },
            {
                "key": "recall_tags_match",
                "description": "Tag matching mode for recall",
                "default": "any",
                "choices": ["any", "all", "any_strict", "all_strict"],
            },
            {
                "key": "recall_types",
                "description": "Fact types to surface on recall",
                "default": "observation",
            },
            {"key": "auto_recall", "description": "Automatically recall memories before each turn", "default": True},
            {
                "key": "recall_sync",
                "description": "Recall synchronously against the current message",
                "default": False,
            },
            {
                "key": "recall_indicator",
                "description": "Show recall status line",
                "default": True,
            },
            {
                "key": "retain_indicator",
                "description": "Show retain status line",
                "default": True,
            },
            {"key": "auto_retain", "description": "Automatically retain conversation turns", "default": True},
            {"key": "retain_every_n_turns", "description": "Retain every N turns (1 = every turn)", "default": 1},
            {
                "key": "retain_async",
                "description": "Process retain asynchronously on the Hindsight server",
                "default": True,
            },
            {
                "key": "prefetch_waits_for_retain",
                "description": "Wait for retain to be recall-visible before prefetch",
                "default": True,
            },
            {
                "key": "prefetch_retain_drain_timeout",
                "description": "Max seconds to wait for retain visibility",
                "default": 10.0,
            },
            {
                "key": "retain_context",
                "description": "Context label for retained memories",
                "default": "conversation between Hermes Agent and the User",
            },
            {"key": "recall_max_tokens", "description": "Maximum tokens for recall results", "default": 4096},
            {
                "key": "recall_max_input_chars",
                "description": "Maximum input query length for auto-recall",
                "default": 800,
            },
            {"key": "recall_prompt_preamble", "description": "Custom preamble for recalled memories"},
            {"key": "timeout", "description": "API request timeout in seconds", "default": _DEFAULT_TIMEOUT},
            {
                "key": "idle_timeout",
                "description": "Embedded daemon idle timeout in seconds (0 disables auto-shutdown)",
                "default": _DEFAULT_IDLE_TIMEOUT,
                "when": {"mode": "local_embedded"},
            },
            {
                "key": "port_health_grace_timeout",
                "description": "Seconds to wait for slow daemon /health",
                "default": "",
                "when": {"mode": "local_embedded"},
            },
        ]