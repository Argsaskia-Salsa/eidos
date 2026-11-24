"""Hindsight plugin constants and pure config normalizers (no I/O, no origin imports)."""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import stat
import threading
import urllib.request
from pathlib import Path
from typing import Any

import hermes_constants
from agent.secret_scope import UnscopedSecretError, get_secret
from utils import read_json_or_empty

# Log under the plugin package's own logger name (loader-path independent).
logger = logging.getLogger(__name__.rpartition(".")[0])

_DEFAULT_API_URL = "https://api.hindsight.vectorize.io"
_DEFAULT_LOCAL_URL = "http://localhost:8888"
# Keep in sync with plugin.yaml and pyproject.toml. Raised to 0.10.1 with the embed
# floor (see pyproject.toml): _maybe_upgrade_client() then pulls the 0.10.0 cohort's
# client forward on session start instead of waiting for a `hermes update`.
_MIN_CLIENT_VERSION = "0.10.1"
_DEFAULT_TIMEOUT = 120  # seconds — cloud API can take 30-40s per request
_DEFAULT_IDLE_TIMEOUT = 300  # seconds — Hindsight embedded daemon default
# ``metadata.source`` on retained memories is OPT-IN (AGENTS.md forbids
# on-by-default attribution tags): ``retain_source`` / HINDSIGHT_RETAIN_SOURCE.
_DEFAULT_RETAIN_SOURCE = ""
# Hindsight brand mark (eye ringed by graph nodes) for the recall/retain indicators.
_HINDSIGHT_GLYPH = "👁️"
# Hindsight 0.5.0 added ``update_mode='append'``; older APIs would silently
# overwrite prior turns under a stable document_id, so they keep the per-process id.
# Mirrors hindsight-integrations/openclaw — Hindsight 0.5.0 added `update_mode='append'` semantics on retain
# (vectorize-io/hindsight#932).
_MIN_VERSION_FOR_UPDATE_MODE_APPEND = "0.5.0"
_VALID_BUDGETS = {"low", "mid", "high"}
_PROVIDER_DEFAULT_MODELS = {
    "openai": "gpt-4o-mini",
    "anthropic": "claude-haiku-4-5",
    "gemini": "gemini-3.6-flash",
    "groq": "openai/gpt-oss-120b",
    "openrouter": "qwen/qwen3.5-9b",
    "minimax": "MiniMax-M2.7",
    "ollama": "gemma3:12b",
    "lmstudio": "local-model",
    "openai_compatible": "your-model-name",
}
# The embedded daemon speaks OpenAI wire format for these providers.
_OPENAI_WIRE_PROVIDERS = {"openai_compatible", "openrouter"}
_OBSERVATION_SCOPE_KEYWORDS = {"per_tag", "combined", "all_combinations"}

_LOCAL_MODES = {"local", "local_embedded"}
_RETAIN_CONTEXT_DEFAULT = "conversation between Hermes Agent and the User"

_SYSTEM_PROMPT_TAILS = {
    "context": "Relevant memories are automatically injected into context.",
    "tools": "Use hindsight_recall to search, hindsight_reflect for synthesis, hindsight_retain to store facts.",
    "hybrid": (
        "Relevant memories are automatically injected into context. "
        "Use hindsight_recall to search, hindsight_reflect for synthesis, "
        "hindsight_retain to store facts."
    ),
}

# update_mode='append' capability (Hindsight >= 0.5.0), cached per (API URL, key fingerprint)
# per process so every provider on the same API+key shares one /version round trip.
_append_capability_cache: dict[tuple[str, str | None], bool] = {}
_append_capability_lock = threading.Lock()

# Read by hindsight_embed.daemon_embed_manager AT IMPORT TIME: how long to wait
# for a slow /health before killing the daemon as stale.
_PORT_HEALTH_GRACE_ENV = "HINDSIGHT_EMBED_PORT_HEALTH_GRACE_TIMEOUT"

# Stale embedded-daemon connection markers (client recreated, operation retried once).
_RETRIABLE_CONNECTION_MARKERS = (
    "cannot connect to host",
    "connection refused",
    "connect call failed",
    "clientconnectorerror",
)


def _cloud_api_key(config: dict) -> str:
    return config.get("apiKey") or config.get("api_key") or get_secret("HINDSIGHT_API_KEY", "")


def _parse_int_setting(value: Any, default: int) -> int:
    """Parse an integer config/env value, falling back on invalid input."""
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        logger.warning("Invalid integer Hindsight setting %r; using default %s", value, default)
        return default


def _daemon_llm_provider(provider: str) -> str:
    return "openai" if provider in _OPENAI_WIRE_PROVIDERS else provider


def _normalize_retain_tags(value: Any) -> list[str]:
    """Normalize tag config/tool values to a deduplicated list of strings."""
    if value is None:
        return []
    raw_items = value if isinstance(value, list) else [value]
    if isinstance(value, str):
        text = value.strip()
        parsed = None
        if text.startswith("["):
            with contextlib.suppress(Exception):
                parsed = json.loads(text)
        raw_items = parsed if isinstance(parsed, list) else text.split(",")
    normalized: list[str] = []
    for item in raw_items:
        tag = str(item).strip()
        if tag and tag not in normalized:
            normalized.append(tag)
    return normalized


def _normalize_observation_scopes(value: Any) -> Any:
    """Normalize observation_scopes to a keyword string, ``list[list[str]]`` (one inner
    list per consolidation pass), or ``None`` (Hindsight's ``combined`` default).
    Accepts a keyword, a JSON-encoded list, a flat tag list (one scope) or a list of
    tag-lists; anything unrecognized -> ``None`` so we never send an invalid payload."""
    if isinstance(value, str):
        text = value.strip()
        if text in _OBSERVATION_SCOPE_KEYWORDS:
            return text
        if text.startswith("["):
            try:
                return _normalize_observation_scopes(json.loads(text))
            except Exception:
                return None
        return None
    if not isinstance(value, (list, tuple)):
        return None
    if all(isinstance(entry, str) for entry in value):  # flat tag list -> one scope
        value = [value]
    scopes = [
        [str(tag).strip() for tag in entry if str(tag).strip()]
        if isinstance(entry, (list, tuple))
        else [entry.strip()]
        if isinstance(entry, str) and entry.strip()
        else []
        for entry in value
    ]
    return [s for s in scopes if s] or None


def _sanitize_bank_segment(value: str) -> str:
    """URL/filesystem-safe bank_id placeholder: runs outside ``[A-Za-z0-9_-]`` (per
    ``str.isalnum``) become one dash; leading/trailing ``-``/``_`` are stripped."""
    # \w == str.isalnum() + "_" for str patterns, so this matches the per-char rule.
    return re.sub(r"[^\w-]+", "-", str(value)).strip("-_") if value else ""


def _resolve_bank_id_template(template: str, fallback: str, **placeholders: str) -> str:
    """Render a bank_id template ({profile}, {workspace}, {platform}, {user}, {session}),
    sanitizing each placeholder; the ``-``/``_`` runs empty placeholders leave are
    collapsed (``hermes-{user}`` -> ``hermes``). Empty/invalid template -> *fallback*."""
    if not template:
        return fallback
    try:
        rendered = template.format(**{k: _sanitize_bank_segment(v) for k, v in placeholders.items()})
    except (KeyError, IndexError) as exc:
        logger.warning("Invalid bank_id_template %r: %s — using fallback %r", template, exc, fallback)
        return fallback
    return re.sub(r"([-_])\1+", r"\1", rendered).strip("-_") or fallback


def _fetch_hindsight_api_version(api_url: str, api_key: str | None = None, timeout: float = 5.0) -> str | None:
    """GET ``<api_url>/version`` -> version string, or None on any failure (= legacy API)."""
    if not api_url:
        return None
    url = api_url.rstrip("/") + "/version"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {api_key}"} if api_key else {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
    except Exception as exc:
        logger.debug("Hindsight /version probe failed for %s: %s", url, exc)
        return None
    version = (data.get("version") or data.get("api_version")) if isinstance(data, dict) else None
    return str(version) if version else None


def _check_api_supports_update_mode_append(api_url: str, api_key: str | None = None) -> bool:
    """Cached ``update_mode='append'`` check for *api_url*. False on any probe failure
    (safe default: per-process document_id, no update_mode = resume-overwrite fix intact).

    Probes once per URL per process. See #6654.
    """
    if not api_url:
        return False
    try:
        from agent.credential_persistence import fingerprint_secret_value
    except ImportError:
        def fingerprint_secret_value(val: str | None) -> str:
            return val or ""
    cache_key = (api_url, fingerprint_secret_value(api_key))
    with _append_capability_lock:
        if cache_key in _append_capability_cache:
            return _append_capability_cache[cache_key]
    version = _fetch_hindsight_api_version(api_url, api_key)
    try:  # missing/invalid version -> unsupported
        from packaging.version import Version

        supported = bool(version) and Version(version) >= Version(_MIN_VERSION_FOR_UPDATE_MODE_APPEND)
    except Exception:
        supported = False
    with _append_capability_lock:
        # A concurrent probe may have filled the cache meanwhile; its answer wins.
        supported = _append_capability_cache.setdefault(cache_key, supported)
    if supported:
        logger.debug("Hindsight API %s version %s supports update_mode='append'", api_url, version)
    else:
        logger.warning(
            "Hindsight API at %s reports version %r, older than %s. "
            "Falling back to per-process document_id — retains across "
            "processes/sessions create separate documents instead of "
            "appending to a session-scoped one. Upgrade Hindsight to "
            "%s+ to enable update_mode='append' deduplication.",
            api_url,
            version,
            _MIN_VERSION_FOR_UPDATE_MODE_APPEND,
            _MIN_VERSION_FOR_UPDATE_MODE_APPEND,
        )
    return supported


def _export_port_health_grace_timeout(config: dict[str, Any]) -> None:
    """Export the daemon health grace timeout BEFORE ``daemon_embed_manager`` is
    imported. Only when configured; ``setdefault`` so an explicit env override wins."""
    raw = config.get("port_health_grace_timeout")
    if raw is None or raw == "":
        return
    try:
        seconds = float(raw)
    except (TypeError, ValueError):
        return logger.warning("Invalid Hindsight port_health_grace_timeout %r; ignoring.", raw)
    if seconds < 0:
        return logger.warning("Negative Hindsight port_health_grace_timeout %r; ignoring.", raw)
    os.environ.setdefault(_PORT_HEALTH_GRACE_ENV, repr(seconds))


def _load_simple_env(path) -> dict[str, str]:
    """Parse a KEY=VALUE env file (comments/blank lines ignored). utf-8-sig: also used
    on the Hermes .env during post_setup, where a Notepad BOM would stick to the first key."""
    if not path.exists():
        return {}
    pairs = (
        line.split("=", 1)
        for line in path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
        if line and not line.startswith("#") and "=" in line
    )
    return {key.strip(): value.strip() for key, value in pairs}


def _embedded_profile_env_path(config: dict[str, Any]) -> Path:
    profile = str(config.get("profile", "hermes") or "hermes")
    return Path.home() / ".hindsight" / "profiles" / f"{profile}.env"


def _on_disk_llm_api_key(config: dict[str, Any]) -> str:
    """The key currently persisted in the profile env file ("" when absent)."""
    with contextlib.suppress(Exception):
        return _load_simple_env(_embedded_profile_env_path(config)).get("HINDSIGHT_API_LLM_API_KEY", "") or ""
    return ""


def _embedded_llm_api_key(config: dict[str, Any]) -> str:
    """Resolve the LLM API key: explicit config first, then the profile secret
    scope, then the on-disk profile env as a last resort."""
    if config.get("llmApiKey") or config.get("llm_api_key"):
        return config.get("llmApiKey") or config.get("llm_api_key")
    try:
        scoped = get_secret("HINDSIGHT_API_LLM_API_KEY", "") or get_secret("HINDSIGHT_LLM_API_KEY", "")
    except UnscopedSecretError:
        scoped = ""
    if scoped:
        return scoped
    return _on_disk_llm_api_key(config)


def _build_embedded_profile_env(config: dict[str, Any], *, llm_api_key: str | None = None) -> dict[str, str]:
    """Build the profile-scoped env that standalone hindsight-embed consumes."""
    if llm_api_key is None:
        llm_api_key = _embedded_llm_api_key(config)
    env_values = {
        "HINDSIGHT_API_LLM_PROVIDER": str(_daemon_llm_provider(config.get("llm_provider", ""))),
        "HINDSIGHT_API_LLM_API_KEY": str(llm_api_key or ""),
        "HINDSIGHT_API_LLM_MODEL": str(config.get("llm_model", "")),
        "HINDSIGHT_API_LOG_LEVEL": "info",
    }
    base_url = config.get("llm_base_url")
    if not base_url:
        try:
            base_url = get_secret("HINDSIGHT_API_LLM_BASE_URL", "") or ""
        except UnscopedSecretError:
            base_url = ""
    if base_url:
        env_values["HINDSIGHT_API_LLM_BASE_URL"] = str(base_url)
    if (idle_timeout := config.get("idle_timeout")) is None:
        idle_timeout = os.environ.get("HINDSIGHT_IDLE_TIMEOUT")
    if idle_timeout is not None and idle_timeout != "":
        env_values["HINDSIGHT_EMBED_DAEMON_IDLE_TIMEOUT"] = str(_parse_int_setting(idle_timeout, _DEFAULT_IDLE_TIMEOUT))
    return env_values


def _may_rewrite_profile_env(config: dict[str, Any]) -> bool:
    """Whether rewriting the profile env file is safe right now."""
    if _build_embedded_profile_env(config).get("HINDSIGHT_API_LLM_API_KEY"):
        return True
    return not _on_disk_llm_api_key(config)


def _secure_write_profile_env(profile_env: Path, content: str) -> None:
    """Create/overwrite *profile_env* owner-only (0600); a pre-existing file is
    tightened BEFORE the plaintext LLM API key is written."""
    if profile_env.exists():
        with contextlib.suppress(OSError):
            os.chmod(profile_env, 0o600)
    fd = os.open(str(profile_env), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(content)


def _validate_profile_env_permissions(profile_env: Path) -> None:
    """Post-write check: owner-only on POSIX (Windows ACLs aren't mode bits; skipped)."""
    if os.name != "posix":
        return
    if stat.S_IMODE(profile_env.stat().st_mode) != 0o600:
        with contextlib.suppress(OSError):
            os.chmod(profile_env, 0o600)
        if stat.S_IMODE(profile_env.stat().st_mode) != 0o600:
            raise PermissionError(f"Embedded Hindsight profile environment is not owner-only: {profile_env}")


def _materialize_embedded_profile_env(config: dict[str, Any], *, llm_api_key: str | None = None) -> Path:
    """Write the profile env file; never leave a plaintext key in a file whose
    permissions could not be verified."""
    profile_env = _embedded_profile_env_path(config)
    profile_env.parent.mkdir(parents=True, exist_ok=True)
    env_values = _build_embedded_profile_env(config, llm_api_key=llm_api_key)
    content = "".join(f"{key}={value}\n" for key, value in env_values.items())
    try:
        _secure_write_profile_env(profile_env, content)
        _validate_profile_env_permissions(profile_env)
    except BaseException:
        with contextlib.suppress(OSError):
            profile_env.unlink()
        raise
    return profile_env


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


def _validate_config(cfg: dict, source: str) -> dict:
    """Validate config dict, warn on unknown keys, and ensure required fields have valid defaults."""
    if not isinstance(cfg, dict):
        logger.warning("Hindsight config from %s is not a dict, using defaults", source)
        return {}
    unknown = set(cfg.keys()) - _KNOWN_CONFIG_KEYS
    if unknown:
        logger.warning("Hindsight config from %s contains unknown keys: %s", source, sorted(unknown))
    mode = cfg.get("mode", "cloud")
    if mode not in ("cloud", "local_embedded", "local_external", "local"):
        logger.warning("Hindsight config from %s has invalid mode %r, defaulting to 'cloud'", source, mode)
        cfg["mode"] = "cloud"
    budget = cfg.get("recall_budget") or cfg.get("budget", "mid")
    if budget not in _VALID_BUDGETS:
        logger.warning("Hindsight config from %s has invalid budget %r, defaulting to 'mid'", source, budget)
        cfg["recall_budget"] = "mid"
    memory_mode = cfg.get("memory_mode", "hybrid")
    if memory_mode not in _SYSTEM_PROMPT_TAILS:
        logger.warning("Hindsight config from %s has invalid memory_mode %r, defaulting to 'hybrid'", source, memory_mode)
        cfg["memory_mode"] = "hybrid"
    prefetch_method = cfg.get("recall_prefetch_method") or cfg.get("prefetch_method", "recall")
    if prefetch_method not in {"recall", "reflect"}:
        logger.warning("Hindsight config from %s has invalid prefetch_method %r, defaulting to 'recall'", source, prefetch_method)
        cfg["recall_prefetch_method"] = "recall"
    return cfg


def _load_config() -> dict:
    """$HERMES_HOME/hindsight/config.json (profile-scoped), else ~/.hindsight/config.json
    (legacy, shared), else environment variables."""
    for path in (hermes_constants.get_hermes_home() / "hindsight" / "config.json", Path.home() / ".hindsight" / "config.json"):
        if path.exists() and (data := read_json_or_empty(path)):
            return _validate_config(data, str(path))
    return {
        "mode": get_secret("HINDSIGHT_MODE", "") or "cloud",
        "apiKey": get_secret("HINDSIGHT_API_KEY", ""),
        "timeout": _parse_int_setting(os.environ.get("HINDSIGHT_TIMEOUT"), _DEFAULT_TIMEOUT),
        "idle_timeout": _parse_int_setting(os.environ.get("HINDSIGHT_IDLE_TIMEOUT"), _DEFAULT_IDLE_TIMEOUT),
        "retain_tags": get_secret("HINDSIGHT_RETAIN_TAGS", "") or "",
        "observation_scopes": get_secret("HINDSIGHT_RETAIN_OBSERVATION_SCOPES", "") or "",
        "retain_source": _scoped_setting("HINDSIGHT_RETAIN_SOURCE", _DEFAULT_RETAIN_SOURCE),
        "retain_user_prefix": _scoped_setting("HINDSIGHT_RETAIN_USER_PREFIX", "User"),
        "retain_assistant_prefix": _scoped_setting("HINDSIGHT_RETAIN_ASSISTANT_PREFIX", "Assistant"),
        "banks": {
            "hermes": {
                "bankId": get_secret("HINDSIGHT_BANK_ID", "") or "hermes",
                "budget": os.environ.get("HINDSIGHT_BUDGET", "mid"),
                "enabled": True,
            }
        },
    }


def _scoped_setting(name: str, default: str = "") -> str:
    """Profile-scoped read of a retain SHAPING value, with the provider's own default on a miss."""
    try:
        value = get_secret(name, default)
    except UnscopedSecretError:
        return default
    return default if value is None else value


def _int_setting(key: str, env_var: str, default: int) -> int:
    """Config value if set (explicit 0 preserved), else env var, else default."""
    # This is a placeholder - actual implementation is in the provider
    return _parse_int_setting(os.environ.get(env_var), default)


def _maybe_upgrade_client() -> None:
    """Auto-upgrade an outdated hindsight-client via the environment-aware lazy_deps installer."""
    try:
        from importlib.metadata import version as pkg_version

        from packaging.version import Version

        installed = pkg_version("hindsight-client")
        if Version(installed) < Version(_MIN_CLIENT_VERSION):
            logger.warning("hindsight-client %s is outdated (need >=%s), attempting upgrade...", installed, _MIN_CLIENT_VERSION)
            from tools.lazy_deps import install_specs

            outcome = install_specs([f"hindsight-client>={_MIN_CLIENT_VERSION}"], timeout=120)
            if outcome.ok:
                logger.info("hindsight-client upgraded to >=%s", _MIN_CLIENT_VERSION)
            elif outcome.blocked:
                logger.warning(
                    "Auto-upgrade unavailable: %s. Run: uv pip install 'hindsight-client>=%s'",
                    outcome.reason,
                    _MIN_CLIENT_VERSION,
                )
            else:
                logger.warning(
                    "Auto-upgrade failed: %s. Run: uv pip install 'hindsight-client>=%s'",
                    (outcome.stderr or "").strip() or "install error",
                    _MIN_CLIENT_VERSION,
                )
    except Exception:
        pass


def _mint_document_id(session_id: str) -> str:
    """Per-process document id: reusing session_id alone overwrote the document on /resume."""
    return f"{session_id}-{__import__('datetime').datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"