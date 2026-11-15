"""Body Bridge Plugin — forwards Hermes events to reflexd via MCP.

Hooks:
- post_gateway_admission → user_message (after auth/admission, inside session slot)
- post_llm_call → user_response (after LLM completes its turn)
- gateway_platform_event → platform_event (Telegram reactions, edits)
- on_stream_start → stream_start (streaming generation begins)
- on_stream_delta → stream_delta (streaming text delta, throttled)
- on_stream_end → stream_end (streaming generation ends)
- pre_gateway_dispatch → filter/rewrite incoming events before auth
- on_session_end → session_end (per-turn session close)
- on_session_finalize → session_finalize (session boundary cleanup)
- api_request_error → api_error (LLM API failures)

Middleware:
- llm_execution → thinking_start / thinking_end (at exact LLM generation boundaries)

Posts to reflexd MCP (http://localhost:8770/mcp) for reflexd consumption.

Configuration:
- REFLEXD_MCP_URL environment variable (default: http://localhost:8770/mcp)
- REFLEXD_TIMEOUT_SECONDS environment variable (default: 1.0)
- REFLEXD_MAX_PENDING environment variable (default: 256)
- REFLEXD_SKIP_PLATFORMS environment variable (default: local,desktop,cli)
- REFLEXD_BREAKER_THRESHOLD environment variable (default: 5)
- REFLEXD_BREAKER_COOLDOWN environment variable (default: 10)
- REFLEXD_INJECT_TOOL environment variable (default: inject_event)
- REFLEXD_STREAM_TTL_SECONDS environment variable (default: 300)
"""

from __future__ import annotations

import asyncio
import itertools
import logging
import os
import threading
import time
from collections import defaultdict
from concurrent.futures import Future as ConcurrentFuture
from typing import Any

import httpx

logger = logging.getLogger("hermes.plugins.body_bridge")
logger.info("Body Bridge plugin module loaded")

DEFAULT_MCP_URL = "http://localhost:8770/mcp"
DEFAULT_TIMEOUT = 1.0
DEFAULT_MAX_PENDING = 256
DEFAULT_SKIP_PLATFORMS = "local,desktop,cli"
DEFAULT_BREAKER_THRESHOLD = 5
DEFAULT_BREAKER_COOLDOWN = 10
DEFAULT_INJECT_TOOL = "inject_event"
DEFAULT_STREAM_TTL = 300

_REFLEXD_MCP_URL = os.getenv("REFLEXD_MCP_URL", DEFAULT_MCP_URL)


def _env_float(name: str, default: float) -> float:
    """Parse float from env with warning on invalid value."""
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        logger.warning("body_bridge: invalid %s=%r, using default %s", name, raw, default)
        return default


def _env_int(name: str, default: int) -> int:
    """Parse int from env with warning on invalid value."""
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning("body_bridge: invalid %s=%r, using default %s", name, raw, default)
        return default


def _env_str(name: str, default: str) -> str:
    """Get string from env with default."""
    return os.getenv(name, default)


_REFLEXD_TIMEOUT = _env_float("REFLEXD_TIMEOUT_SECONDS", DEFAULT_TIMEOUT)
_REFLEXD_MAX_PENDING = _env_int("REFLEXD_MAX_PENDING", DEFAULT_MAX_PENDING)
_REFLEXD_BREAKER_THRESHOLD = _env_int(
    "REFLEXD_BREAKER_THRESHOLD", DEFAULT_BREAKER_THRESHOLD
)
_REFLEXD_BREAKER_COOLDOWN = _env_float(
    "REFLEXD_BREAKER_COOLDOWN", DEFAULT_BREAKER_COOLDOWN
)
_REFLEXD_INJECT_TOOL = _env_str("REFLEXD_INJECT_TOOL", DEFAULT_INJECT_TOOL)
_REFLEXD_STREAM_TTL = _env_float(
    "REFLEXD_STREAM_TTL_SECONDS", DEFAULT_STREAM_TTL
)

_raw_skip = _env_str("REFLEXD_SKIP_PLATFORMS", DEFAULT_SKIP_PLATFORMS)
_SKIP_PLATFORMS: frozenset[str] = frozenset(
    p.strip().lower() for p in _raw_skip.split(",") if p.strip()
)

_SEQ = itertools.count(1)
_RPC_ID = itertools.count(1)

_stream_buffer: dict[str, list[str]] = defaultdict(list)
_stream_timers: dict[str, asyncio.Task] = {}
_stream_lengths: dict[str, int] = {}
_STAYM_FLUSH_CHARS = 80
_STAYM_FLUSH_MS = 100

_stream_ttl_task: asyncio.Task | None = None

_metrics: dict[str, int] = defaultdict(int)
_metrics_lock = threading.Lock()
_last_metrics_log = 0.0


def _is_local_platform(platform: str | None) -> bool:
    """Check if platform should be skipped (local development platforms)."""
    if platform is None:
        return False
    return platform in _SKIP_PLATFORMS


def _build_payload(
    event_type: str,
    text: str | None,
    platform: str,
    chat_id: str,
    user_id: str,
    data: dict[str, Any] | None = None,
    session_id: str | None = None,
    turn_id: str | None = None,
) -> dict[str, Any]:
    """Build the payload for reflexd inject_event."""
    payload_data = {
        "text": text or "",
        "platform": platform,
        "chat_id": chat_id,
        "user_id": user_id,
        "session_id": session_id or "",
        "turn_id": turn_id or "",
        "seq": next(_SEQ),
        "ts": time.time(),
        "v": 1,
        "data": data if data is not None else {},
    }
    return {
        "name": event_type,
        "data": payload_data,
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

    if chat_id == "unknown" and session_id:
        chat_id = session_id

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
        self._pending_futures: set[ConcurrentFuture[Any]] = set()
        self._pending_lock = threading.Lock()
        self._client: httpx.AsyncClient | None = None
        self._worker_exc: BaseException | None = None
        self._breaker_failures = 0
        self._breaker_open = False
        self._breaker_open_time = 0.0
        self._breaker_lock = threading.Lock()

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(
                    connect=0.5, read=self._timeout, write=self._timeout, pool=5.0
                ),
                limits=httpx.Limits(
                    max_connections=10, max_keepalive_connections=5
                ),
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
            self._worker_exc = None

            def _run() -> None:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                self._worker_loop = loop
                self._worker_ready.set()
                try:
                    loop.run_forever()
                except BaseException as e:
                    self._worker_exc = e
                finally:
                    loop.close()

            self._worker_thread = threading.Thread(
                target=_run, daemon=True, name="body-bridge-worker"
            )
            self._worker_thread.start()
            if not self._worker_ready.wait(timeout=0.5):
                logger.error(
                    "body_bridge: worker loop did not become ready",
                    exc_info=self._worker_exc,
                )
                return None
            logger.info("body_bridge: worker loop started")
            return self._worker_loop

    def _cleanup_future(self, fut: ConcurrentFuture[Any]) -> None:
        with self._pending_lock:
            self._pending_futures.discard(fut)

    def _close_client(self, loop: asyncio.AbstractEventLoop) -> None:
        """Close the httpx client on the worker loop."""
        client = self._client
        self._client = None
        if client is not None and not client.is_closed:
            loop.call_soon_threadsafe(
                lambda c=client: asyncio.ensure_future(c.aclose(), loop=loop)
            )

    async def _post_to_reflexd(
        self,
        event_type: str,
        text: str | None,
        platform: str,
        chat_id: str,
        user_id: str,
        data: dict[str, Any] | None = None,
        session_id: str | None = None,
        turn_id: str | None = None,
    ) -> None:
        """Send a single event to reflexd MCP endpoint."""
        payload = _build_payload(
            event_type, text, platform, chat_id, user_id, data, session_id, turn_id
        )

        try:
            client = await self._get_client()
            response = await client.post(
                self._mcp_url,
                json={
                    "jsonrpc": "2.0",
                    "method": "tools/call",
                    "params": {
                        "name": _REFLEXD_INJECT_TOOL,
                        "arguments": payload,
                    },
                    "id": next(_RPC_ID),
                },
            )
            if response.status_code >= 400:
                logger.warning(
                    "body_bridge: POST %s -> HTTP %s",
                    event_type,
                    response.status_code,
                )
                self._record_fail()
            else:
                logger.debug(
                    "body_bridge: POST %s -> HTTP %s", event_type, response.status_code
                )
                self._record_success()
        except Exception as e:
            logger.warning("body_bridge: POST %s failed: %s", event_type, e)
            self._record_fail()

    def _record_success(self) -> None:
        with self._breaker_lock:
            self._breaker_failures = 0
            self._breaker_open = False
        with _metrics_lock:
            _metrics["sent_ok"] += 1

    def _record_fail(self) -> None:
        with self._breaker_lock:
            self._breaker_failures += 1
            if self._breaker_failures >= _REFLEXD_BREAKER_THRESHOLD and not self._breaker_open:
                self._breaker_open = True
                self._breaker_open_time = time.time()
                logger.info(
                    "body_bridge: circuit breaker OPEN after %d failures",
                    self._breaker_failures,
                )
        with _metrics_lock:
            _metrics["sent_fail"] += 1

    def _check_breaker(self) -> bool:
        """Return True if breaker allows request, False if open (dropped)."""
        with self._breaker_lock:
            if not self._breaker_open:
                return True
            # half-open after cooldown
            if time.time() - self._breaker_open_time >= _REFLEXD_BREAKER_COOLDOWN:
                logger.info("body_bridge: circuit breaker HALF-OPEN, allowing probe")
                return True
            return False

    def schedule_post(
        self,
        event_type: str,
        text: str | None,
        platform: str,
        chat_id: str,
        user_id: str,
        data: dict[str, Any] | None = None,
        session_id: str | None = None,
        turn_id: str | None = None,
    ) -> None:
        """Schedule a fire-and-forget POST to reflexd MCP (thread-safe)."""
        norm_platform = (platform or "unknown").strip().lower()

        logger.debug(
            "body_bridge: schedule_post called: event_type=%s, platform=%s, chat_id=%s",
            event_type,
            norm_platform,
            chat_id,
        )

        if _is_local_platform(norm_platform):
            logger.debug("body_bridge: skipping local platform: %s", norm_platform)
            with _metrics_lock:
                _metrics["dropped_local"] += 1
            return

        if not self._check_breaker():
            logger.debug(
                "body_bridge: circuit breaker open, dropping %s event", event_type
            )
            with _metrics_lock:
                _metrics["dropped_breaker"] += 1
            return

        loop = self._ensure_worker()
        if loop is None:
            logger.warning("body_bridge: worker loop unavailable, dropping %s event", event_type)
            return

        async def _post() -> None:
            await self._post_to_reflexd(
                event_type, text, norm_platform, chat_id, user_id, data, session_id, turn_id
            )

        try:
            fut = asyncio.run_coroutine_threadsafe(_post(), loop)
        except Exception as e:
            logger.warning("body_bridge: failed to schedule %s: %s", event_type, e)
            return

        with self._pending_lock:
            if len(self._pending_futures) >= _REFLEXD_MAX_PENDING:
                oldest = next(iter(self._pending_futures))
                self._pending_futures.discard(oldest)
                oldest.cancel()
                with _metrics_lock:
                    _metrics["dropped_pending"] += 1
                    now = time.time()
                    if not hasattr(self, "_last_pending_warn") or (
                        now - self._last_pending_warn >= 10
                    ):
                        logger.warning(
                            "body_bridge: pending queue full (%d), dropped oldest",
                            _REFLEXD_MAX_PENDING
                        )
                        self._last_pending_warn = now
            self._pending_futures.add(fut)
        fut.add_done_callback(self._cleanup_future)
        with _metrics_lock:
            _metrics["scheduled"] += 1

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

        if loop is not None and self._worker_thread is not None and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=2.0)
            if self._worker_thread.is_alive():
                logger.warning("body_bridge: worker thread did not stop within timeout")
        logger.info("body_bridge: worker loop stopped")

        # cancel pending futures
        with self._pending_lock:
            for fut in list(self._pending_futures):
                fut.cancel()
            self._pending_futures.clear()

        global _stream_ttl_task
        if _stream_ttl_task and not _stream_ttl_task.done():
            _stream_ttl_task.cancel()

        self._log_metrics()

    def _log_metrics(self) -> None:
        """Log metrics snapshot."""
        with _metrics_lock:
            if _metrics:
                logger.info(
                    "body_bridge: metrics scheduled=%d sent_ok=%d sent_fail=%d "
                    "dropped_pending=%d dropped_breaker=%d dropped_local=%d",
                    _metrics["scheduled"],
                    _metrics["sent_ok"],
                    _metrics["sent_fail"],
                    _metrics["dropped_pending"],
                    _metrics["dropped_breaker"],
                    _metrics["dropped_local"],
                )


_dispatcher = EventDispatcher()


def _schedule_post(
    event_type: str,
    text: str | None,
    platform: str,
    chat_id: str,
    user_id: str,
    data: dict[str, Any] | None = None,
    session_id: str | None = None,
    turn_id: str | None = None,
) -> None:
    """Backward-compatible wrapper for existing hook/middleware code."""
    _dispatcher.schedule_post(
        event_type, text, platform, chat_id, user_id, data, session_id, turn_id
    )


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
    logger.debug(
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
        session_id=session_key,
        turn_id=message_id,
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
) -> tuple[bool, Any]:
    """post_llm_call: fires after LLM completes its turn, before response delivery.

    Returns (consumed, reply) to match hook contract across Hermes builds.
    """
    logger.debug(
        "body_bridge: post_llm_call called: session_id=%s, platform=%s",
        session_id,
        platform,
    )
    if not assistant_response or not assistant_response.strip():
        logger.debug("body_bridge: post_llm_call empty response, skipping")
        return False, None

    chat_id, user_id = _extract_chat_user_from_history(conversation_history, session_id)

    _schedule_post(
        "user_response",
        text=assistant_response,
        platform=platform,
        chat_id=chat_id,
        user_id=user_id,
        session_id=session_id,
        turn_id=turn_id,
    )
    return False, None


# ─── LLM Execution Middleware: thinking_start / thinking_end ───
# Signature per docs: def middleware(**kwargs) -> response
# MUST BE SYNC — _run_execution_chain invokes the callback without await.
def _llm_execution_middleware(**kwargs: Any) -> Any:
    """Middleware wrapper around LLM execution (SYNC).

    Fires thinking_start BEFORE calling next_call (the actual LLM),
    fires thinking_end AFTER next_call returns.
    """
    logger.debug(
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

    logger.debug("body_bridge: firing thinking_start")
    _schedule_post(
        "thinking_start",
        text="",
        platform=platform,
        chat_id=chat_id,
        user_id=user_id,
        session_id=session_id,
    )

    try:
        return next_call(request)
    finally:
        logger.debug("body_bridge: firing thinking_end")
        _schedule_post(
            "thinking_end",
            text="",
            platform=platform,
            chat_id=chat_id,
            user_id=user_id,
            session_id=session_id,
        )


async def _gateway_platform_event_hook(
    platform: str, event_type: str, payload: dict, **kwargs
) -> None:
    """Forward platform events (reactions, edits) to reflexd."""
    chat_id = str(payload.get("chat_id") or "unknown")
    user_id = str(payload.get("user_id") or "unknown")

    _schedule_post(
        "platform_event",
        text="",
        platform=platform,
        chat_id=chat_id,
        user_id=user_id,
        data={
            "event_type": event_type,
            "payload": payload,
        },
    )


async def _on_stream_start_hook(
    session_id: str, turn_id: str, iteration: int, model: str, provider: str, surface: str, **kwargs
) -> None:
    _schedule_post(
        "stream_start",
        text="",
        platform="stream",
        chat_id=session_id,
        user_id="unknown",
        session_id=session_id,
        turn_id=turn_id,
    )


async def _on_stream_delta_hook(
    session_id: str,
    turn_id: str,
    iteration: int,
    model: str,
    provider: str,
    surface: str,
    delta: str,
    kind: str,
    **kwargs,
) -> None:
    _stream_buffer[session_id].append(delta)
    _stream_lengths[session_id] = _stream_lengths.get(session_id, 0) + len(delta)
    if _stream_lengths[session_id] >= _STAYM_FLUSH_CHARS:
        if _stream_timers.get(session_id):
            _stream_timers[session_id].cancel()
        await _flush_stream_buffer(session_id)
    else:
        if session_id not in _stream_timers:

            async def _timer() -> None:
                await asyncio.sleep(_STAYM_FLUSH_MS / 1000)
                await _flush_stream_buffer(session_id)

            _stream_timers[session_id] = asyncio.create_task(_timer())


async def _on_stream_end_hook(
    session_id: str,
    turn_id: str,
    iteration: int,
    model: str,
    provider: str,
    surface: str,
    final_text: str,
    finished: bool,
    error: str | None,
    **kwargs,
) -> None:
    await _flush_stream_buffer(session_id)
    _schedule_post(
        "stream_end",
        text=final_text,
        platform="stream",
        chat_id=session_id,
        user_id="unknown",
        session_id=session_id,
        turn_id=turn_id,
    )


async def _flush_stream_buffer(session_id: str) -> None:
    if session_id not in _stream_buffer:
        return
    text = "".join(_stream_buffer.pop(session_id, []))
    _stream_lengths.pop(session_id, None)
    t = _stream_timers.pop(session_id, None)
    if t and not t.done():
        t.cancel()
    if text:
        _schedule_post(
            "stream_delta",
            text=text,
            platform="stream",
            chat_id=session_id,
            user_id="unknown",
            session_id=session_id,
        )


_stream_ttl_sweep_task: asyncio.Task | None = None


async def _start_stream_ttl_sweep(loop: asyncio.AbstractEventLoop) -> None:
    """Start periodic TTL sweep for stream buffers."""
    global _stream_ttl_task
    if _stream_ttl_task and not _stream_ttl_task.done():
        return

    async def _sweep() -> None:
        while True:
            try:
                await asyncio.sleep(60)
                # Simpler TTL: flush all buffers on session_end/finalize (done in hooks)
                # and rely on TTL for orphaned sessions.
                # No per-session timestamp stored; skip aggressive TTL for simplicity.
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("body_bridge: stream TTL sweep error")

    _stream_ttl_task = loop.create_task(_sweep())


async def _pre_gateway_dispatch_hook(
    event: Any, gateway: Any, session_store: Any, **kwargs
) -> dict | None:
    """Filter/rewrite incoming events before auth/pairing."""
    text = event.get("text", "") if isinstance(event, dict) else getattr(event, "text", "")
    if not text or not text.strip():
        return {"action": "skip", "reason": "empty message"}
    return None


async def _on_session_end_hook(
    session_id: str,
    completed: bool,
    interrupted: bool,
    model: str | None,
    platform: str,
    reason: str,
    task_id: str,
    turn_id: str,
    api_request_id: str,
    **kwargs,
) -> None:
    await _flush_stream_buffer(session_id)
    _schedule_post(
        "session_end",
        text="",
        platform=platform,
        chat_id=session_id,
        user_id="unknown",
        session_id=session_id,
        turn_id=turn_id,
    )


async def _on_session_finalize_hook(session_id: str, platform: str, reason: str, **kwargs) -> None:
    await _flush_stream_buffer(session_id)
    _schedule_post(
        "session_finalize",
        text="",
        platform=platform,
        chat_id=session_id,
        user_id="unknown",
        session_id=session_id,
    )


async def _api_request_error_hook(
    task_id: str,
    turn_id: str,
    api_request_id: str,
    session_id: str,
    platform: str,
    model: str,
    provider: str,
    base_url: str,
    api_mode: str,
    api_call_count: int,
    api_duration: float,
    started_at: float,
    ended_at: float,
    status_code: int | None,
    retry_count: int | None,
    max_retries: int | None,
    retryable: bool | None,
    reason: str | None,
    error: dict,
    request: dict,
    **kwargs,
) -> None:
    error_msg = error.get("message", "Unknown error") if isinstance(error, dict) else str(error)
    _schedule_post(
        "api_error",
        text=error_msg,
        platform=provider,
        chat_id=model,
        user_id="unknown",
        session_id=session_id,
        turn_id=turn_id,
    )


def register(ctx: Any) -> None:
    """Plugin entry point — called by Hermes on startup."""
    logger.info("Body Bridge plugin loaded: registering hooks and middleware")

    _dispatcher._ensure_worker()
    loop = _dispatcher._worker_loop
    if loop:
        asyncio.run_coroutine_threadsafe(_start_stream_ttl_sweep(loop), loop)

    ctx.register_hook("post_gateway_admission", _post_gateway_admission_hook)
    ctx.register_hook("post_llm_call", _post_llm_call_hook)
    ctx.register_middleware("llm_execution", _llm_execution_middleware)

    ctx.register_hook("gateway_platform_event", _gateway_platform_event_hook)
    ctx.register_hook("on_stream_start", _on_stream_start_hook)
    ctx.register_hook("on_stream_delta", _on_stream_delta_hook)
    ctx.register_hook("on_stream_end", _on_stream_end_hook)
    ctx.register_hook("pre_gateway_dispatch", _pre_gateway_dispatch_hook)
    ctx.register_hook("on_session_end", _on_session_end_hook)
    ctx.register_hook("on_session_finalize", _on_session_finalize_hook)
    ctx.register_hook("api_request_error", _api_request_error_hook)

    logger.info(
        "Body Bridge hooks/middleware registered: "
        "post_gateway_admission, post_llm_call, gateway_platform_event, "
        "on_stream_start, on_stream_delta, on_stream_end, "
        "pre_gateway_dispatch, on_session_end, on_session_finalize, "
        "api_request_error, llm_execution"
    )
