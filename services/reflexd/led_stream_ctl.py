#!/usr/bin/env python3
"""
LED Stream Controller: manages stackchan_follow_led_stream via MCP
and controls led_stream_server via HTTP.
"""

import asyncio
import aiohttp
import os
import sys
import uuid
import argparse
import json


# Configuration
GATEWAY_MCP_URL = os.getenv("GATEWAY_MCP_URL", "http://127.0.0.1:8767/mcp")
STACKCHAN_TOKEN = os.getenv("STACKCHAN_TOKEN", "")
HOST_IP = os.getenv("HOST_IP", "192.168.x.x")
WS_SERVER_URL = os.getenv("WS_SERVER_URL", f"ws://{HOST_IP}:8772")
HTTP_SERVER_URL = os.getenv("HTTP_SERVER_URL", f"http://{HOST_IP}:8773")

TARGET = os.getenv("TARGET", "base_ring")
MAX_FPS = float(os.getenv("MAX_FPS", "20"))

REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=10.0, connect=5.0, sock_read=5.0)


# ===== MCP FUNCTIONS =====

async def mcp_initialize(session: aiohttp.ClientSession, token: str) -> str:
    async with session.post(
        GATEWAY_MCP_URL,
        json={
            "jsonrpc": "2.0",
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "led_stream_ctl", "version": "1.0"}
            },
            "id": str(uuid.uuid4())
        },
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Authorization": f"Bearer {token}"
        }
    ) as resp:
        session_id = resp.headers.get("Mcp-Session-Id")
        if not session_id:
            raise RuntimeError("No Mcp-Session-Id")
        return session_id


async def mcp_call(session: aiohttp.ClientSession, token: str, session_id: str,
                   tool: str, args: dict) -> dict:
    async with session.post(
        GATEWAY_MCP_URL,
        json={
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {"name": tool, "arguments": args},
            "id": str(uuid.uuid4())
        },
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Authorization": f"Bearer {token}",
            "Mcp-Session-Id": session_id
        },
        timeout=REQUEST_TIMEOUT
    ) as resp:
        data = await resp.json()
        if "error" in data:
            raise RuntimeError(f"MCP error: {data['error']}")
        return data.get("result", {})


async def stream_start(session: aiohttp.ClientSession, token: str, session_id: str) -> dict:
    return await mcp_call(session, token, session_id, "stackchan_follow_led_stream", {
        "action": "start",
        "url": WS_SERVER_URL,
        "target": TARGET,
        "max_fps": MAX_FPS,
        "color_order": "grb"
    })


async def stream_stop(session: aiohttp.ClientSession, token: str, session_id: str) -> dict:
    return await mcp_call(session, token, session_id, "stackchan_follow_led_stream", {
        "action": "stop"
    })


async def stream_status(session: aiohttp.ClientSession, token: str, session_id: str) -> dict:
    return await mcp_call(session, token, session_id, "stackchan_follow_led_stream", {
        "action": "status"
    })


# ===== HTTP CONTROL FUNCTIONS =====

async def http_get(session: aiohttp.ClientSession, path: str) -> dict:
    async with session.get(f"{HTTP_SERVER_URL}{path}") as resp:
        return await resp.json()


async def http_post(session: aiohttp.ClientSession, path: str, data: dict) -> dict:
    async with session.post(f"{HTTP_SERVER_URL}{path}", json=data) as resp:
        return await resp.json()


async def server_status(session: aiohttp.ClientSession) -> dict:
    return await http_get(session, "/status")


async def server_list_patterns(session: aiohttp.ClientSession) -> dict:
    return await http_get(session, "/patterns")


async def server_set_pattern(session: aiohttp.ClientSession, name: str, **args) -> dict:
    return await http_post(session, "/pattern", {"pattern": name, "args": args})


async def server_set_fps(session: aiohttp.ClientSession, fps: float) -> dict:
    return await http_post(session, "/fps", {"fps": fps})


async def server_set_brightness(session: aiohttp.ClientSession, brightness: float) -> dict:
    return await http_post(session, "/brightness", {"brightness": brightness})


# ===== COMMANDS =====

async def cmd_start(args):
    if not STACKCHAN_TOKEN:
        print("ERROR: Set STACKCHAN_TOKEN")
        return 1
    
    async with aiohttp.ClientSession(timeout=REQUEST_TIMEOUT) as http:
        session_id = await mcp_initialize(http, STACKCHAN_TOKEN)
        print(f"Session: {session_id}")
        
        result = await stream_start(http, STACKCHAN_TOKEN, session_id)
        print(f"Stream started: {json.dumps(result, indent=2)}")


async def cmd_stop(args):
    if not STACKCHAN_TOKEN:
        print("ERROR: Set STACKCHAN_TOKEN")
        return 1
    
    async with aiohttp.ClientSession(timeout=REQUEST_TIMEOUT) as http:
        session_id = await mcp_initialize(http, STACKCHAN_TOKEN)
        print(f"Session: {session_id}")
        
        result = await stream_stop(http, STACKCHAN_TOKEN, session_id)
        print(f"Stream stopped: {json.dumps(result, indent=2)}")


async def cmd_status(args):
    if not STACKCHAN_TOKEN:
        print("ERROR: Set STACKCHAN_TOKEN")
        return 1
    
    async with aiohttp.ClientSession(timeout=REQUEST_TIMEOUT) as http:
        session_id = await mcp_initialize(http, STACKCHAN_TOKEN)
        
        # Gateway stream status
        result = await stream_status(http, STACKCHAN_TOKEN, session_id)
        print("=== Gateway Stream Status ===")
        print(json.dumps(result, indent=2))
        
        # Server status
        try:
            srv = await server_status(http)
            print("\n=== LED Server Status ===")
            print(json.dumps(srv, indent=2))
        except Exception as e:
            print(f"\nLED Server: unavailable ({e})")


async def cmd_pattern(args):
    async with aiohttp.ClientSession(timeout=REQUEST_TIMEOUT) as http:
        # List patterns if no args
        if not args.name:
            result = await server_list_patterns(http)
            print("Available patterns:")
            for p in result["patterns"]:
                marker = " *" if p == result["current"] else ""
                print(f"  {p}{marker}")
            print(f"\nCurrent: {result['current']} {result['args']}")
            return 0
        
        # Set pattern
        result = await server_set_pattern(http, args.name, **args.args)
        print(f"Pattern set: {json.dumps(result, indent=2)}")


async def cmd_fps(args):
    async with aiohttp.ClientSession(timeout=REQUEST_TIMEOUT) as http:
        result = await server_set_fps(http, args.fps)
        print(f"FPS set: {json.dumps(result, indent=2)}")


async def cmd_brightness(args):
    async with aiohttp.ClientSession(timeout=REQUEST_TIMEOUT) as http:
        result = await server_set_brightness(http, args.brightness)
        print(f"Brightness set: {json.dumps(result, indent=2)}")


async def cmd_monitor(args):
    if not STACKCHAN_TOKEN:
        print("ERROR: Set STACKCHAN_TOKEN")
        return 1
    
    async with aiohttp.ClientSession(timeout=REQUEST_TIMEOUT) as http:
        session_id = await mcp_initialize(http, STACKCHAN_TOKEN)
        print(f"Session: {session_id}")
        print("Monitoring... Ctrl+C to stop\n")
        
        try:
            while True:
                result = await stream_status(http, STACKCHAN_TOKEN, session_id)
                try:
                    # Parse nested JSON from MCP response
                    content = result.get("content", [{}])[0].get("text", "{}")
                    status = json.loads(content)
                    
                    print(f"\rConnected: {status.get('connected')} | "
                          f"State: {status.get('connect_state')} | "
                          f"Sent: {status.get('frames_sent')} | "
                          f"Dropped: {status.get('frames_dropped')} | "
                          f"Error: {status.get('last_error') or 'none'}", end="")
                except:
                    print(f"\rStatus: {result}", end="")
                
                await asyncio.sleep(args.interval)
        except KeyboardInterrupt:
            print("\nStopped.")


# ===== MAIN =====

def main():
    parser = argparse.ArgumentParser(description="LED Stream Controller")
    sub = parser.add_subparsers(dest="command", required=True)
    
    # MCP stream commands
    sub.add_parser("start", help="Start WebSocket stream via gateway")
    sub.add_parser("stop", help="Stop WebSocket stream")
    sub.add_parser("status", help="Show stream and server status")
    p_mon = sub.add_parser("monitor", help="Monitor stream status continuously")
    p_mon.add_argument("-i", "--interval", type=float, default=2.0, help="Poll interval (sec)")
    
    # HTTP server control
    p_pat = sub.add_parser("pattern", help="List or set pattern")
    p_pat.add_argument("name", nargs="?", help="Pattern name (cycle, rainbow, breathing, kitt, solid, off)")
    p_pat.add_argument("--color", nargs=3, type=int, metavar=("R","G","B"), help="Color for solid/kitt/breathing")
    p_pat.add_argument("--tail", type=float, help="Tail fade for kitt")
    
    p_fps = sub.add_parser("fps", help="Set FPS (1-30)")
    p_fps.add_argument("fps", type=float)
    
    p_bri = sub.add_parser("brightness", help="Set brightness (0.0-1.0)")
    p_bri.add_argument("brightness", type=float)
    
    args = parser.parse_args()
    
    # Process pattern args
    if args.command == "pattern" and args.name:
        pattern_args = {}
        if args.color:
            pattern_args["color"] = args.color
        if args.tail:
            pattern_args["tail"] = args.tail
        args.args = pattern_args
    
    # Run command
    cmd_map = {
        "start": cmd_start,
        "stop": cmd_stop,
        "status": cmd_status,
        "monitor": cmd_monitor,
        "pattern": cmd_pattern,
        "fps": cmd_fps,
        "brightness": cmd_brightness,
    }
    
    return asyncio.run(cmd_map[args.command](args))


if __name__ == "__main__":
    if not os.getenv("STACKCHAN_TOKEN") and sys.argv[1] in ("start", "stop", "status", "monitor"):
        print("ERROR: Set STACKCHAN_TOKEN environment variable")
        sys.exit(1)
    sys.exit(main())