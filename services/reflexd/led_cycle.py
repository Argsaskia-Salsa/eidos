#!/usr/bin/env python3
"""
Sequential LED cycle: 12 LEDs cycle 1→2→...→12→1→2...
Stable: connection reuse, timeouts, retry, graceful shutdown.
"""

import asyncio
import aiohttp
import os
import signal

# Configuration
GATEWAY_MCP_URL = os.getenv("GATEWAY_MCP_URL", "http://127.0.0.1:8767/mcp")
STACKCHAN_TOKEN = os.getenv("STACKCHAN_TOKEN", "")
SWITCH_DELAY = 0.1  # seconds between LEDs (configurable)

# Timeouts
REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=5.0, connect=2.0, sock_read=3.0)
MAX_RETRIES = 3
RETRY_BASE_DELAY = 0.1

# Global shutdown event
_shutdown_event = asyncio.Event()


def _setup_signals():
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _shutdown_event.set)
        except NotImplementedError:
            pass  # Windows


async def _call_with_retry(session: aiohttp.ClientSession, token: str, session_id: str,
                           tool: str, args: dict, req_id: int, max_retries: int = MAX_RETRIES) -> dict:
    """Call MCP tool with retry and exponential backoff."""
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

    for attempt in range(max_retries):
        try:
            async with session.post(
                GATEWAY_MCP_URL,
                json=request,
                headers=headers,
                timeout=REQUEST_TIMEOUT
            ) as resp:
                data = await resp.json()
                if "error" in data:
                    raise RuntimeError(f"Tool error: {data['error']}")
                return data.get("result", {})
        except (asyncio.TimeoutError, aiohttp.ClientError) as e:
            if attempt == max_retries - 1:
                raise
            await asyncio.sleep(RETRY_BASE_DELAY * (2 ** attempt))

    raise RuntimeError("Max retries exceeded")


async def get_session_id(token: str) -> str:
    async with aiohttp.ClientSession() as s:
        async with s.post(
            GATEWAY_MCP_URL,
            json={
                "jsonrpc": "2.0",
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "led_cycle", "version": "1.0"}
                },
                "id": 1
            },
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
                "Authorization": f"Bearer {token}"
            },
            timeout=REQUEST_TIMEOUT
        ) as resp:
            session_id = resp.headers.get("Mcp-Session-Id")
            if not session_id:
                raise RuntimeError("No Mcp-Session-Id")
            return session_id


async def main():
    if not STACKCHAN_TOKEN:
        print("ERROR: Set STACKCHAN_TOKEN")
        return

    _setup_signals()

    print("Connecting...")
    session_id = await get_session_id(STACKCHAN_TOKEN)
    print(f"Session: {session_id}")

    # Pre-generate patterns
    patterns = []
    for i in range(12):
        colors = [[0, 0, 0]] * 12
        colors[i] = [255, 255, 255]
        patterns.append(colors)

    # Single reused session for all requests
    connector = aiohttp.TCPConnector(limit=10, keepalive_timeout=30, enable_cleanup_closed=True)
    async with aiohttp.ClientSession(timeout=REQUEST_TIMEOUT, connector=connector) as http:
        print("Starting cycle (Ctrl+C to stop)...")
        req_id = 0
        try:
            while not _shutdown_event.is_set():
                for pattern in patterns:
                    if _shutdown_event.is_set():
                        break
                    await asyncio.sleep(0)
                    await _call_with_retry(http, STACKCHAN_TOKEN, session_id,
                                         "set_leds", {"colors": pattern}, req_id)
                    req_id += 1
                    await asyncio.sleep(SWITCH_DELAY)
        except asyncio.CancelledError:
            pass
        finally:
            # Clear all LEDs on exit (best effort, no retry)
            try:
                await http.post(
                    GATEWAY_MCP_URL,
                    json={"jsonrpc": "2.0", "method": "tools/call",
                          "params": {"name": "set_leds", "arguments": {"colors": [[0, 0, 0]] * 12}},
                          "id": 999},
                    headers={"Authorization": f"Bearer {STACKCHAN_TOKEN}",
                             "Mcp-Session-Id": session_id,
                             "Content-Type": "application/json",
                             "Accept": "application/json, text/event-stream"},
                    timeout=aiohttp.ClientTimeout(total=2.0)
                )
            except Exception:
                pass
            print("\nDone")


if __name__ == "__main__":
    asyncio.run(main())