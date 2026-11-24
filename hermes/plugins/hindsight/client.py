"""Hindsight client management: creation, caching, and operation execution with retry logic."""

from __future__ import annotations

import contextlib
import logging
import time
from collections.abc import Callable
from typing import Any

from .embedded import _RETRIABLE_CONNECTION_MARKERS
from .settings import (
    _DEFAULT_API_URL,
    _DEFAULT_LOCAL_URL,
    _DEFAULT_TIMEOUT,
    _daemon_llm_provider,
    _embedded_llm_api_key,
    _export_port_health_grace_timeout,
)

logger = logging.getLogger(__name__)


class HindsightClientManager:
    """Manages Hindsight client lifecycle and operation execution."""

    def __init__(
        self,
        mode: str,
        api_url: str,
        api_key: str,
        llm_base_url: str,
        config: dict,
        timeout: int,
        cloud_client_factory: Callable[[], Any] | None = None,
        embedded_client_factory: Callable[[], Any] | None = None,
    ):
        self._mode = mode
        self._api_url = api_url
        self._api_key = api_key
        self._llm_base_url = llm_base_url
        self._config = config
        self._timeout = timeout
        self._client: Any = None
        self._cloud_client_factory = cloud_client_factory
        self._embedded_client_factory = embedded_client_factory

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def api_url(self) -> str:
        return self._api_url

    @property
    def api_key(self) -> str:
        return self._api_key

    @property
    def client(self) -> Any:
        return self._client

    def get_client(self) -> Any:
        """Return cached client or create new one."""
        if self._client is None:
            self._client = self._create_client()
        return self._client

    def _create_client(self) -> Any:
        """Create appropriate client based on mode."""
        if self._mode == "local_embedded":
            if self._embedded_client_factory:
                return self._embedded_client_factory()
            return self._new_embedded_client()
        if self._cloud_client_factory:
            return self._cloud_client_factory()
        return self._new_cloud_client()

    def _new_embedded_client(self) -> Any:
        from hindsight import HindsightEmbedded

        HindsightEmbedded.__del__ = lambda self: None
        cfg = self._config
        llm_provider = _daemon_llm_provider(cfg.get("llm_provider", ""))
        logger.debug(
            "Creating HindsightEmbedded client (profile=%s, provider=%s)", cfg.get("profile", "hermes"), llm_provider
        )
        kwargs = dict(
            profile=cfg.get("profile", "hermes"),
            llm_provider=llm_provider,
            llm_api_key=_embedded_llm_api_key(cfg),
            llm_model=cfg.get("llm_model", ""),
            idle_timeout=cfg.get("idle_timeout", _DEFAULT_IDLE_TIMEOUT),
        )
        if self._llm_base_url:
            kwargs["llm_base_url"] = self._llm_base_url
        return HindsightEmbedded(**kwargs)

    def _new_cloud_client(self) -> Any:
        from hindsight_client import Hindsight

        kwargs = {"base_url": self._api_url, "timeout": float(self._timeout or _DEFAULT_TIMEOUT)}
        if self._api_key:
            kwargs["api_key"] = self._api_key
        logger.debug(
            "Creating Hindsight cloud client (url=%s, has_key=%s, timeout=%s)",
            self._api_url,
            bool(self._api_key),
            kwargs["timeout"],
        )
        return Hindsight(**kwargs)

    def run_operation(self, operation: Callable, operation_name: str = "operation") -> Any:
        """Run an async client operation with timing and retry for local_embedded."""
        start = time.monotonic()
        try:
            result = self._run_sync(operation(self.get_client()))
            latency_ms = int((time.monotonic() - start) * 1000)
            logger.debug(
                "Hindsight %s completed (bank=%s, mode=%s, latency_ms=%d)",
                operation_name,
                self._config.get("bank_id", "unknown"),
                self._mode,
                latency_ms,
            )
            return result
        except Exception as exc:
            latency_ms = int((time.monotonic() - start) * 1000)
            text = f"{type(exc).__name__}: {exc}".lower()
            if self._mode != "local_embedded" or not any(m in text for m in _RETRIABLE_CONNECTION_MARKERS):
                logger.warning(
                    "Hindsight %s failed (bank=%s, mode=%s, latency_ms=%d): %s",
                    operation_name,
                    self._config.get("bank_id", "unknown"),
                    self._mode,
                    latency_ms,
                    exc,
                )
                raise
            logger.info(
                "Hindsight embedded daemon appears unreachable; recreating client and retrying once: %s",
                exc,
            )
            self._client = None
            self._client = self.get_client()
            return self._run_sync(operation(self._client))

    def _run_sync(self, coro) -> Any:
        from hindsight_hermes import _run_sync as global_run_sync
        return global_run_sync(coro, timeout=self._timeout)

    def close(self) -> None:
        """Close the client and clear cache."""
        if self._client is None:
            return
        if self._mode != "local_embedded":
            self._run_sync(self._client.aclose())
            self._client = None
            return
        inner_client = getattr(self._client, "_client", None)
        if inner_client is not None and hasattr(inner_client, "aclose"):
            self._run_sync(inner_client.aclose())
            with contextlib.suppress(Exception):
                self._client._client = None
        with contextlib.suppress(RuntimeError):
            self._client.close()
        self._client = None

    def check_update_mode_append(self, session_id: str) -> tuple[str, str | None]:
        """Resolve document_id and update_mode based on API capability."""
        url = getattr(self._client, "url", None) if self._mode == "local_embedded" else None
        probe_url = str(url) if url else (self._api_url or "")
        from hindsight_hermes import (
            _check_api_supports_update_mode_append,
            _mint_document_id,
        )
        if session_id and _check_api_supports_update_mode_append(probe_url, self._api_key):
            return session_id, "append"
        return _mint_document_id(session_id), None

    @staticmethod
    def get_default_url(mode: str) -> str:
        return _DEFAULT_LOCAL_URL if mode in {"local_embedded", "local_external"} else _DEFAULT_API_URL

    def prepare_embedded_start(self) -> None:
        """Export port health grace timeout before starting embedded daemon."""
        if self._mode == "local_embedded":
            _export_port_health_grace_timeout(self._config)