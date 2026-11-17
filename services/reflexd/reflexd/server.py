import asyncio
import importlib
import sys
import time
from pathlib import Path

import aiohttp
from aiohttp import web
from jsonschema import ValidationError, validate

from .gateway_client import get_gateway_client
from .gateway_tools import GATEWAY_TOOLS
from .registry import clear, get_handler, list_handlers

_running: dict[str, asyncio.Task] = {}
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
            raise InjectEventError(-32602, f"Invalid arguments for tool '{tool_name}': {e.message}")


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


async def handle_inject_event(name: str, data: dict) -> dict:
    lock = _get_running_lock()
    async with lock:
        if name in _running:
            _running[name].cancel()
            try:
                await _running[name]
            except asyncio.CancelledError:
                pass

        handler = get_handler(name)
        if handler:
            get_gateway_client()

            task = asyncio.create_task(handler(data))
            _running[name] = task
        else:
            raise NoHandlerError(name)

    try:
        await task
        return {"status": "ok", "event": name}
    except asyncio.CancelledError:
        raise EventCancelledError(name)
    except Exception as e:  # noqa: BLE001
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
            # Remove from sys.modules to force re-import and re-execute decorators
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
        if method == "tools/call":
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
            else:
                return web.json_response(
                    {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "error": {"code": -32601, "message": f"Unknown tool: {tool_name}"},
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
    except Exception as e:  # noqa: BLE001
        return web.json_response(
            {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32603, "message": str(e)},
            },
            status=500,
        )


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
    app.router.add_get("/health", health_check)
    return app
