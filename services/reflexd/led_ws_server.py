#!/usr/bin/env python3
"""
WebSocket LED Stream Server for StackChan.
Streams LED patterns to gateway via WebSocket.
Gateway connects using: stackchan_follow_led_stream(action="start", url="ws://host:port", target="base_ring")
"""

import asyncio
import json
import time
import os
import signal
import websockets
from typing import AsyncGenerator


# Configuration
WS_HOST = os.getenv("WS_HOST", "0.0.0.0")
WS_PORT = int(os.getenv("WS_PORT", "8772"))
NUM_LEDS = 12
FPS = 20
SWITCH_DELAY = 1.0 / FPS


# Patterns: sequential 1->2->...->12->1...
def generate_patterns() -> list[list[list[int]]]:
    patterns = []
    for i in range(NUM_LEDS):
        colors = [[0, 0, 0] for _ in range(NUM_LEDS)]
        colors[i] = [255, 255, 255]
        patterns.append(colors)
    return patterns


PATTERNS = generate_patterns()


async def led_frame_generator() -> AsyncGenerator[str, None]:
    """Generate LED frames as JSON strings."""
    pattern_idx = 0
    while True:
        frame = {
            "ts": time.time(),
            "kind": "continuous",
            "colors": PATTERNS[pattern_idx]
        }
        yield json.dumps(frame)
        pattern_idx = (pattern_idx + 1) % len(PATTERNS)
        await asyncio.sleep(SWITCH_DELAY)


async def handler(websocket):
    """Handle WebSocket connection from gateway."""
    client_ip = websocket.remote_address[0] if websocket.remote_address else "unknown"
    print(f"Gateway connected from {client_ip}")
    frame_count = 0
    
    try:
        async for frame_json in led_frame_generator():
            await websocket.send(frame_json)
            frame_count += 1
            if frame_count % 20 == 0:
                print(f"Sent {frame_count} frames")
    except websockets.exceptions.ConnectionClosed as e:
        print(f"Gateway disconnected from {client_ip} (code={e.code}, reason={e.reason})")
    except Exception as e:
        print(f"Error with {client_ip}: {e}")


async def main():
    # Setup signal handling
    shutdown_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, shutdown_event.set)
        except NotImplementedError:
            pass

    print(f"Starting WebSocket LED server on ws://{WS_HOST}:{WS_PORT}")
    print(f"Patterns: {NUM_LEDS} LEDs, {FPS} FPS, sequential cycle")
    print("Waiting for gateway to connect via stackchan_follow_led_stream...")
    print()

    async with websockets.serve(handler, WS_HOST, WS_PORT):
        await shutdown_event.wait()
    
    print("\nServer stopped")


if __name__ == "__main__":
    asyncio.run(main())