"""
Integration tests for LED Stream - full cycle: server + WS + HTTP + MCP + hot-reload.
"""

import asyncio
import json
import tempfile
from pathlib import Path

import pytest
import aiohttp
import websockets

from reflexd.streams.led import (
    LEDStreamServer,
    LEDPatternRegistry,
    LEDStreamClient,
    PATTERNS,
    reload_all_patterns,
)
from reflexd.streams.led.config import CONFIG


async def create_test_server():
    """Create and start LED stream server for testing."""
    server = LEDStreamServer()
    # Use test ports
    server.config.WS_PORT = 18772
    server.config.HTTP_PORT = 18773
    server.config.WS_HOST = "127.0.0.1"
    
    # Start server in background
    task = asyncio.create_task(server.start())
    
    # Wait for server to start
    await asyncio.sleep(0.5)
    
    return server, task


async def stop_test_server(server, task):
    """Stop test server and cleanup."""
    await server.stop()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


class TestLEDPatternRegistry:
    """Tests for LEDPatternRegistry (no server needed)."""
    
    def test_builtin_patterns_loaded(self):
        """Test all builtin patterns are loaded."""
        assert "cycle" in PATTERNS
        assert "rainbow" in PATTERNS
        assert "breathing" in PATTERNS
        assert "kitt" in PATTERNS
        assert "solid" in PATTERNS
        assert "off" in PATTERNS
    
    def test_pattern_functions_return_frames(self):
        """Test pattern functions return valid frames."""
        for name, func in PATTERNS.items():
            frames = func(CONFIG.NUM_LEDS)
            assert isinstance(frames, list)
            assert len(frames) > 0
            for frame in frames:
                assert isinstance(frame, list)
                assert len(frame) == CONFIG.NUM_LEDS
                for led in frame:
                    assert isinstance(led, list)
                    assert len(led) == 3
                    for c in led:
                        assert 0 <= c <= 255
    
    def test_reload_all_patterns(self):
        """Test reload_all_patterns works."""
        patterns_before = list(PATTERNS.keys())
        patterns_after = reload_all_patterns()
        assert set(patterns_before) == set(patterns_after)


class TestLEDStreamIntegration:
    """Integration tests for LED stream full cycle with running server."""
    
    @pytest.mark.asyncio
    async def test_server_status_endpoint(self):
        """Test /status endpoint returns correct state."""
        server, task = await create_test_server()
        try:
            async with aiohttp.ClientSession() as session:
                url = f"http://127.0.0.1:{server.config.HTTP_PORT}/status"
                async with session.get(url) as resp:
                    assert resp.status == 200
                    data = await resp.json()
                    assert data["pattern"] == "cycle"  # default pattern
                    assert data["fps"] == CONFIG.DEFAULT_FPS
                    assert data["brightness"] == CONFIG.DEFAULT_BRIGHTNESS
        finally:
            await stop_test_server(server, task)
    
    @pytest.mark.asyncio
    async def test_set_pattern_via_http(self):
        """Test setting pattern via HTTP POST /pattern."""
        server, task = await create_test_server()
        try:
            async with aiohttp.ClientSession() as session:
                url = f"http://127.0.0.1:{server.config.HTTP_PORT}/pattern"
                async with session.post(url, json={"pattern": "rainbow", "args": {}}) as resp:
                    assert resp.status == 200
                    data = await resp.json()
                    assert data["ok"] is True
                    assert data["status"]["pattern"] == "rainbow"
        finally:
            await stop_test_server(server, task)
    
    @pytest.mark.asyncio
    async def test_set_pattern_with_args(self):
        """Test setting pattern with arguments."""
        server, task = await create_test_server()
        try:
            async with aiohttp.ClientSession() as session:
                url = f"http://127.0.0.1:{server.config.HTTP_PORT}/pattern"
                async with session.post(url, json={
                    "pattern": "breathing", 
                    "args": {"color": [255, 0, 0]}
                }) as resp:
                    assert resp.status == 200
                    data = await resp.json()
                    assert data["ok"] is True
                    assert data["status"]["pattern"] == "breathing"
                    assert data["status"]["pattern_args"]["color"] == [255, 0, 0]
        finally:
            await stop_test_server(server, task)
    
    @pytest.mark.asyncio
    async def test_set_invalid_pattern(self):
        """Test setting invalid pattern returns 400."""
        server, task = await create_test_server()
        try:
            async with aiohttp.ClientSession() as session:
                url = f"http://127.0.0.1:{server.config.HTTP_PORT}/pattern"
                async with session.post(url, json={"pattern": "nonexistent", "args": {}}) as resp:
                    assert resp.status == 400
                    data = await resp.json()
                    assert data["ok"] is False
        finally:
            await stop_test_server(server, task)
    
    @pytest.mark.asyncio
    async def test_set_fps(self):
        """Test setting FPS via HTTP."""
        server, task = await create_test_server()
        try:
            async with aiohttp.ClientSession() as session:
                url = f"http://127.0.0.1:{server.config.HTTP_PORT}/fps"
                async with session.post(url, json={"fps": 30}) as resp:
                    assert resp.status == 200
                    data = await resp.json()
                    assert data["ok"] is True
                    assert data["status"]["fps"] == 30
        finally:
            await stop_test_server(server, task)
    
    @pytest.mark.asyncio
    async def test_set_brightness(self):
        """Test setting brightness via HTTP."""
        server, task = await create_test_server()
        try:
            async with aiohttp.ClientSession() as session:
                url = f"http://127.0.0.1:{server.config.HTTP_PORT}/brightness"
                async with session.post(url, json={"brightness": 0.5}) as resp:
                    assert resp.status == 200
                    data = await resp.json()
                    assert data["ok"] is True
                    assert data["status"]["brightness"] == 0.5
        finally:
            await stop_test_server(server, task)
    
    @pytest.mark.asyncio
    async def test_list_patterns(self):
        """Test listing patterns via HTTP."""
        server, task = await create_test_server()
        try:
            async with aiohttp.ClientSession() as session:
                url = f"http://127.0.0.1:{server.config.HTTP_PORT}/patterns"
                async with session.get(url) as resp:
                    assert resp.status == 200
                    data = await resp.json()
                    assert "patterns" in data
                    assert data["current"] == "cycle"
                    pattern_names = [p["name"] for p in data["patterns"]]
                    assert "cycle" in pattern_names
                    assert "rainbow" in pattern_names
                    assert "breathing" in pattern_names
                    assert "kitt" in pattern_names
                    assert "solid" in pattern_names
                    assert "off" in pattern_names
        finally:
            await stop_test_server(server, task)
    
    @pytest.mark.asyncio
    async def test_ws_frame_format(self):
        """Test WebSocket frame format matches stackchan_follow_led_stream expectations."""
        server, task = await create_test_server()
        try:
            uri = f"ws://127.0.0.1:{server.config.WS_PORT}"
            async with websockets.connect(uri) as ws:
                frame_json = await ws.recv()
                frame = json.loads(frame_json)
                
                # Check required fields
                assert "ts" in frame
                assert frame["kind"] == "continuous"
                assert "colors" in frame
                assert isinstance(frame["colors"], list)
                assert len(frame["colors"]) == CONFIG.NUM_LEDS
                for led in frame["colors"]:
                    assert isinstance(led, list)
                    assert len(led) == 3  # RGB
                    for c in led:
                        assert 0 <= c <= 255
        finally:
            await stop_test_server(server, task)
    
    @pytest.mark.asyncio
    async def test_custom_pattern_hot_reload(self):
        """Test hot-reload of custom patterns without server restart."""
        server, task = await create_test_server()
        try:
            # Use the actual custom directory from the registry
            custom_dir = LEDPatternRegistry.custom_dir
            custom_dir.mkdir(parents=True, exist_ok=True)
            
            # Write custom pattern
            pattern_content = '''
from reflexd.streams.led.registry import LEDPatternSpec

def test_custom(num_leds=12, **kwargs):
    return [[[100, 100, 100] for _ in range(num_leds)]]

PATTERN = LEDPatternSpec(
    name="test_custom",
    func=test_custom,
    args={},
    description="Test custom pattern"
)
'''
            (custom_dir / "test_custom.py").write_text(pattern_content)
            
            try:
                async with aiohttp.ClientSession() as session:
                    # Reload patterns
                    url = f"http://127.0.0.1:{server.config.HTTP_PORT}/patterns/reload"
                    async with session.post(url, json={}) as resp:
                        assert resp.status == 200
                        data = await resp.json()
                        assert data["ok"] is True
                        assert "test_custom" in data["patterns"]
                    
                    # Verify pattern is available
                    url = f"http://127.0.0.1:{server.config.HTTP_PORT}/patterns"
                    async with session.get(url) as resp:
                        data = await resp.json()
                        pattern_names = [p["name"] for p in data["patterns"]]
                        assert "test_custom" in pattern_names
                    
                    # Set the custom pattern
                    url = f"http://127.0.0.1:{server.config.HTTP_PORT}/pattern"
                    async with session.post(url, json={"pattern": "test_custom", "args": {}}) as resp:
                        assert resp.status == 200
                        data = await resp.json()
                        assert data["status"]["pattern"] == "test_custom"
            finally:
                # Clean up
                (custom_dir / "test_custom.py").unlink(missing_ok=True)
                reload_all_patterns()
        finally:
            await stop_test_server(server, task)


class TestLEDStreamClient:
    """Tests for LEDStreamClient (requires running server)."""
    
    @pytest.mark.asyncio
    async def test_client_status(self):
        """Test client status method."""
        server, task = await create_test_server()
        try:
            client = LEDStreamClient(
                http_url=f"http://127.0.0.1:{server.config.HTTP_PORT}"
            )
            async with client:
                status = await client.status()
                assert "pattern" in status
                assert "fps" in status
                assert "brightness" in status
        finally:
            await stop_test_server(server, task)
    
    @pytest.mark.asyncio
    async def test_client_set_pattern(self):
        """Test client set_pattern method."""
        server, task = await create_test_server()
        try:
            client = LEDStreamClient(
                http_url=f"http://127.0.0.1:{server.config.HTTP_PORT}"
            )
            async with client:
                result = await client.set_pattern("rainbow")
                assert result["ok"] is True
                assert result["status"]["pattern"] == "rainbow"
        finally:
            await stop_test_server(server, task)
    
    @pytest.mark.asyncio
    async def test_client_set_fps(self):
        """Test client set_fps method."""
        server, task = await create_test_server()
        try:
            client = LEDStreamClient(
                http_url=f"http://127.0.0.1:{server.config.HTTP_PORT}"
            )
            async with client:
                result = await client.set_fps(30)
                assert result["ok"] is True
                assert result["status"]["fps"] == 30
        finally:
            await stop_test_server(server, task)
    
    @pytest.mark.asyncio
    async def test_client_set_brightness(self):
        """Test client set_brightness method."""
        server, task = await create_test_server()
        try:
            client = LEDStreamClient(
                http_url=f"http://127.0.0.1:{server.config.HTTP_PORT}"
            )
            async with client:
                result = await client.set_brightness(0.5)
                assert result["ok"] is True
                assert result["status"]["brightness"] == 0.5
        finally:
            await stop_test_server(server, task)
    
    @pytest.mark.asyncio
    async def test_client_list_patterns(self):
        """Test client list_patterns method."""
        server, task = await create_test_server()
        try:
            client = LEDStreamClient(
                http_url=f"http://127.0.0.1:{server.config.HTTP_PORT}"
            )
            async with client:
                result = await client.list_patterns()
                assert "patterns" in result
                pattern_names = [p["name"] for p in result["patterns"]]
                assert "cycle" in pattern_names
        finally:
            await stop_test_server(server, task)
    
    @pytest.mark.asyncio
    async def test_client_reload_patterns(self):
        """Test client reload_patterns method."""
        server, task = await create_test_server()
        try:
            client = LEDStreamClient(
                http_url=f"http://127.0.0.1:{server.config.HTTP_PORT}"
            )
            async with client:
                result = await client.reload_patterns()
                assert result["ok"] is True
        finally:
            await stop_test_server(server, task)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])