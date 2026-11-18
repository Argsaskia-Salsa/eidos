#!/usr/bin/env python3
"""
Minimal LED test: LED 0 red for 1s, then LED 1 green
"""

import asyncio
import aiohttp
import os

GATEWAY_MCP_URL = os.getenv("GATEWAY_MCP_URL", "http://127.0.0.1:8767/mcp")
STACKCHAN_TOKEN = os.getenv("STACKCHAN_TOKEN", "")

async def get_session_id(token: str) -> str:
    request = {
        "jsonrpc": "2.0",
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "led_test", "version": "1.0"}
        },
        "id": 1
    }
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "Authorization": f"Bearer {token}"
    }
    async with aiohttp.ClientSession().post(GATEWAY_MCP_URL, json=request, headers=headers) as resp:
        session_id = resp.headers.get("Mcp-Session-Id")
        if not session_id:
            raise RuntimeError("No Mcp-Session-Id in response")
        return session_id

async def call_tool(token: str, session_id: str, tool: str, args: dict, req_id: int) -> dict:
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
    async with aiohttp.ClientSession().post(GATEWAY_MCP_URL, json=request, headers=headers) as resp:
        data = await resp.json()
        if "error" in data:
            raise RuntimeError(f"Tool error: {data['error']}")
        return data.get("result", {})

async def main():
    if not os.getenv("STACKCHAN_TOKEN"):
        print("ERROR: Set STACKCHAN_TOKEN environment variable")
        return

    token = os.getenv("STACKCHAN_TOKEN")
    
    print("Getting session...")
    session_id = await get_session_id(token)
    print(f"Session: {session_id}")

    while True:
        # LED 0 RED
        print("LED 0 RED...")
        await call_tool(token, session_id, "set_leds", {"colors": [[255, 0, 0], [0, 0, 0]]}, 1)
        print("LED 0 RED ON")

        await asyncio.sleep(0.01)

        # LED 0 OFF, LED 1 GREEN
        print("LED 0 OFF, LED 1 GREEN...")
        await call_tool(token, session_id, "set_leds", {"colors": [[0, 0, 0], [0, 255, 0]]}, 2)
        print("LED 0 OFF, LED 1 GREEN ON")

        await asyncio.sleep(0.01)
    
    print("Done")

async def call_tool(token: str, session_id: str, tool: str, args: dict, req_id: int) -> dict:
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
    async with aiohttp.ClientSession().post(
        os.getenv("GATEWAY_MCP_URL", "http://127.0.0.1:8767/mcp"),
        json=request, headers=headers
    ) as resp:
        data = await resp.json()
        if "error" in data:
            raise RuntimeError(f"Tool error: {data['error']}")
        return data.get("result", {})

if __name__ == "__main__":
    import asyncio
    import aiohttp
    import os
    asyncio.run(main())
