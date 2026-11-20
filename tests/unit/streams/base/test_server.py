"""
Unit tests for StreamServer base class (state machine, caching, WS frame loop).
"""

import asyncio
import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from reflexd.streams.base.registry import StreamRegistry, SpecProtocol
from reflexd.streams.base.server import StreamServer, ServerConfig


class DummySpec:
    def __init__(self, name: str, func, args: dict = None, description: str = ""):
        self.name = name
        self.func = func
        self.args = args or {}
        self.description = description


def pattern_func_a(**kwargs):
    return [[[1, 2, 3], [4, 5, 6]], [[7, 8, 9], [10, 11, 12]]]  # 2 frames, 2 LEDs


def pattern_func_b(**kwargs):
    return [[[100, 100, 100]]]  # 1 frame, 1 LED


class TestRegistry(StreamRegistry):
    custom_dir = __import__("pathlib").Path("/nonexistent")
    module_prefix = "test"
    spec_attr = "PATTERN"
    _BUILTIN_SPECS = {
        "pattern_a": DummySpec("pattern_a", pattern_func_a, {}, "Pattern A"),
        "pattern_b": DummySpec("pattern_b", pattern_func_b, {"param": 1}, "Pattern B"),
    }


class TestConfig(ServerConfig):
    WS_HOST = "127.0.0.1"
    WS_PORT = 9999
    HTTP_PORT = 9998
    DEFAULT_FPS = 10.0
    DEFAULT_INTENSITY = 1.0


class TestStreamServer(StreamServer):
    element_name = "pattern"
    intensity_name = "brightness"
    registry = TestRegistry
    
    def get_config(self) -> ServerConfig:
        return TestConfig()
    
    def make_frame(self, element_data: list, intensity: float) -> dict:
        return {"ts": time.time(), "kind": "test", "colors": element_data}
    
    def generate_frames(self, element_func: callable, **args) -> list:
        return element_func(**args)


@pytest.fixture
def server():
    """Create a test server instance."""
    TestRegistry.reload_all()
    return TestStreamServer()


def test_server_initial_state(server):
    """Test server initializes with default pattern."""
    assert server.state.element_name == "pattern_a"
    assert server.state.fps == 10.0
    assert server.state.intensity == 1.0
    assert server.state.frame_count == 0


def test_server_set_element(server):
    """Test setting element updates state and invalidates cache."""
    server.state.set_element("pattern_b", param=42)
    
    assert server.state.element_name == "pattern_b"
    assert server.state.element_args == {"param": 42}
    assert server.state.frame_count == 0


def test_server_set_invalid_element(server):
    """Test setting invalid element raises ValueError."""
    with pytest.raises(ValueError, match="Unknown pattern"):
        server.state.set_element("nonexistent")


def test_server_set_fps(server):
    """Test setting FPS clamps to valid range."""
    server.state.set_fps(100)
    assert server.state.fps == 60  # clamped
    
    server.state.set_fps(0)
    assert server.state.fps == 1  # clamped


def test_server_set_intensity(server):
    """Test setting intensity clamps to valid range."""
    server.state.set_intensity(2.0)
    assert server.state.intensity == 1.0
    
    server.state.set_intensity(-0.5)
    assert server.state.intensity == 0.0


def test_server_apply_intensity(server):
    """Test intensity application to frames."""
    frames = [[[100, 100, 100], [50, 50, 50]]]
    
    server.state.set_intensity(0.5)
    result = server.state.apply_intensity(frames)
    
    assert result[0][0] == [50, 50, 50]
    assert result[0][1] == [25, 25, 25]


def test_server_apply_intensity_noop_at_full(server):
    """Test intensity 1.0 returns original frames (no copy)."""
    frames = [[[100, 100, 100]]]
    server.state.set_intensity(1.0)
    
    result = server.state.apply_intensity(frames)
    assert result is frames  # same object


def test_server_cache_invalidation_on_element_change(server):
    """Test cache key changes when element changes."""
    # Get initial pattern (pattern_a)
    frames1 = server.state.get_element()
    key1 = server.state._cache_key
    
    # Change to pattern_b
    server.state.set_element("pattern_b")
    frames2 = server.state.get_element()
    key2 = server.state._cache_key
    
    assert key1 != key2
    assert frames1 != frames2


def test_server_cache_invalidation_on_args_change(server):
    """Test cache key changes when args change."""
    frames1 = server.state.get_element()
    key1 = server.state._cache_key
    
    # Same pattern, different args
    server.state.set_element("pattern_b", param=99)
    frames2 = server.state.get_element()
    key2 = server.state._cache_key
    
    assert key1 != key2


def test_server_cache_invalidation_on_fps_change(server):
    """Test FPS change doesn't invalidate element cache (separate concern)."""
    frames1 = server.state.get_element()
    key1 = server.state._cache_key
    
    server.state.set_fps(30)
    frames2 = server.state.get_element()
    key2 = server.state._cache_key
    
    # Cache key should be same (element+args unchanged)
    assert key1 == key2
    assert frames1 is frames2  # same cached object


def test_server_status_includes_all_fields(server):
    """Test status dict has all expected fields."""
    status = server.state.status()
    
    assert "pattern" in status
    assert "pattern_args" in status
    assert "fps" in status
    assert "brightness" in status
    assert "frames_in_element" in status
    assert "frame_index" in status
    assert "connected_clients" in status
    assert "uptime_seconds" in status


def test_server_make_frame_format(server):
    """Test make_frame returns correct WS format."""
    frame_data = [[[255, 0, 0], [0, 255, 0]]]
    result = server.make_frame(frame_data, 1.0)
    
    assert "ts" in result
    assert result["kind"] == "test"
    assert result["colors"] == frame_data


def test_server_generate_frames_calls_func_with_args(server):
    """Test generate_frames passes args to pattern function."""
    call_kwargs = {}
    
    def tracking_func(**kwargs):
        call_kwargs.update(kwargs)
        return [[[1, 2, 3]]]
    
    server.generate_frames(tracking_func, color=[255, 0, 0], speed=2.0)
    
    assert call_kwargs == {"color": [255, 0, 0], "speed": 2.0}


# ===== HTTP ENDPOINT TESTS =====

@pytest.mark.asyncio
async def test_http_status_endpoint(server):
    """Test /status endpoint returns state."""
    from aiohttp.test_utils import make_mocked_request
    
    request = make_mocked_request("GET", "/status")
    response = await server.http_status(request)
    
    assert response.status == 200
    data = json.loads(response.body)
    assert data["pattern"] == "pattern_a"


@pytest.mark.asyncio
async def test_http_set_element_endpoint(server):
    """Test POST /pattern endpoint."""
    from aiohttp.test_utils import make_mocked_request
    
    request = make_mocked_request("POST", "/pattern")
    request.json = AsyncMock(return_value={"pattern": "pattern_b", "args": {"param": 99}})
    
    response = await server.http_set_element(request)
    
    assert response.status == 200
    data = json.loads(response.body)
    assert data["ok"] is True
    assert data["status"]["pattern"] == "pattern_b"
    assert data["status"]["pattern_args"] == {"param": 99}


@pytest.mark.asyncio
async def test_http_set_element_invalid(server):
    """Test POST /pattern with invalid pattern returns 400."""
    from aiohttp.test_utils import make_mocked_request
    
    request = make_mocked_request("POST", "/pattern")
    request.json = AsyncMock(return_value={"pattern": "nonexistent", "args": {}})
    
    response = await server.http_set_element(request)
    
    assert response.status == 400
    data = json.loads(response.body)
    assert data["ok"] is False


@pytest.mark.asyncio
async def test_http_set_fps_endpoint(server):
    """Test POST /fps endpoint."""
    from aiohttp.test_utils import make_mocked_request
    
    request = make_mocked_request("POST", "/fps")
    request.json = AsyncMock(return_value={"fps": 30})
    
    response = await server.http_set_fps(request)
    
    assert response.status == 200
    data = json.loads(response.body)
    assert data["ok"] is True
    assert data["status"]["fps"] == 30


@pytest.mark.asyncio
async def test_http_set_intensity_endpoint(server):
    """Test POST /brightness endpoint."""
    from aiohttp.test_utils import make_mocked_request
    
    request = make_mocked_request("POST", "/brightness")
    request.json = AsyncMock(return_value={"brightness": 0.5})
    
    response = await server.http_set_intensity(request)
    
    assert response.status == 200
    data = json.loads(response.body)
    assert data["ok"] is True
    assert data["status"]["brightness"] == 0.5


@pytest.mark.asyncio
async def test_http_list_elements_endpoint(server):
    """Test GET /patterns endpoint."""
    from aiohttp.test_utils import make_mocked_request
    
    request = make_mocked_request("GET", "/patterns")
    response = await server.http_list_elements(request)
    
    assert response.status == 200
    data = json.loads(response.body)
    assert "patterns" in data
    assert data["current"] == "pattern_a"
    assert len(data["patterns"]) == 2


@pytest.mark.asyncio
async def test_http_reload_elements_endpoint(server):
    """Test POST /patterns/reload endpoint."""
    from aiohttp.test_utils import make_mocked_request
    
    request = make_mocked_request("POST", "/patterns/reload")
    response = await server.http_reload_elements(request)
    
    assert response.status == 200
    data = json.loads(response.body)
    assert data["ok"] is True
    assert "patterns" in data


# ===== WS FRAME LOOP TIMING TEST =====

@pytest.mark.asyncio
async def test_ws_frame_timing_no_drift(server):
    """Test WS frame loop uses monotonic clock to avoid drift."""
    mock_ws = AsyncMock()
    mock_ws.remote_address = ("127.0.0.1", 12345)
    
    # Mock send to track calls
    send_calls = []
    async def mock_send(data):
        send_calls.append(time.monotonic())
    mock_ws.send = mock_send
    
    # Run for a short time
    server.state.set_fps(50)  # Lower FPS for more reliable test
    server.state.set_intensity(1.0)
    
    # Start handler task
    task = asyncio.create_task(server.ws_handler(mock_ws))
    
    # Let it run for ~200ms
    await asyncio.sleep(0.2)
    
    # Cancel
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    
    # Should have sent ~8-12 frames (50 FPS * 0.2s = 10 frames)
    # Allow some variance for test environment
    assert len(send_calls) >= 6
    assert len(send_calls) <= 20
    
    # Check intervals are roughly consistent (within 50% tolerance)
    if len(send_calls) >= 3:
        intervals = [send_calls[i+1] - send_calls[i] for i in range(len(send_calls)-1)]
        expected_interval = 0.02  # 50 FPS = 20ms
        for interval in intervals:
            assert 0.01 < interval < 0.04  # 10ms - 40ms tolerance


if __name__ == "__main__":
    pytest.main([__file__, "-v"])