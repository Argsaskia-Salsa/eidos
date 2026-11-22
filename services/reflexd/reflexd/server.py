import asyncio
import importlib
import logging
import os
import sys
import time
from pathlib import Path

import aiohttp
from aiohttp import web
from jsonschema import ValidationError, validate

from .gateway_client import get_gateway_client
from .gateway_tools import GATEWAY_TOOLS
from .registry import (
    clear,
    get_cleanup,
    get_handler,
    get_metadata,
    list_handlers,
)

logger = logging.getLogger(__name__)

import uuid
from typing import TypedDict

# Session management
_sessions: dict[str, dict] = {}
_session_lock = asyncio.Lock()

# Event types that can be received via /events endpoint
VALID_EVENT_TYPES = {
    "user_message",
    "user_response",
    "thinking_start",
    "thinking_end",
    "platform_event",
    "stream_start",
    "stream_delta",
    "stream_end",
    "session_end",
    "session_finalize",
    "api_error",
}

# WebSocket clients for real-time event streaming
_event_ws_clients: set[web.WebSocketResponse] = set()
_EVENT_WS_MAX_CLIENTS = int(os.getenv("EVENTS_WS_MAX_CLIENTS", "10"))

# Event queue for processing - will be initialized lazily per event loop
_event_queues: dict[int, asyncio.Queue] = {}
_event_processor_task: asyncio.Task | None = None


def _get_event_queue() -> asyncio.Queue:
    """Get or create the event queue for the current event loop."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # No running loop, create a new one (fallback)
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    
    loop_id = id(loop)
    if loop_id not in _event_queues:
        _event_queues[loop_id] = asyncio.Queue()
    return _event_queues[loop_id]


def _new_session_id() -> str:
    return uuid.uuid4().hex


async def _get_session(session_id: str) -> dict | None:
    async with _session_lock:
        return _sessions.get(session_id)


async def _create_session() -> str:
    session_id = _new_session_id()
    async with _session_lock:
        _sessions[session_id] = {"created": time.time(), "data": {}}
    return session_id


async def _delete_session(session_id: str) -> None:
    async with _session_lock:
        _sessions.pop(session_id, None)


class RunningEntry(TypedDict):
    task: asyncio.Task
    priority: int
    conflict_group: str | None


_running: dict[str, RunningEntry] = {}
_start_time = time.time()


class InjectEventError(Exception):
    def __init__(self, code: int, message: str, event: str | None = None):
        self.code = code
        self.message = message
        self.event = event
        super().__init__(message)


class NoHandlerError(InjectEventError):
    def __init__(self, event: str):
        super().__init__(-32601, f"No handler for event: {event}", event)


class HandlerError(InjectEventError):
    def __init__(self, event: str, error: str):
        super().__init__(-32603, f"Handler error for event {event}: {error}", event)


class EventCancelledError(InjectEventError):
    def __init__(self, event: str):
        super().__init__(-32000, f"Event cancelled: {event}", event)


def _get_reflexes_dir() -> str:
    from .main import REFLEXES_DIR

    return REFLEXES_DIR


def _validate_tool_arguments(tool_name: str, arguments: dict) -> None:
    tool_schema = GATEWAY_TOOLS.get(tool_name)
    if tool_schema:
        try:
            validate(instance=arguments, schema=tool_schema)
        except ValidationError as e:
            raise InjectEventError(
                -32602, f"Invalid arguments for tool '{tool_name}': {e.message}"
            )


_running_locks: dict[int, asyncio.Lock] = {}


def _get_running_lock() -> asyncio.Lock:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.get_event_loop()
    loop_id = id(loop)
    if loop_id not in _running_locks:
        _running_locks[loop_id] = asyncio.Lock()
    return _running_locks[loop_id]


async def cancel_reflex(name: str) -> bool:
    """Cancel a specific running reflex by name, run its cleanup."""
    lock = _get_running_lock()
    async with lock:
        entry = _running.pop(name, None)
    if entry:
        entry["task"].cancel()
        try:
            await entry["task"]
        except asyncio.CancelledError:
            pass
        cleanup = get_cleanup(name)
        if cleanup:
            try:
                await cleanup()
            except Exception:
                logger.exception("Cleanup failed for %s", name)
        return True
    return False


async def handle_inject_event(name: str, data: dict) -> dict:
    lock = _get_running_lock()
    async with lock:
        if name in _running:
            _running[name]["task"].cancel()
            try:
                await _running[name]["task"]
            except asyncio.CancelledError:
                pass

        meta = get_metadata(name)
        new_priority = meta.get("priority", 0)
        new_conflict_group = meta.get("conflict_group")

        to_cancel = []
        for running_name, entry in _running.items():
            entry_priority = entry["priority"]
            entry_group = entry["conflict_group"]
            should_cancel = False
            if (
                new_conflict_group
                and entry_group == new_conflict_group
                or new_priority > entry_priority
            ):
                should_cancel = True
            if should_cancel:
                to_cancel.append(running_name)

        for running_name in to_cancel:
            entry = _running.pop(running_name)
            entry["task"].cancel()
            try:
                await entry["task"]
            except asyncio.CancelledError:
                pass
            cleanup = get_cleanup(running_name)
            if cleanup:
                try:
                    await cleanup()
                except Exception:
                    logger.exception("Cleanup failed for %s", running_name)

        handler = get_handler(name)
        if handler:
            get_gateway_client()

            task = asyncio.create_task(handler(data))
            _running[name] = {
                "task": task,
                "priority": new_priority,
                "conflict_group": new_conflict_group,
            }
        else:
            raise NoHandlerError(name)

    try:
        await task
        return {"status": "ok", "event": name}
    except asyncio.CancelledError:
        raise EventCancelledError(name)
    except Exception as e:  # noqa: BLE001 - catch all handler errors for MCP response
        raise HandlerError(name, str(e))
    finally:
        async with lock:
            _running.pop(name, None)


async def handle_call(tool: str, arguments: dict) -> dict:
    client = get_gateway_client()
    if not client:
        raise RuntimeError("Gateway client not connected")
    return await client.call(tool, arguments)


async def handle_list_reflexes() -> dict:
    return {"reflexes": list_handlers()}


async def handle_list_gateway_tools() -> dict:
    return {"tools": GATEWAY_TOOLS}


async def handle_reload_reflexes(reflexes_dir: str) -> dict:
    clear()

    reflexes_path = Path(reflexes_dir).expanduser()
    if not reflexes_path.exists():
        return {
            "status": "error",
            "message": f"Reflexes directory not found: {reflexes_dir}",
        }

    sys.path.insert(0, str(reflexes_path.parent))

    loaded = []
    errors = []

    for py_file in reflexes_path.glob("*.py"):
        if py_file.name.startswith("_"):
            continue
        module_name = py_file.stem
        full_module_name = f"reflexes.{module_name}"
        try:
            if full_module_name in sys.modules:
                del sys.modules[full_module_name]
            importlib.import_module(full_module_name)
            loaded.append(module_name)
        except (ImportError, ModuleNotFoundError, SyntaxError) as e:
            errors.append({"module": module_name, "error": str(e)})

    return {"status": "ok", "loaded": loaded, "errors": errors}


async def handle_jsonrpc(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except (aiohttp.ContentTypeError, ValueError):
        return web.json_response(
            {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": "Parse error"},
            },
            status=400,
        )

    jsonrpc = body.get("jsonrpc")
    if jsonrpc != "2.0":
        return web.json_response(
            {
                "jsonrpc": "2.0",
                "id": body.get("id"),
                "error": {"code": -32600, "message": "Invalid Request"},
            },
            status=400,
        )

    method = body.get("method")
    params = body.get("params", {})
    req_id = body.get("id")

    try:
        if method == "initialize":
            session_id = await _create_session()
            result = {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "eidos-reflexd", "version": "0.1.0"},
            }
            response = web.json_response(
                {"jsonrpc": "2.0", "id": req_id, "result": result}
            )
            response.headers["Mcp-Session-Id"] = session_id
            return response
        elif method == "tools/call":
            # Validate session
            session_id = request.headers.get("Mcp-Session-Id")
            if not session_id:
                return web.json_response(
                    {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "error": {
                            "code": -32600,
                            "message": "Missing Mcp-Session-Id header",
                        },
                    },
                    status=400,
                )
            session = await _get_session(session_id)
            if not session:
                return web.json_response(
                    {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "error": {
                            "code": -32600,
                            "message": "Invalid or expired session",
                        },
                    },
                    status=400,
                )

            tool_name = params.get("name")
            arguments = params.get("arguments", {})

            if tool_name == "inject_event":
                name = arguments.get("name")
                data = arguments.get("data", {})
                if not name:
                    return web.json_response(
                        {
                            "jsonrpc": "2.0",
                            "id": req_id,
                            "error": {"code": -32602, "message": "Missing event name"},
                        },
                        status=400,
                    )
                result = await handle_inject_event(name, data)
            elif tool_name == "call":
                tool = arguments.get("tool")
                args = arguments.get("arguments", {})
                if not tool:
                    return web.json_response(
                        {
                            "jsonrpc": "2.0",
                            "id": req_id,
                            "error": {"code": -32602, "message": "Missing tool name"},
                        },
                        status=400,
                    )
                _validate_tool_arguments(tool, args)
                result = await handle_call(tool, args)
            elif tool_name == "list_reflexes":
                result = await handle_list_reflexes()
            elif tool_name == "list_gateway_tools":
                result = await handle_list_gateway_tools()
            elif tool_name == "reload_reflexes":
                result = await handle_reload_reflexes(_get_reflexes_dir())
            elif tool_name == "user_message":
                result = await handle_inject_event(
                    "user_message", arguments.get("data", {})
                )
            elif tool_name == "user_response":
                result = await handle_inject_event(
                    "user_response", arguments.get("data", {})
                )
            elif tool_name == "thinking_start":
                result = await handle_inject_event(
                    "thinking_start", arguments.get("data", {})
                )
            elif tool_name == "thinking_end":
                result = await handle_inject_event(
                    "thinking_end", arguments.get("data", {})
                )
            elif tool_name == "cancel_reflex":
                reflex_name = arguments.get("name")
                if not reflex_name:
                    return web.json_response(
                        {
                            "jsonrpc": "2.0",
                            "id": req_id,
                            "error": {"code": -32602, "message": "Missing reflex name"},
                        },
                        status=400,
                    )
                success = await cancel_reflex(reflex_name)
                result = {"status": "ok", "cancelled": success}
            elif tool_name == "list_running_reflexes":
                lock = _get_running_lock()
                async with lock:
                    result = {"running": list(_running.keys())}
            else:
                return web.json_response(
                    {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "error": {
                            "code": -32601,
                            "message": f"Unknown tool: {tool_name}",
                        },
                    },
                    status=404,
                )
        else:
            return web.json_response(
                {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32601, "message": f"Method not found: {method}"},
                },
                status=404,
            )

        return web.json_response({"jsonrpc": "2.0", "id": req_id, "result": result})
    except InjectEventError as e:
        return web.json_response(
            {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": e.code, "message": e.message},
            },
            status=500 if e.code == -32603 else 200,
        )
    except Exception as e:  # noqa: BLE001 - catch all unexpected errors for MCP response
        return web.json_response(
            {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32603, "message": str(e)},
            },
            status=500,
        )


async def handle_events(request: web.Request) -> web.Response:
    """Handle events from body_expression_bridge via HTTP POST /events."""
    try:
        body = await request.json()
    except (aiohttp.ContentTypeError, ValueError):
        return web.json_response(
            {
                "ok": False,
                "error": "Invalid JSON",
            },
            status=400,
        )

    event_type = body.get("event_type")
    payload = body.get("payload", {})
    meta = body.get("meta", {})

    if not event_type:
        return web.json_response(
            {"ok": False, "error": "Missing event_type"},
            status=400,
        )

    if event_type not in VALID_EVENT_TYPES:
        return web.json_response(
            {"ok": False, "error": f"Unknown event_type: {event_type}"},
            status=400,
        )

    # Put event in queue for processing
    try:
        await asyncio.wait_for(_get_event_queue().put({
            "event_type": event_type,
            "payload": payload,
            "meta": meta,
        }), timeout=1.0)
    except TimeoutError:
        return web.json_response(
            {"ok": False, "error": "Event queue full"},
            status=503,
        )

    return web.json_response({"ok": True})


async def _event_processor() -> None:
    """Background task to process events from queue."""
    while True:
        try:
            event = await asyncio.wait_for(_get_event_queue().get(), timeout=1.0)
        except TimeoutError:
            continue

        event_type = event["event_type"]
        payload = event["payload"]
        meta = event.get("meta", {})

        try:
            if event_type in ("user_message", "thinking_start"):
                await handle_inject_event("thinking_start", payload)
            elif event_type in ("user_response", "thinking_end"):
                await handle_inject_event("thinking_end", payload)
            elif event_type == "user_message":
                await handle_inject_event("user_message", payload)
            elif event_type == "user_response":
                await handle_inject_event("user_response", payload)
            elif event_type == "thinking_start":
                await handle_inject_event("thinking_start", payload)
            elif event_type == "thinking_end":
                await handle_inject_event("thinking_end", payload)
            else:
                logger.debug("Unhandled event type: %s", event_type)
        except Exception:
            logger.exception("Error processing event %s", event_type)

        # Broadcast to WebSocket clients after successful processing
        ws_message = {
            "type": "event",
            "event_type": event_type,
            "payload": payload,
            "meta": meta,
        }
        for ws in list(_event_ws_clients):
            try:
                await ws.send_json(ws_message)
            except Exception:
                _event_ws_clients.discard(ws)


async def handle_events_stream(request: web.Request) -> web.WebSocketResponse:
    """WebSocket endpoint for real-time event streaming (GET /events/stream)."""
    if len(_event_ws_clients) >= _EVENT_WS_MAX_CLIENTS:
        return web.Response(status=503, text="Max WebSocket clients reached")

    ws = web.WebSocketResponse()
    await ws.prepare(request)
    _event_ws_clients.add(ws)

    try:
        await ws.send_json({"type": "connected", "event_types": list(VALID_EVENT_TYPES)})
        async for msg in ws:
            if msg.type == aiohttp.WSMsgType.CLOSE:
                break
            # Ignore incoming messages from client (keep-alive only)
    finally:
        _event_ws_clients.discard(ws)

    return ws


async def _start_event_processor() -> None:
    """Start the background event processor task."""
    global _event_processor_task
    if _event_processor_task is None or _event_processor_task.done():
        _event_processor_task = asyncio.create_task(_event_processor())
        logger.info("Event processor started")


async def health_check(request: web.Request) -> web.Response:
    client = get_gateway_client()
    gateway_connected = (
        client is not None
        and getattr(client, "session", None) is not None
        and getattr(client, "session_id", None) is not None
    )
    return web.json_response(
        {
            "status": "ok",
            "gateway_connected": gateway_connected,
            "reflexes_loaded": len(list_handlers()),
            "uptime_seconds": time.time() - _start_time,
        }
    )


def create_app() -> web.Application:
    app = web.Application()
    app.router.add_post("/mcp", handle_jsonrpc)
    app.router.add_post("/events", handle_events)
    app.router.add_get("/events/stream", handle_events_stream)
    app.router.add_get("/health", health_check)
    # Serve debug UI
    import os
    debug_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "tests", "debug")
    app.router.add_static("/tests/debug/", path=debug_path, name="debug")
    app.on_startup.append(lambda app: _start_event_processor())
    return app
