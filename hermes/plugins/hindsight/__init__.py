"""Hindsight memory plugin — MemoryProvider with knowledge graph, entity resolution
and multi-strategy retrieval; cloud (API key), local_external, or local_embedded.

Config: $HERMES_HOME/hindsight/config.json (profile-scoped), else ~/.hindsight/
config.json (legacy, shared), else env: HINDSIGHT_API_KEY / BANK_ID / BUDGET /
API_URL / MODE / TIMEOUT / IDLE_TIMEOUT / RETAIN_TAGS / RETAIN_OBSERVATION_SCOPES /
RETAIN_SOURCE / RETAIN_USER_PREFIX / RETAIN_ASSISTANT_PREFIX, and
HINDSIGHT_EMBED_PORT_HEALTH_GRACE_TIMEOUT (config.json port_health_grace_timeout).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import threading
from collections.abc import Callable
from typing import Any

from agent.async_utils import safe_schedule_threadsafe
from agent.memory_provider import MemoryProvider, RecallStatus, spawn_context_thread
from hermes_constants import get_hermes_home
from utils import read_json_or_empty

from .client import HindsightClientManager
from .health import HealthChecker
from .recall import RecallManager
from .retain import RetainManager
from .session import SessionManager
from .settings import (
    _DEFAULT_API_URL,
    _DEFAULT_IDLE_TIMEOUT,
    _DEFAULT_TIMEOUT,
    _HINDSIGHT_GLYPH,
    _LOCAL_MODES,
    _MIN_CLIENT_VERSION,
    _VALID_BUDGETS,
    _check_api_supports_update_mode_append,
    _mint_document_id,
    _cloud_api_key,
    _load_config,
)
from .templates import apply_template, fetch_hermes_templates, fetch_manifest
from .tools import ToolHandler

logger = logging.getLogger(__name__)

# Re-export for tests that patch these
get_hermes_home = get_hermes_home
read_json_or_empty = read_json_or_empty
os = os
_mint_document_id = _mint_document_id

# One long-lived event loop per process for Hindsight async calls; ephemeral
# loops would leak aiohttp sessions.
_loop: asyncio.AbstractEventLoop | None = None
_loop_thread: threading.Thread | None = None
_loop_lock = threading.Lock()


def _get_loop() -> asyncio.AbstractEventLoop:
    """Return a long-lived event loop running on a background thread."""
    global _loop, _loop_thread
    with _loop_lock:
        if _loop is not None and _loop.is_running():
            return _loop
        loop = _loop = asyncio.new_event_loop()
        _loop_thread = threading.Thread(
            target=lambda: (asyncio.set_event_loop(loop), loop.run_forever()),
            daemon=True,
            name="hindsight-loop",
        )
        _loop_thread.start()
        return _loop


def _run_sync(coro, timeout: float = _DEFAULT_TIMEOUT) -> Any:
    """Schedule *coro* on the shared loop and block until done."""
    future = safe_schedule_threadsafe(coro, _get_loop())
    if future is None:
        raise RuntimeError("Hindsight loop unavailable")
    return future.result(timeout=timeout)


class HindsightMemoryProvider(MemoryProvider):
    """Hindsight long-term memory with knowledge graph and multi-strategy retrieval."""

    def __init__(self):
        # Factory functions for client creation (can be monkeypatched by tests)
        def _cloud_client_factory():
            return getattr(self, '_new_cloud_client', self._new_cloud_client_impl)()

        def _embedded_client_factory():
            return getattr(self, '_new_embedded_client', self._new_embedded_client_impl)()

        # Create sub-managers
        client_manager = HindsightClientManager(
            mode="cloud",  # Will be updated in initialize
            api_url=_DEFAULT_API_URL,
            api_key="",
            llm_base_url="",
            config={},
            timeout=_DEFAULT_TIMEOUT,
            cloud_client_factory=_cloud_client_factory,
            embedded_client_factory=_embedded_client_factory,
        )
        retain_manager = RetainManager(
            client_manager=client_manager,
            get_session_id=lambda: "",
            get_parent_session_id=lambda: "",
            get_bank_id=lambda: "",
            get_retain_async=lambda: True,
            get_retain_context=lambda: "",
            get_retain_tags=list,
            get_observation_scopes=lambda: None,
            get_retain_source=lambda: "",
            get_retain_user_prefix=lambda: "User",
            get_retain_assistant_prefix=lambda: "Assistant",
            get_turn_index=lambda: 0,
            get_session_turns=list,
            get_metadata_attrs=list,
            get_event_timestamp=lambda: "",
            is_shutting_down=lambda: False,
        )
        recall_manager = RecallManager(
            client_manager=client_manager,
            get_bank_id=lambda: "",
            get_budget=lambda: "mid",
            get_recall_max_tokens=lambda: 4096,
            get_recall_tags=lambda: None,
            get_recall_tags_match=lambda: "any",
            get_recall_types=lambda: ["observation"],
            get_recall_prompt_preamble=lambda: "",
            get_prefetch_method=lambda: "recall",
            get_auto_recall=lambda: True,
            get_recall_sync=lambda: False,
            get_recall_max_input_chars=lambda: 800,
            get_memory_mode=lambda: "hybrid",
            get_recall_indicator=lambda: True,
            is_shutting_down=lambda: False,
            retain_manager=retain_manager,
            get_prefetch_waits_for_retain=lambda: True,
            get_prefetch_retain_drain_timeout=lambda: 10.0,
        )
        tool_handler = ToolHandler(
            client_manager=client_manager,
            retain_manager=retain_manager,
            recall_manager=recall_manager,
            get_mode=lambda: "cloud",
            get_bank_id=lambda: "",
            get_api_key=lambda: "",
            get_api_url=lambda: "",
            get_config=dict,
        )
        health_checker = HealthChecker(
            client_manager=client_manager,
            get_mode=lambda: "cloud",
            get_bank_id=lambda: "",
            get_client=lambda: None,
            get_config=dict,
        )
        session_manager = SessionManager(
            client_manager=client_manager,
            retain_manager=retain_manager,
            recall_manager=recall_manager,
            tool_handler=tool_handler,
            health_checker=health_checker,
        )

        # Store references for delegation
        self._session_manager = session_manager
        self._client_manager = client_manager
        self._retain_manager = retain_manager
        self._recall_manager = recall_manager
        self._tool_handler = tool_handler
        self._health_checker = health_checker

        # Expose required attributes for MemoryProvider interface
        self._config = None
        self._api_key = ""
        self._client = None
        self._warning_callback: Callable[[str], None] | None = None
        self._platform: str = "cli"
        self._shutting_down = threading.Event()

    @property
    def name(self) -> str:
        return "hindsight"

    @property
    def _mode(self) -> str:
        return self._session_manager._mode

    @_mode.setter
    def _mode(self, value: str) -> None:
        self._session_manager._mode = value

    def __getattr__(self, name: str) -> Any:
        """Delegate to session manager."""
        return getattr(self._session_manager, name)

    # MemoryProvider interface methods
    def backup_paths(self) -> list[str]:
        return self._session_manager.backup_paths()

    def is_available(self) -> bool:
        return self._session_manager.is_available()

    def unavailable_reason(self) -> str:
        return self._session_manager.unavailable_reason()

    def health_check(self) -> dict:
        return self._session_manager.health_check()

    def save_config(self, values: dict, hermes_home: str) -> None:
        self._session_manager.save_config(values, hermes_home)

    def post_setup(self, hermes_home: str, config: dict) -> None:
        self._session_manager.post_setup(hermes_home, config)

    def get_config_schema(self):
        return self._session_manager.get_config_schema()

    def initialize(self, session_id: str, **kwargs) -> None:
        self._session_manager.initialize(session_id, **kwargs)
        # Sync commonly accessed attributes
        self._config = self._session_manager._config
        self._api_key = self._client_manager.api_key
        self._client = self._client_manager.client
        self._warning_callback = self._session_manager._warning_callback
        self._platform = self._session_manager._platform

    def system_prompt_block(self) -> str:
        return self._session_manager.system_prompt_block()

    def sync_turn(self, user_content: str, assistant_content: str, *, session_id: str = "") -> None:
        self._session_manager.sync_turn(user_content, assistant_content, session_id=session_id)

    def get_tool_schemas(self) -> list[dict[str, Any]]:
        return self._session_manager.get_tool_schemas()

    def handle_tool_call(self, tool_name: str, args: dict, **kwargs) -> str:
        return self._session_manager.handle_tool_call(tool_name, args, **kwargs)

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        return self._session_manager.prefetch(query, session_id=session_id)

    def recall_status(self) -> RecallStatus | None:
        return self._session_manager.recall_status()

    def queue_prefetch(self, query: str, *, session_id: str = "") -> None:
        self._session_manager.queue_prefetch(query, session_id=session_id)

    def on_session_switch(
        self, new_session_id: str, *, parent_session_id: str = "", reset: bool = False, **kwargs
    ) -> None:
        self._session_manager.on_session_switch(new_session_id, parent_session_id=parent_session_id, reset=reset, **kwargs)

    def shutdown(self) -> None:
        self._session_manager.shutdown()

    # Implementation methods for client creation (used by client manager factories)
    def _new_cloud_client_impl(self):
        return self._client_manager._new_cloud_client()

    def _new_embedded_client_impl(self):
        return self._client_manager._new_embedded_client()

    # Backward compatibility for tests
    def _new_cloud_client(self):
        return self._new_cloud_client_impl()

    def _new_embedded_client(self):
        return self._new_embedded_client_impl()


def register(ctx) -> None:
    """Register Hindsight as a memory provider plugin."""
    ctx.register_memory_provider(HindsightMemoryProvider())


# Module-global loop is intentionally NOT stopped in shutdown.
# Shared across every provider in the process; stopping it would strand
# siblings' aiohttp sessions ("Unclosed client session").