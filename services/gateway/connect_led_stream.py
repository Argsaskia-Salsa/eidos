#!/usr/bin/env python3
"""Auto-connect gateway to LED Stream Server on startup."""
import asyncio
import aiohttp
import os
import sys

async def connect():
    token = os.getenv('STACKCHAN_TOKEN')
    print(f"DEBUG: token={'set' if token else 'NOT SET'}", file=sys.stderr)
    url = 'http://127.0.0.1:8767/mcp'
    
    # Wait for gateway MCP HTTP to be ready
    for attempt in range(30):
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(f'http://127.0.0.1:8767/healthz', timeout=aiohttp.ClientTimeout(total=1)) as r:
                    if r.status == 200:
                        print(f"DEBUG: healthz ok at attempt {attempt}", file=sys.stderr)
                        break
        except Exception as e:
            print(f"DEBUG: healthz attempt {attempt} failed: {e}", file=sys.stderr)
            pass
        await asyncio.sleep(0.5)
    
    async with aiohttp.ClientSession() as s:
        r = await s.post(url, json={
            'jsonrpc': '2.0',
            'method': 'initialize',
            'params': {
                'protocolVersion': '2024-11-05',
                'capabilities': {},
                'clientInfo': {'name': 'gateway-autostart', 'version': '1.0'}
            },
            'id': 1
        }, headers={
            'Authorization': f'Bearer {token}',
            'Content-Type': 'application/json',
            'Accept': 'application/json, text/event-stream'
        })
        print(f"DEBUG: initialize status={r.status}", file=sys.stderr)
        sid = r.headers.get('Mcp-Session-Id')
        if not sid:
            text = await r.text()
            print(f"DEBUG: initialize failed: {text}", file=sys.stderr)
            print("Failed to get session ID", file=sys.stderr)
            return
        
        print(f"DEBUG: got session_id={sid}", file=sys.stderr)
        
        # Wait for ESP32 to be connected
        for attempt in range(60):
            resp = await s.post(url, json={
                'jsonrpc': '2.0',
                'method': 'tools/call',
                'params': {'name': 'get_status', 'arguments': {}},
                'id': 2
            }, headers={
                'Authorization': f'Bearer {token}',
                'Content-Type': 'application/json',
                'Accept': 'application/json',
                'Mcp-Session-Id': sid
            })
            if resp.status == 200:
                import json
                text = await resp.text()
                data = json.loads(text)
                result_text = data.get('result', {}).get('content', [{}])[0].get('text', '{}')
                status = json.loads(result_text)
                if status.get('connected'):
                    print(f"DEBUG: ESP32 connected at attempt {attempt}", file=sys.stderr)
                    break
            await asyncio.sleep(1)
        else:
            print("DEBUG: ESP32 never connected", file=sys.stderr)
            return
        
        # Start LED stream
        resp2 = await s.post(url, json={
            'jsonrpc': '2.0',
            'method': 'tools/call',
            'params': {
                'name': 'stackchan_follow_led_stream',
                'arguments': {
                    'action': 'start',
                    'url': 'ws://192.168.2.145:8772',
                    'target': 'base_ring',
                    'max_fps': 30,
                    'color_order': 'grb'
                }
            },
            'id': 3
        }, headers={
            'Authorization': f'Bearer {token}',
            'Content-Type': 'application/json',
            'Accept': 'application/json',
            'Mcp-Session-Id': sid
        })
        print(f"DEBUG: start status={resp2.status}", file=sys.stderr)
        text = await resp2.text()
        print(f"DEBUG: start response={text}", file=sys.stderr)
        print("LED stream connected")

asyncio.run(connect())
