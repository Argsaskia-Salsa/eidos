import asyncio
from unittest.mock import AsyncMock, Mock, patch

import pytest

from reflexd.gateway_client import GatewayClient
from reflexd.safety import _head_times


@pytest.fixture(autouse=True)
def reset_rate_limit():
    _head_times.clear()


@pytest.fixture
def gateway_client():
    return GatewayClient("http://test-gateway:8767/mcp", "test-token")


class MockAsyncContextManager:
    def __init__(self, return_value):
        self._return_value = return_value

    async def __aenter__(self):
        return self._return_value

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        return None


def make_post_mock(mock_resp):
    """Create a mock for session.post that returns an async context manager."""
    mock_cm = MockAsyncContextManager(mock_resp)
    # Use a regular Mock, not AsyncMock, so it returns the context manager directly
    mock_post = Mock(return_value=mock_cm)
    return mock_post


class TestGatewayClient:
    @pytest.mark.asyncio
    async def test_connect_creates_session(self, gateway_client):
        with patch("aiohttp.ClientSession") as mock_session_class:
            mock_session = AsyncMock()
            mock_session_class.return_value = mock_session
            mock_resp = AsyncMock()
            mock_resp.status = 200
            mock_resp.headers = {"Mcp-Session-Id": "test-session-id"}
            mock_session.post = make_post_mock(mock_resp)

            await gateway_client.connect()

            assert gateway_client.session is mock_session
            mock_session.post.assert_called_once()
            assert gateway_client.session_id == "test-session-id"

    @pytest.mark.asyncio
    async def test_initialize_sends_correct_request(self, gateway_client):
        with patch("aiohttp.ClientSession") as mock_session_class:
            mock_session = AsyncMock()
            mock_session_class.return_value = mock_session
            mock_resp = AsyncMock()
            mock_resp.status = 200
            mock_resp.headers = {"Mcp-Session-Id": "test-session-id"}
            mock_session.post = make_post_mock(mock_resp)

            await gateway_client.connect()

            call_args = mock_session.post.call_args
            assert call_args[1]["json"]["jsonrpc"] == "2.0"
            assert call_args[1]["json"]["method"] == "initialize"
            assert call_args[1]["json"]["params"]["protocolVersion"] == "2024-11-05"
            assert call_args[1]["headers"]["Authorization"] == "Bearer test-token"
            assert call_args[1]["headers"]["Content-Type"] == "application/json"

    @pytest.mark.asyncio
    async def test_initialize_stores_session_id(self, gateway_client):
        with patch("aiohttp.ClientSession") as mock_session_class:
            mock_session = AsyncMock()
            mock_session_class.return_value = mock_session
            mock_resp = AsyncMock()
            mock_resp.status = 200
            mock_resp.headers = {"Mcp-Session-Id": "abc-123"}
            mock_session.post = make_post_mock(mock_resp)

            await gateway_client.connect()
            assert gateway_client.session_id == "abc-123"

    @pytest.mark.asyncio
    async def test_initialize_raises_on_non_200(self, gateway_client):
        with patch("aiohttp.ClientSession") as mock_session_class:
            mock_session = AsyncMock()
            mock_session_class.return_value = mock_session
            mock_resp = AsyncMock()
            mock_resp.status = 401
            mock_session.post = make_post_mock(mock_resp)

            with pytest.raises(RuntimeError, match="Initialize failed: 401"):
                await gateway_client.connect()

    @pytest.mark.asyncio
    async def test_initialize_raises_on_missing_session_id(self, gateway_client):
        with patch("aiohttp.ClientSession") as mock_session_class:
            mock_session = AsyncMock()
            mock_session_class.return_value = mock_session
            mock_resp = AsyncMock()
            mock_resp.status = 200
            mock_resp.headers = {}
            mock_session.post = make_post_mock(mock_resp)

            with pytest.raises(RuntimeError, match="No Mcp-Session-Id in response"):
                await gateway_client.connect()

    @pytest.mark.asyncio
    async def test_call_sends_tools_call(self, gateway_client):
        gateway_client.session = AsyncMock()
        gateway_client.session_id = "test-session-id"
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json.return_value = {"result": {"success": True}}
        gateway_client.session.post = make_post_mock(mock_resp)

        result = await gateway_client.call("set_avatar", {"face": "happy"})

        call_args = gateway_client.session.post.call_args
        assert call_args[1]["json"]["jsonrpc"] == "2.0"
        assert call_args[1]["json"]["method"] == "tools/call"
        assert call_args[1]["json"]["params"]["name"] == "set_avatar"
        assert call_args[1]["json"]["params"]["arguments"] == {"face": "happy"}
        assert call_args[1]["headers"]["Mcp-Session-Id"] == "test-session-id"
        assert result == {"success": True}

    @pytest.mark.asyncio
    async def test_call_applies_safety_clamping(self, gateway_client):
        gateway_client.session = AsyncMock()
        gateway_client.session_id = "test-session-id"
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json.return_value = {"result": {}}
        gateway_client.session.post = make_post_mock(mock_resp)

        await gateway_client.call("move_head", {"yaw": 180, "pitch": -10, "speed": 500})

        call_args = gateway_client.session.post.call_args
        args = call_args[1]["json"]["params"]["arguments"]
        assert args["yaw"] == 90
        assert args["pitch"] == 5
        assert args["speed"] == 200

    @pytest.mark.asyncio
    async def test_call_enforces_rate_limit(self, gateway_client):
        gateway_client.session = AsyncMock()
        gateway_client.session_id = "test-session-id"

        for _ in range(3):
            mock_resp = AsyncMock()
            mock_resp.status = 200
            mock_resp.json.return_value = {"result": {}}
            gateway_client.session.post = make_post_mock(mock_resp)
            await gateway_client.call("move_head", {"yaw": 0, "pitch": 60})

        with pytest.raises(RuntimeError, match="Rate limit exceeded"):
            await gateway_client.call("move_head", {"yaw": 0, "pitch": 60})

    @pytest.mark.asyncio
    async def test_call_raises_on_gateway_error(self, gateway_client):
        gateway_client.session = AsyncMock()
        gateway_client.session_id = "test-session-id"
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json.return_value = {
            "error": {"code": -32603, "message": "Internal error"}
        }
        gateway_client.session.post = make_post_mock(mock_resp)

        with pytest.raises(RuntimeError, match="Tool error"):
            await gateway_client.call("set_avatar", {"face": "happy"})

    @pytest.mark.asyncio
    async def test_call_raises_on_http_error(self, gateway_client):
        gateway_client.session = AsyncMock()
        gateway_client.session_id = "test-session-id"
        mock_resp = AsyncMock()
        mock_resp.status = 500
        mock_resp.text.return_value = "Internal Server Error"
        gateway_client.session.post = make_post_mock(mock_resp)

        with pytest.raises(RuntimeError, match="Tool call failed: 500"):
            await gateway_client.call("set_avatar", {"face": "happy"})

    @pytest.mark.asyncio
    async def test_call_timeout(self, gateway_client):
        gateway_client.session = AsyncMock()
        gateway_client.session_id = "test-session-id"

        # Create a mock that raises TimeoutError when __aenter__ is called
        mock_cm = Mock()
        mock_cm.__aenter__ = Mock(side_effect=TimeoutError())
        mock_cm.__aexit__ = Mock(return_value=None)
        gateway_client.session.post = Mock(return_value=mock_cm)

        with pytest.raises(asyncio.TimeoutError):
            await gateway_client.call("set_avatar", {"face": "happy"})

    @pytest.mark.asyncio
    async def test_close_closes_session(self, gateway_client):
        mock_session = AsyncMock()
        gateway_client.session = mock_session
        gateway_client.session_id = "test-session-id"

        await gateway_client.close()

        mock_session.close.assert_called_once()
        assert gateway_client.session is None
        assert gateway_client.session_id is None

    @pytest.mark.asyncio
    async def test_initialize_raises_when_session_not_created(self, gateway_client):
        gateway_client.session = None

        with pytest.raises(RuntimeError, match="Session not created"):
            await gateway_client._initialize()

    @pytest.mark.asyncio
    async def test_call_raises_when_not_connected(self, gateway_client):
        gateway_client.session = None
        gateway_client.session_id = None

        with pytest.raises(RuntimeError, match="Not connected to gateway"):
            await gateway_client.call("set_avatar", {"face": "happy"})

        gateway_client.session = AsyncMock()
        gateway_client.session_id = None

        with pytest.raises(RuntimeError, match="Not connected to gateway"):
            await gateway_client.call("set_avatar", {"face": "happy"})

        gateway_client.session = None
        gateway_client.session_id = "test-session-id"

        with pytest.raises(RuntimeError, match="Not connected to gateway"):
            await gateway_client.call("set_avatar", {"face": "happy"})


class TestGatewayClientModule:
    def test_get_gateway_client_returns_none_by_default(self):
        from reflexd.gateway_client import _gateway_client, get_gateway_client
        original = _gateway_client
        try:
            import reflexd.gateway_client as gc
            gc._gateway_client = None
            assert get_gateway_client() is None
        finally:
            import reflexd.gateway_client as gc
            gc._gateway_client = original

    def test_set_gateway_client_sets_global(self):
        from reflexd.gateway_client import (
            _gateway_client,
            get_gateway_client,
            set_gateway_client,
        )
        original = _gateway_client
        try:
            mock_client = AsyncMock()
            set_gateway_client(mock_client)
            assert get_gateway_client() is mock_client
        finally:
            import reflexd.gateway_client as gc
            gc._gateway_client = original
