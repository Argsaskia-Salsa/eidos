import asyncio
import importlib
import sys
from pathlib import Path
from aiohttp import web

from .registry import get_handler, list_handlers, clear
from .gateway_tools import GATEWAY_TOOLS
from .gateway_client import get_gateway_client


_running: dict[str, asyncio.Task] = {}
_gateway_client_instance = None


async def handle_inject_event(name: str, data: dict) -> dict:
    global _gateway_client_instance

    if name in _running:
        _running[name].cancel()
        try:
            await _running[name]
        except asyncio.CancelledError:
            pass

    handler = get_handler(name)
    if handler:
        client = get_gateway_client()
        if client:
            _gateway_client_instance = client

        task = asyncio.create_task(handler(data))
        _running[name] = task
        try:
            await task
            return {"status": "ok", "event": name}
        except asyncio.CancelledError:
            return {"status": "cancelled", "event": name}
        except Exception as e:
            return {"status": "error", "event": name, "error": str(e)}
        finally:
            _running.pop(name, None)
    else:
        return {"status": "no_handler", "event": name}


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
        return {"status": "error", "message": f"Reflexes directory not found: {reflexes_dir}"}

    sys.path.insert(0, str(reflexes_path.parent))

    loaded = []
    errors = []

    for py_file in reflexes_path.glob("*.py"):
        if py_file.name.startswith("_"):
            continue
        module_name = py_file.stem
        try:
            importlib.import_module(f"reflexes.{module_name}")
            loaded.append(module_name)
        except Exception as e:
            errors.append({"module": module_name, "error": str(e)})

    return {"status": "ok", "loaded": loaded, "errors": errors}


async def handle_jsonrpc(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except Exception:
        return web.json_response({
            "jsonrpc": "2.0",
            "id": None,
            "error": {"code": -32700, "message": "Parse error"}
        }, status=400)

    jsonrpc = body.get("jsonrpc")
    if jsonrpc != "2.0":
        return web.json_response({
            "jsonrpc": "2.0",
            "id": body.get("id"),
            "error": {"code": -32600, "message": "Invalid Request"}
        }, status=400)

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
                    result = {"error": "Missing event name"}
                else:
                    result = await handle_inject_event(name, data)
            elif tool_name == "call":
                tool = arguments.get("tool")
                args = arguments.get("arguments", {})
                if not tool:
                    result = {"error": "Missing tool name"}
                else:
                    result = await handle_call(tool, args)
            elif tool_name == "list_reflexes":
                result = await handle_list_reflexes()
            elif tool_name == "list_gateway_tools":
                result = await handle_list_gateway_tools()
            elif tool_name == "reload_reflexes":
                from .main import REFLEXES_DIR
                result = await handle_reload_reflexes(REFLEXES_DIR)
            elif tool_name == "user_message":
                result = await handle_inject_event("user_message", arguments.get("data", {}))
            elif tool_name == "user_response":
                result = await handle_inject_event("user_response", arguments.get("data", {}))
            elif tool_name == "thinking_start":
                result = await handle_inject_event("thinking_start", arguments.get("data", {}))
            elif tool_name == "thinking_end":
                result = await handle_inject_event("thinking_end", arguments.get("data", {}))
            else:
                result = {"error": f"Unknown tool: {tool_name}"}
        else:
            return web.json_response({
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32601, "message": f"Method not found: {method}"}
            }, status=404)

        return web.json_response({
            "jsonrpc": "2.0",
            "id": req_id,
            "result": result
        })
    except Exception as e:
        return web.json_response({
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32603, "message": str(e)}
        }, status=500)


async def health_check(request: web.Request) -> web.Response:
    return web.json_response({"status": "ok"})


def create_app() -> web.Application:
    app = web.Application()
    app.router.add_post("/mcp", handle_jsonrpc)
    app.router.add_get("/health", health_check)
    return app
