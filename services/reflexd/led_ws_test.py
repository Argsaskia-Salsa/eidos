#!/usr/bin/env python3
"""
WebSocket LED Stream Test Client.
1. Starts WebSocket server (background)
2. Connects gateway via stackchan_follow_led_stream
3. Runs test cycle
"""

import asyncio
import aiohttp
import os
import subprocess
import signal
import sys
import time
import uuid


GATEWAY_MCP_URL = os.getenv("GATEWAY_MCP_URL", "http://127.0.0.1:8767/mcp")
STACKCHAN_TOKEN = os.getenv("STACKCHAN_TOKEN", "")
# Use host IP since gateway runs in container/different namespace
HOST_IP = os.getenv("HOST_IP", "192.168.x.x")
WS_SERVER_URL = os.getenv("WS_SERVER_URL", f"ws://{HOST_IP}:8772")
TARGET = os.getenv("TARGET", "base_ring")  # base_ring, port_b, port_c
MAX_FPS = float(os.getenv("MAX_FPS", "20"))

REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=10.0, connect=5.0, sock_read=5.0)


async def get_session_id(session: aiohttp.ClientSession, token: str) -> str:
    request = {
        "jsonrpc": "2.0",
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "ws_led_test", "version": "1.0"}
        },
        "id": str(uuid.uuid4())
    }
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "Authorization": f"Bearer {token}"
    }
    async with session.post(GATEWAY_MCP_URL, json=request, headers=headers) as resp:
        session_id = resp.headers.get("Mcp-Session-Id")
        if not session_id:
            raise RuntimeError("No Mcp-Session-Id")
        return session_id


async def call_tool(session: aiohttp.ClientSession, token: str, session_id: str,
                    tool: str, args: dict, req_id: str) -> dict:
    request = {
        "jsonrpc": "2.0",
        "method": "tools/call",
        "params": {"name": tool, "arguments": args},
        "id": req_id
    }
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "Authorization": f"Bearer {token}",
        "Mcp-Session-Id": session_id
    }
    async with session.post(GATEWAY_MCP_URL, json=request, headers=headers, timeout=REQUEST_TIMEOUT) as resp:
        data = await resp.json()
        if "error" in data:
            raise RuntimeError(f"Tool error: {data['error']}")
        return data.get("result", {})


async def start_ws_stream(gateway_session: aiohttp.ClientSession, token: str, session_id: str) -> dict:
    """Call stackchan_follow_led_stream to start WebSocket streaming."""
    return await call_tool(gateway_session, token, session_id, "stackchan_follow_led_stream", {
        "action": "start",
        "url": WS_SERVER_URL,
        "target": TARGET,
        "max_fps": MAX_FPS,
        "color_order": "grb"
    }, str(uuid.uuid4()))


async def stop_ws_stream(gateway_session: aiohttp.ClientSession, token: str, session_id: str) -> dict:
    """Call stackchan_follow_led_stream to stop WebSocket streaming."""
    return await call_tool(gateway_session, token, session_id, "stackchan_follow_led_stream", {
        "action": "stop"
    }, str(uuid.uuid4()))


async def get_ws_status(gateway_session: aiohttp.ClientSession, token: str, session_id: str) -> dict:
    """Get WebSocket stream status."""
    return await call_tool(gateway_session, token, session_id, "stackchan_follow_led_stream", {
        "action": "status"
    }, str(uuid.uuid4()))


async def main() -> int:
    if not STACKCHAN_TOKEN:
        print("ERROR: Set STACKCHAN_TOKEN")
        return 1

    # Check if server is already running on port
    import socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_running = sock.connect_ex(('127.0.0.1', 8772)) == 0
    sock.close()
    
    ws_server = None
    if not server_running:
        # Start WebSocket server as subprocess
        print(f"Starting WebSocket server on ws://{HOST_IP}:8772...")
        ws_server = subprocess.Popen([
            sys.executable, "-m", "led_ws_server"
        ], cwd="/home/erika/projects/eidos/services/reflexd",
           env={**os.environ, "WS_HOST": "0.0.0.0", "WS_PORT": "8772"})
        # Give server time to start
        await asyncio.sleep(1.0)
    else:
        print(f"WebSocket server already running on port 8772")

    try:
        print("Connecting to gateway...")
        async with aiohttp.ClientSession(timeout=REQUEST_TIMEOUT) as http:
            session_id = await get_session_id(http, STACKCHAN_TOKEN)
            print(f"Session: {session_id}")

            print(f"Starting WebSocket stream to {WS_SERVER_URL} (target={TARGET})...")
            result = await start_ws_stream(http, STACKCHAN_TOKEN, session_id)
            print(f"Start result: {result}")

            # Wait a bit for connection to establish
            await asyncio.sleep(2.0)

            # Check status
            status = await get_ws_status(http, STACKCHAN_TOKEN, session_id)
            print(f"Status: {status}")

            print("\nStreaming... Press Ctrl+C to stop")
            try:
                while True:
                    await asyncio.sleep(5.0)
                    status = await get_ws_status(http, STACKCHAN_TOKEN, session_id)
                    print(f"Status: running={status.get('running')}, connected={status.get('connected')}, "
                          f"frames_sent={status.get('frames_sent')}, frames_dropped={status.get('frames_dropped')}")
            except KeyboardInterrupt:
                pass

            print("\nStopping stream...")
            result = await stop_ws_stream(http, STACKCHAN_TOKEN, session_id)
            print(f"Stop result: {result}")

    finally:
        if ws_server is not None:
            print("Stopping WebSocket server...")
            ws_server.terminate()
            try:
                ws_server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                ws_server.kill()
                ws_server.wait()
        else:
            print("WebSocket server was already running, leaving it alive")

    print("Done")
    return 0


if __name__ == "__main__":
    if not os.getenv("STACKCHAN_TOKEN"):
        print("ERROR: Set STACKCHAN_TOKEN environment variable")
        sys.exit(1)
    sys.exit(asyncio.run(main()))