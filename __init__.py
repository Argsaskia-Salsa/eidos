"""Body Bridge Plugin — forwards Hermes events to reflexd via MCP.

Hooks:
- post_gateway_admission → user_message (after auth/admission, inside session slot)
- post_llm_call → user_response (after LLM completes its turn)

Middleware:
- LLM_EXECUTION_MIDDLEWARE → thinking_start / thinking_end (at exact LLM generation boundaries)

Posts to reflexd MCP (http://localhost:8770/mcp) for reflexd consumption.

Configuration:
- REFLEXD_MCP_URL environment variable (default: http://localhost:8770/mcp)
- REFLEXD_TIMEOUT_SECONDS environment variable (default: 1.0)
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import threading
from typing import Any

import httpx

logger = logging.getLogger("hermes.plugins.body_bridge")
logger.info("Body Bridge plugin module loaded")

DEFAULT_MCP_URL = "http://localhost:8770/mcp"
DEFAULT_TIMEOUT = 1.0

_REFLEXD_MCP_URL = os.getenv("REFLEXD_MCP_URL", DEFAULT_MCP_URL)
_REFLEXD_TIMEOUT = float(os.getenv("REFLEXD_TIMEOUT_SECONDS", str(DEFAULT_TIMEOUT)))

_LOCAL_PLATFORMS: frozenset[str] = frozenset({"local", "desktop", "cli"})


def _is_local_platform(platform: str | None) -> bool:
    """Check if platform should be skipped (local development platforms)."""
    if platform is None:
        return False
    return platform.lower() in _LOCAL_PLATFORMS


def _build_payload(
    event_type: str,
    text: str | None,
    platform: str,
    chat_id: str,
    user_id: str,
) -> dict[str, Any]:
    """Build the payload for reflexd inject_event."""
    return {
        "name": event_type,
        "data": {
            "text": text or "",
            "platform": platform or "unknown",
            "chat_id": chat_id,
            "user_id": user_id,
        },
        "type": "inject",
    }


def _extract_chat_user_from_history(
    conversation_history: list[dict[str, Any]] | None,
    session_id: str | None = None,
) -> tuple[str, str]:
    """Extract chat_id and user_id from the last user message in history."""
    chat_id = "unknown"
    user_id = "unknown"

    for msg in reversed(conversation_history or []):
        if not isinstance(msg, dict):
            continue
        if msg.get("role") != "user":
            continue
        meta = msg.get("metadata") if isinstance(msg.get("metadata"), dict) else {}
        chat_id = str(msg.get("chat_id") or meta.get("chat_id") or session_id or "unknown")
        user_id = str(msg.get("user_id") or meta.get("user_id") or "unknown")
        break

    return chat_id, user_id


class EventDispatcher:
    """Encapsulates the worker loop and event posting to reflexd."""

    def __init__(
        self,
        mcp_url: str = _REFLEXD_MCP_URL,
        timeout: float = _REFLEXD_TIMEOUT,
    ) -> None:
        self._mcp_url = mcp_url
        self._timeout = timeout
        self._worker_loop: asyncio.AbstractEventLoop | None = None
        self._worker_thread: threading.Thread | None = None
        self._worker_ready = threading.Event()
        self._worker_lock = threading.Lock()
        self._pending_futures: set[asyncio.Future[Any]] = set()
        self._pending_lock = threading.Lock()
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(connect=0.5, read=1.0, write=1.0, pool=5.0),
                limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
            )
        return self._client

    def _ensure_worker(self) -> asyncio.AbstractEventLoop | None:
        """Start (or restart) the worker loop. Returns the loop, or None on failure."""
        if self._worker_loop is not None and self._worker_loop.is_running():
            return self._worker_loop

        with self._worker_lock:
            if self._worker_loop is not None and self._worker_loop.is_running():
                return self._worker_loop
            self._worker_ready.clear()

            def _run() -> None:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                self._worker_loop = loop
                self._worker_ready.set()
                try:
                    loop.run_forever()
                finally:
                    loop.close()

            self._worker_thread = threading.Thread(
                target=_run, daemon=True, name="body-bridge-worker"
            )
            self._worker_thread.start()
            if not self._worker_ready.wait(timeout=5.0):
                logger.warning("body_bridge: worker loop did not become ready")
                return None
            return self._worker_loop

    def _cleanup_future(self, fut: asyncio.Future[Any]) -> None:
        with self._pending_lock:
            self._pending_futures.discard(fut)

    def _close_client(self, loop: asyncio.AbstractEventLoop) -> None:
        """Close the httpx client on the worker loop."""
        if self._client is not None and not self._client.is_closed:
            loop.call_soon_threadsafe(lambda: asyncio.create_task(self._client.aclose()))
            self._client = None

    async def _post_to_reflexd(
        self,
        event_type: str,
        text: str | None,
        platform: str,
        chat_id: str,
        user_id: str,
    ) -> None:
        """Send a single event to reflexd MCP endpoint."""
        payload = _build_payload(event_type, text, platform, chat_id, user_id)

        try:
            client = await self._get_client()
            response = await client.post(
                self._mcp_url,
                json={
                    "jsonrpc": "2.0",
                    "method": "tools/call",
                    "params": {
                        "name": "inject_event",
                        "arguments": payload,
                    },
                    "id": 1,
                },
                timeout=self._timeout,
            )
            if response.status_code >= 400:
                logger.warning(
                    "body_bridge: POST %s -> HTTP %s",
                    event_type,
                    response.status_code,
                )
            else:
                logger.info("body_bridge: POST %s -> HTTP %s", event_type, response.status_code)
        except Exception as e:
            logger.warning("body_bridge: POST %s failed: %s", event_type, e)

    def schedule_post(
        self,
        event_type: str,
        text: str | None,
        platform: str,
        chat_id: str,
        user_id: str,
    ) -> None:
        """Schedule a fire-and-forget POST to reflexd MCP (thread-safe)."""
        logger.info(
            "body_bridge: schedule_post called: event_type=%s, platform=%s, chat_id=%s",
            event_type,
            platform,
            chat_id,
        )

        if _is_local_platform(platform):
            logger.info("body_bridge: skipping local platform: %s", (platform or "").lower())
            return

        loop = self._ensure_worker()
        if loop is None:
            logger.warning("body_bridge: worker loop unavailable, dropping %s event", event_type)
            return

        async def _post() -> None:
            await self._post_to_reflexd(event_type, text, platform, chat_id, user_id)

        try:
            fut = asyncio.run_coroutine_threadsafe(_post(), loop)
        except Exception as e:
            logger.warning("body_bridge: failed to schedule %s: %s", event_type, e)
            return

        with self._pending_lock:
            self._pending_futures.add(fut)
        fut.add_done_callback(self._cleanup_future)

    def shutdown(self) -> None:
        """Stop the worker loop and clean up pending futures."""
        loop = None
        with self._worker_lock:
            if self._worker_loop is not None and self._worker_loop.is_running():
                loop = self._worker_loop
                self._close_client(loop)
                loop.call_soon_threadsafe(loop.stop)
            self._worker_loop = None
            self._worker_thread = None
            self._worker_ready.clear()

        # Don't join daemon thread - it will exit when loop stops
        # Give the loop a moment to stop
        if loop is not None:
            import time

            time.sleep(0.05)

        with self._pending_lock:
            for fut in list(self._pending_futures):
                fut.cancel()
            self._pending_futures.clear()

    def __del__(self) -> None:
        """Best-effort cleanup on garbage collection."""
        with contextlib.suppress(Exception):
            self.shutdown()


_dispatcher = EventDispatcher()


def _schedule_post(
    event_type: str,
    text: str | None,
    platform: str,
    chat_id: str,
    user_id: str,
) -> None:
    """Backward-compatible wrapper for existing hook/middleware code."""
    _dispatcher.schedule_post(event_type, text, platform, chat_id, user_id)


async def _post_gateway_admission_hook(
    session_key: str,
    platform: str,
    source: dict[str, Any],
    message_id: str,
    text: str,
) -> tuple[bool, Any]:
    """post_gateway_admission: fires after auth+admission, inside session slot.

    Perfect point for user_message — message is validated and has a session.
    Returns (consumed, reply). consumed=False to let message continue.
    """
    text_preview = text[:50] if text else ""
    logger.info(
        "body_bridge: post_gateway_admission called: session_key=%s, platform=%s, text=%s",
        session_key,
        platform,
        text_preview,
    )
    if not text or not text.strip():
        logger.debug("body_bridge: post_gateway_admission empty text, skipping")
        return False, None

    src = source if isinstance(source, dict) else {}
    chat_id = str(src.get("chat_id") or "unknown")
    user_id = str(src.get("user_id") or "unknown")
    src_platform = src.get("platform") or platform or "unknown"

    _schedule_post(
        "user_message",
        text=text,
        platform=src_platform,
        chat_id=chat_id,
        user_id=user_id,
    )

    return False, None


async def _post_llm_call_hook(
    session_id: str,
    task_id: str,
    turn_id: str,
    user_message: str,
    assistant_response: str,
    conversation_history: list[dict[str, Any]],
    model: str,
    platform: str,
    side_question_token: Any = None,
) -> None:
    """post_llm_call: fires after LLM completes its turn, before response delivery.

    Returns None (observer hook). If your Hermes build expects a
    (consumed, reply) tuple here, change the return accordingly and
    verify against the framework version.
    """
    logger.info(
        "body_bridge: post_llm_call called: session_id=%s, platform=%s",
        session_id,
        platform,
    )
    if not assistant_response or not assistant_response.strip():
        logger.debug("body_bridge: post_llm_call empty response, skipping")
        return None

    chat_id, user_id = _extract_chat_user_from_history(conversation_history, session_id)

    _schedule_post(
        "user_response",
        text=assistant_response,
        platform=platform or "unknown",
        chat_id=chat_id,
        user_id=user_id,
    )
    return None


# ─── LLM Execution Middleware: thinking_start / thinking_end ───
# Signature per docs: def middleware(**kwargs) -> response
# MUST BE SYNC — _run_execution_chain invokes the callback without await.
def _llm_execution_middleware(**kwargs: Any) -> Any:
    """Middleware wrapper around LLM execution (SYNC).

    Fires thinking_start BEFORE calling next_call (the actual LLM),
    fires thinking_end AFTER next_call returns.
    """
    logger.info(
        "body_bridge: llm_execution middleware called: session_id=%s, platform=%s",
        kwargs.get("session_id"),
        kwargs.get("platform"),
    )
    request = kwargs.get("request", {})
    next_call = kwargs.get("next_call")
    if next_call is None:
        return request

    session_id = str(kwargs.get("session_id") or "")
    platform = str(kwargs.get("platform") or "")

    if _is_local_platform(platform):
        return next_call(request)

    chat_id = str(kwargs.get("chat_id") or session_id or "unknown")
    user_id = str(kwargs.get("user_id") or "unknown")

    logger.info("body_bridge: firing thinking_start")
    _schedule_post(
        "thinking_start",
        text="",
        platform=platform,
        chat_id=chat_id,
        user_id=user_id,
    )

    try:
        return next_call(request)
    finally:
        logger.info("body_bridge: firing thinking_end")
        _schedule_post(
            "thinking_end",
            text="",
            platform=platform,
            chat_id=chat_id,
            user_id=user_id,
        )


def register(ctx: Any) -> None:
    """Plugin entry point — called by Hermes on startup."""
    logger.info("Body Bridge plugin loaded: registering hooks and middleware")

    _dispatcher._ensure_worker()

    ctx.register_hook("post_gateway_admission", _post_gateway_admission_hook)
    ctx.register_hook("post_llm_call", _post_llm_call_hook)
    ctx.register_middleware("llm_execution", _llm_execution_middleware)

    logger.info(
        "Body Bridge hooks/middleware registered: "
        "post_gateway_admission, post_llm_call, llm_execution"
    )
