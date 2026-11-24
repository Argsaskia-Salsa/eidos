"""Health check for Hindsight provider."""

from __future__ import annotations

import logging
import time
from typing import Any

from .client import HindsightClientManager
from .embedded import _check_local_runtime, _local_runtime_hint
from .settings import _DEFAULT_API_URL, _DEFAULT_LOCAL_URL, _fetch_hindsight_api_version

logger = logging.getLogger(__name__)


class HealthChecker:
    """Performs health checks on Hindsight connections."""

    def __init__(
        self,
        client_manager: HindsightClientManager,
        get_mode: Any,
        get_bank_id: Any,
        get_client: Any,
        get_config: Any,
    ):
        self._client_manager = client_manager
        self._get_mode = get_mode
        self._get_bank_id = get_bank_id
        self._get_client = get_client
        self._get_config = get_config

    def health_check(self) -> dict[str, Any]:
        """Ping the Hindsight API/daemon and return structured health status."""
        start = time.monotonic()
        status = "unhealthy"
        error = None
        latency_ms = None
        details: dict[str, Any] = {}

        try:
            mode = self._get_mode()
            if mode == "local_embedded":
                available, reason = _check_local_runtime()
                if not available:
                    error = f"Local runtime unavailable: {reason}"
                    details["runtime_available"] = False
                    details["runtime_reason"] = reason
                else:
                    details["runtime_available"] = True
                    try:
                        client = self._get_client()
                        profile = self._get_config().get("profile", "hermes") if self._get_config() else "hermes"
                        if hasattr(client, "_manager") and hasattr(client._manager, "is_running"):
                            daemon_running = client._manager.is_running(profile)
                            details["daemon_running"] = daemon_running
                            if not daemon_running:
                                error = "Embedded daemon is not running"
                                status = "degraded"
                            else:
                                status = "healthy"
                        else:
                            status = "healthy"
                    except Exception as e:
                        error = f"Failed to check embedded daemon: {e}"
                        status = "degraded"
                        details["daemon_check_error"] = str(e)
            else:
                api_url = self._client_manager.api_url or (
                    _DEFAULT_LOCAL_URL if mode == "local_external" else _DEFAULT_API_URL
                )
                api_key = self._client_manager.api_key
                version = _fetch_hindsight_api_version(api_url, api_key, timeout=5.0)
                latency_ms = int((time.monotonic() - start) * 1000)
                details["api_url"] = api_url
                details["api_version"] = version
                if version:
                    status = "healthy"
                else:
                    error = "API /version endpoint unreachable or returned no version"
                    status = "degraded"
        except Exception as e:
            error = f"Health check failed: {e}"
            details["exception"] = str(e)

        if latency_ms is None:
            latency_ms = int((time.monotonic() - start) * 1000)

        return {
            "status": status,
            "mode": self._get_mode(),
            "api_url": self._client_manager.api_url or (
                _DEFAULT_LOCAL_URL if self._get_mode() == "local_external" else _DEFAULT_API_URL
            ),
            "bank_id": self._get_bank_id(),
            "latency_ms": latency_ms,
            "error": error,
            "details": details,
        }

    def is_available(self) -> bool:
        try:
            from agent.secret_scope import get_secret

            from . import _LOCAL_MODES, _cloud_api_key, _load_config

            cfg = _load_config()
            mode = cfg.get("mode", "cloud")
            if mode in _LOCAL_MODES:
                from .embedded import _ensure_local_runtime

                return _ensure_local_runtime()[0]
            result = mode == "local_external" or bool(
                _cloud_api_key(cfg) or cfg.get("api_url") or get_secret("HINDSIGHT_API_URL", "")
            )
            return result
        except Exception as e:
            logger.warning(f"Hindsight health check failed: {e}")
            return False

    def unavailable_reason(self) -> str:
        try:
            from . import _LOCAL_MODES, _load_config

            cfg = _load_config()
            if cfg.get("mode", "cloud") not in _LOCAL_MODES:
                return ""
        except Exception:
            return ""
        available, reason = _check_local_runtime()
        return "" if available else _local_runtime_hint(reason).strip()