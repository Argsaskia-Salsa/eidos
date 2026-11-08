"""Body Bridge Plugin — forwards Hermes events to reflexd via MCP.

Hooks:
- post_gateway_admission → user_message (after auth/admission, inside session slot)
- post_llm_call → user_response (after LLM completes its turn)

Middleware:
- LLM_EXECUTION_MIDDLEWARE → thinking_start / thinking_end (at exact LLM generation boundaries)

Posts to reflexd MCP (http://localhost:8770/mcp) for reflexd consumption.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from typing import Any

import httpx

logger = logging.getLogger("hermes.plugins.body_bridge")
logger.info("Body Bridge plugin module loaded")

REFLEXD_MCP_URL = "http://localhost:8770/mcp"

# ─── Dedicated worker thread with its own event loop ───
# The llm_execution middleware is *synchronous* and may run on a thread
# without a running loop, or on the loop thread while next_call() blocks.
# A dedicated worker loop makes _schedule_post safe from any thread and
# ensures thinking_start is dispatched immediately, not blocked behind
# the actual LLM generation.
_worker_loop: asyncio.AbstractEventLoop | None = None
_worker_thread: threading.Thread | None = None
_worker_ready = threading.Event()
_worker_lock = threading.Lock()

# Keep references to in-flight futures so they aren't GC'd mid-flight.
_pending_futures: set = set()
_pending_lock = threading.Lock()


def _ensure_worker() -> asyncio.AbstractEventLoop | None:
    """Start (or restart) the worker loop. Returns the loop, or None on failure."""
    global _worker_loop, _worker_thread
    if _worker_loop is not None and _worker_loop.is_running():
        return _worker_loop

    with _worker_lock:
        if _worker_loop is not None and _worker_loop.is_running():
            return _worker_loop
        _worker_ready.clear()

        def _run() -> None:
            global _worker_loop
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            _worker_loop = loop
            _worker_ready.set()
            try:
                loop.run_forever()
            finally:
                loop.close()

        _worker_thread = threading.Thread(
            target=_run, daemon=True, name="body-bridge-worker"
        )
        _worker_thread.start()
        if not _worker_ready.wait(timeout=5.0):
            logger.warning("body_bridge: worker loop did not become ready")
            return None
        return _worker_loop


def _cleanup_future(fut) -> None:
    with _pending_lock:
        _pending_futures.discard(fut)


def _schedule_post(
    event_type: str,
    text: str,
    platform: str,
    chat_id: str,
    user_id: str,
) -> None:
    """Schedule a fire-and-forget POST to reflexd MCP (thread-safe)."""
    logger.info("body_bridge: _schedule_post called: event_type=%s, platform=%s, chat_id=%s", event_type, platform, chat_id)
    # Guard: platform may be None.
    platform_norm = (platform or "").lower()
    if platform_norm in ("local", "desktop", "cli"):
        logger.info("body_bridge: skipping local platform: %s", platform_norm)
        return

    payload = {
        "name": event_type,
        "data": {
            "text": text or "",
            "platform": platform or "unknown",
            "chat_id": chat_id,
            "user_id": user_id,
        },
        "type": "inject",
    }

    async def _post() -> None:
        try:
            async with httpx.AsyncClient(timeout=1.0) as client:
                response = await client.post(
                    REFLEXD_MCP_URL,
                    json={
                        "jsonrpc": "2.0",
                        "method": "tools/call",
                        "params": {
                            "name": "inject_event",
                            "arguments": payload,
                        },
                        "id": 1,
                    },
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
            # Failures used to be silent (debug). Surface them.
            logger.warning("body_bridge: POST %s failed: %s", event_type, e)

    loop = _ensure_worker()
    if loop is None:
        logger.warning(
            "body_bridge: worker loop unavailable, dropping %s event", event_type
        )
        return

    try:
        fut = asyncio.run_coroutine_threadsafe(_post(), loop)
    except Exception as e:
        logger.warning("body_bridge: failed to schedule %s: %s", event_type, e)
        return

    with _pending_lock:
        _pending_futures.add(fut)
    fut.add_done_callback(_cleanup_future)


async def _post_gateway_admission_hook(
    session_key: str,
    platform: str,
    source: dict,
    message_id: str,
    text: str,
) -> tuple[bool, Any]:
    """post_gateway_admission: fires after auth+admission, inside session slot.

    Perfect point for user_message — message is validated and has a session.
    Returns (consumed, reply). consumed=False to let message continue.
    """
    logger.info("body_bridge: post_gateway_admission called: session_key=%s, platform=%s, text=%s", session_key, platform, text[:50] if text else "")
    if not text or not text.strip():
        logger.debug("body_bridge: post_gateway_admission empty text, skipping")
        return False, None

    # Guard: source may be None or not a dict.
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

    return False, None  # False = not consumed, let it continue


async def _post_llm_call_hook(
    session_id: str,
    task_id: str,
    turn_id: str,
    user_message: str,
    assistant_response: str,
    conversation_history: list,
    model: str,
    platform: str,
    side_question_token: Any = None,
) -> None:
    """post_llm_call: fires after LLM completes its turn, before response delivery.

    Returns None (observer hook). If your Hermes build expects a
    (consumed, reply) tuple here, change the return accordingly and
    verify against the framework version.
    """
    logger.info("body_bridge: post_llm_call called: session_id=%s, platform=%s", session_id, platform)
    if not assistant_response or not assistant_response.strip():
        logger.debug("body_bridge: post_llm_call empty response, skipping")
        return None

    # Best-effort extraction of chat/user identity from the last user message.
    chat_id = "unknown"
    user_id = "unknown"
    for msg in reversed(conversation_history or []):
        if not isinstance(msg, dict):
            continue
        if msg.get("role") != "user":
            continue
        meta = msg.get("metadata") if isinstance(msg.get("metadata"), dict) else {}
        chat_id = str(
            msg.get("chat_id") or meta.get("chat_id") or session_id or "unknown"
        )
        user_id = str(msg.get("user_id") or meta.get("user_id") or "unknown")
        break

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
    logger.info("body_bridge: llm_execution middleware called: session_id=%s, platform=%s", kwargs.get("session_id"), kwargs.get("platform"))
    request = kwargs.get("request", {})
    next_call = kwargs.get("next_call")
    if next_call is None:
        # No downstream to call — bail out without emitting events.
        return request

    session_id = str(kwargs.get("session_id") or "")
    platform = str(kwargs.get("platform") or "")

    if platform.lower() in ("local", "desktop", "cli"):
        return next_call(request)

    chat_id = str(kwargs.get("chat_id") or session_id or "unknown")
    user_id = str(kwargs.get("user_id") or "unknown")

    # THINKING START — dispatched via the worker loop so it does not get
    # blocked behind the synchronous next_call() below.
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
        # THINKING END
        logger.info("body_bridge: firing thinking_end")
        _schedule_post(
            "thinking_end",
            text="",
            platform=platform,
            chat_id=chat_id,
            user_id=user_id,
        )


def register(ctx) -> None:
    """Plugin entry point — called by Hermes on startup."""
    logger.info("Body Bridge plugin loaded: registering hooks and middleware")

    # Bring up the background worker before any event can arrive.
    _ensure_worker()

    ctx.register_hook("post_gateway_admission", _post_gateway_admission_hook)
    ctx.register_hook("post_llm_call", _post_llm_call_hook)
    ctx.register_middleware("llm_execution", _llm_execution_middleware)

    logger.info(
        "Body Bridge hooks/middleware registered: "
        "post_gateway_admission, post_llm_call, llm_execution"
    )