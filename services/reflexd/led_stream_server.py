#!/usr/bin/env python3
"""
LED Stream Server: WebSocket (for gateway) + HTTP (for control).
State stored in server. Controls: pattern, FPS, palette, brightness.
"""

import asyncio
import json
import os
import time
import signal
from typing import AsyncGenerator, Optional
from aiohttp import web
import websockets


# Configuration
WS_HOST = os.getenv("WS_HOST", "0.0.0.0")
WS_PORT = int(os.getenv("WS_PORT", "8772"))
HTTP_PORT = int(os.getenv("HTTP_PORT", "8773"))
NUM_LEDS = 12
DEFAULT_FPS = 20


# ===== PATTERNS =====

def pattern_cycle(num_leds: int = NUM_LEDS) -> list[list[list[int]]]:
    """Sequential 1→2→...→N→1..."""
    patterns = []
    for i in range(num_leds):
        colors = [[0, 0, 0] for _ in range(num_leds)]
        colors[i] = [255, 255, 255]
        patterns.append(colors)
    return patterns


def pattern_rainbow(num_leds: int = NUM_LEDS) -> list[list[list[int]]]:
    """Flowing rainbow wave."""
    import math
    patterns = []
    frames = 60
    for frame in range(frames):
        colors = []
        for i in range(num_leds):
            hue = (frame / frames + i / num_leds) % 1.0
            h = hue * 6.0
            x = int(h)
            f = h - x
            p = 1.0 * (1 - 1.0)
            q = 1.0 * (1 - 1.0 * f)
            t = 1.0 * (1 - 1.0 * (1 - f))
            if x == 0:
                r, g, b = 1.0, t, p
            elif x == 1:
                r, g, b = q, 1.0, p
            elif x == 2:
                r, g, b = p, 1.0, t
            elif x == 3:
                r, g, b = p, q, 1.0
            elif x == 4:
                r, g, b = t, p, 1.0
            else:
                r, g, b = 1.0, p, q
            colors.append([int(r * 255), int(g * 255), int(b * 255)])
        patterns.append(colors)
    return patterns


def pattern_breathing(num_leds: int = NUM_LEDS, color: list[int] = [0, 180, 255]) -> list[list[list[int]]]:
    """All LEDs breathe together."""
    import math
    patterns = []
    frames = 60
    for frame in range(frames):
        brightness = (math.sin(frame * 2 * math.pi / frames) + 1) / 2  # 0..1
        colors = []
        for _ in range(num_leds):
            colors.append([int(c * brightness) for c in color])
        patterns.append(colors)
    return patterns


def pattern_kitt(num_leds: int = NUM_LEDS, color: list[int] = [255, 0, 0], tail: float = 0.5) -> list[list[list[int]]]:
    """KITT scanner back and forth."""
    patterns = []
    # Forward
    for pos in range(num_leds):
        colors = [[0, 0, 0] for _ in range(num_leds)]
        for i in range(num_leds):
            dist = abs(i - pos)
            if dist == 0:
                colors[i] = color[:]
            elif dist <= 3:
                fade = tail ** dist
                colors[i] = [int(c * fade) for c in color]
        patterns.append(colors)
    # Backward
    for pos in range(num_leds - 2, 0, -1):
        colors = [[0, 0, 0] for _ in range(num_leds)]
        for i in range(num_leds):
            dist = abs(i - pos)
            if dist == 0:
                colors[i] = color[:]
            elif dist <= 3:
                fade = tail ** dist
                colors[i] = [int(c * fade) for c in color]
        patterns.append(colors)
    return patterns


def pattern_solid(num_leds: int = NUM_LEDS, color: list[int] = [255, 255, 255]) -> list[list[list[int]]]:
    """Single solid color frame."""
    return [[color[:] for _ in range(num_leds)]]


def pattern_off(num_leds: int = NUM_LEDS) -> list[list[list[int]]]:
    """All off."""
    return [[[0, 0, 0] for _ in range(num_leds)]]


# Pattern registry
PATTERNS = {
    "cycle": pattern_cycle,
    "rainbow": pattern_rainbow,
    "breathing": pattern_breathing,
    "kitt": pattern_kitt,
    "solid": pattern_solid,
    "off": pattern_off,
}


# ===== SERVER STATE =====

class ServerState:
    def __init__(self):
        self.pattern_name = "cycle"
        self.pattern_args = {}
        self.fps = DEFAULT_FPS
        self.brightness = 1.0
        self._pattern_cache: Optional[list] = None
        self._cache_key = None
        self.frame_count = 0
        self.connected_clients = 0
        self.start_time = time.time()
    
    def get_pattern(self) -> list:
        key = (self.pattern_name, json.dumps(self.pattern_args, sort_keys=True))
        if self._cache_key != key:
            func = PATTERNS.get(self.pattern_name, pattern_cycle)
            self._pattern_cache = func(NUM_LEDS, **self.pattern_args)
            self._cache_key = key
            self.frame_count = 0
        return self._pattern_cache or []
    
    def set_pattern(self, name: str, **args):
        if name not in PATTERNS:
            raise ValueError(f"Unknown pattern: {name}. Available: {list(PATTERNS.keys())}")
        self.pattern_name = name
        self.pattern_args = args
    
    def set_fps(self, fps: float):
        self.fps = max(1, min(30, fps))
    
    def set_brightness(self, b: float):
        self.brightness = max(0.0, min(1.0, b))
    
    def apply_brightness(self, colors: list) -> list:
        if self.brightness >= 1.0:
            return colors
        return [[[int(c * self.brightness) for c in led] for led in frame] for frame in colors]
    
    def status(self) -> dict:
        return {
            "pattern": self.pattern_name,
            "pattern_args": self.pattern_args,
            "fps": self.fps,
            "brightness": self.brightness,
            "frames_in_pattern": len(self.get_pattern()) if self._pattern_cache else 0,
            "frame_index": self.frame_count,
            "connected_clients": self.connected_clients,
            "uptime_seconds": time.time() - self.start_time,
        }


STATE = ServerState()


# ===== WEBSOCKET HANDLER (for gateway) =====

async def ws_handler(websocket):
    STATE.connected_clients += 1
    client_ip = websocket.remote_address[0] if websocket.remote_address else "unknown"
    print(f"Gateway connected from {client_ip} (clients: {STATE.connected_clients})")
    
    try:
        pattern = STATE.get_pattern()
        frame_idx = 0
        frame_interval = 1.0 / STATE.fps
        
        while True:
            # Check if pattern/FPS changed
            current_pattern = STATE.get_pattern()
            if current_pattern is not pattern or abs(1.0/STATE.fps - frame_interval) > 0.001:
                pattern = current_pattern
                frame_idx = 0
                frame_interval = 1.0 / STATE.fps
            
            if not pattern:
                await asyncio.sleep(0.1)
                continue
            
            frame_colors = STATE.apply_brightness(pattern)[frame_idx]
            
            frame = {
                "ts": time.time(),
                "kind": "continuous",
                "colors": frame_colors
            }
            
            await websocket.send(json.dumps(frame))
            frame_idx = (frame_idx + 1) % len(pattern)
            STATE.frame_count = frame_idx
            
            await asyncio.sleep(frame_interval)
            
    except websockets.exceptions.ConnectionClosed as e:
        print(f"Gateway disconnected from {client_ip} (code={e.code})")
    except Exception as e:
        print(f"WS error with {client_ip}: {e}")
    finally:
        STATE.connected_clients -= 1


# ===== HTTP CONTROL ENDPOINTS =====

async def http_status(request):
    return web.json_response(STATE.status())


async def http_set_pattern(request):
    try:
        data = await request.json()
        name = data.get("pattern")
        args = data.get("args", {})
        STATE.set_pattern(name, **args)
        return web.json_response({"ok": True, "status": STATE.status()})
    except ValueError as e:
        return web.json_response({"ok": False, "error": str(e)}, status=400)
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=500)


async def http_set_fps(request):
    try:
        data = await request.json()
        fps = float(data.get("fps", DEFAULT_FPS))
        STATE.set_fps(fps)
        return web.json_response({"ok": True, "status": STATE.status()})
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=400)


async def http_set_brightness(request):
    try:
        data = await request.json()
        b = float(data.get("brightness", 1.0))
        STATE.set_brightness(b)
        return web.json_response({"ok": True, "status": STATE.status()})
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=400)


async def http_list_patterns(request):
    return web.json_response({
        "patterns": list(PATTERNS.keys()),
        "current": STATE.pattern_name,
        "args": STATE.pattern_args
    })


# ===== MAIN =====

async def start_servers():
    # HTTP server
    app = web.Application()
    app.router.add_get("/status", http_status)
    app.router.add_post("/pattern", http_set_pattern)
    app.router.add_post("/fps", http_set_fps)
    app.router.add_post("/brightness", http_set_brightness)
    app.router.add_get("/patterns", http_list_patterns)
    
    runner = web.AppRunner(app)
    await runner.setup()
    http_site = web.TCPSite(runner, WS_HOST, HTTP_PORT)
    await http_site.start()
    
    # WebSocket server
    ws_server = await websockets.serve(ws_handler, WS_HOST, WS_PORT)
    
    print(f"LED Stream Server running:")
    print(f"  WebSocket (gateway): ws://{WS_HOST}:{WS_PORT}")
    print(f"  HTTP (control):      http://{WS_HOST}:{HTTP_PORT}")
    print(f"  Patterns: {list(PATTERNS.keys())}")
    print()
    
    # Wait for shutdown
    shutdown_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, shutdown_event.set)
        except NotImplementedError:
            pass
    
    await shutdown_event.wait()
    
    ws_server.close()
    await ws_server.wait_closed()
    await runner.cleanup()
    print("Server stopped")


if __name__ == "__main__":
    asyncio.run(start_servers())