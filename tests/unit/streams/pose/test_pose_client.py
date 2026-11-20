"""Unit tests for PoseStreamClient method signatures."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from reflexd.streams.pose.client import PoseStreamClient
from reflexd.streams.pose.config import CONFIG


class TestPoseStreamClient:
    """Tests for PoseStreamClient (no server needed)."""

    def test_client_init_defaults(self):
        """Test client initialization with defaults."""
        client = PoseStreamClient()
        
        assert client.http_client.base_url == CONFIG.HTTP_SERVER_URL
        assert client.http_client.element_name == "pose"
        assert client.http_client.intensity_name == "smoothing"
        assert client.token == CONFIG.STACKCHAN_TOKEN
        assert client.gateway_mcp_url == CONFIG.GATEWAY_MCP_URL
        assert client.ws_url == CONFIG.WS_SERVER_URL
        assert client.target == CONFIG.TARGET
        assert client.max_fps == CONFIG.MAX_FPS

    def test_client_init_custom_values(self):
        """Test client initialization with custom values."""
        client = PoseStreamClient(
            http_url="http://custom:1234",
            token="custom_token",
            gateway_mcp_url="http://gateway:5678/mcp",
            ws_url="ws://ws:9000",
            target="neck",
            max_fps=30.0
        )
        
        assert client.http_client.base_url == "http://custom:1234"
        assert client.token == "custom_token"
        assert client.gateway_mcp_url == "http://gateway:5678/mcp"
        assert client.ws_url == "ws://ws:9000"
        assert client.target == "neck"
        assert client.max_fps == 30.0

    @pytest.mark.asyncio
    async def test_client_context_manager(self):
        """Test async context manager creates/closes sessions."""
        client = PoseStreamClient()
        
        with patch('aiohttp.ClientSession') as mock_session_class:
            mock_session = AsyncMock()
            mock_session_class.return_value = mock_session
            
            # Mock http_client context manager
            client.http_client.__aenter__ = AsyncMock()
            client.http_client.__aexit__ = AsyncMock()
            
            async with client as ctx:
                assert ctx is client
                mock_session_class.assert_called_once()
            
            mock_session.close.assert_called_once()
            client.http_client.__aenter__.assert_called_once()
            client.http_client.__aexit__.assert_called_once()

    @pytest.mark.asyncio
    async def test_require_session_raises(self):
        """Test _require_session raises when not initialized."""
        client = PoseStreamClient()
        with pytest.raises(RuntimeError, match="Client not initialized"):
            client._require_session()

    @pytest.mark.asyncio
    async def test_require_token_raises(self):
        """Test _require_token raises when token not set."""
        client = PoseStreamClient(token="")
        with pytest.raises(RuntimeError, match="STACKCHAN_TOKEN not set"):
            client._require_token()

    def test_method_signatures_exist(self):
        """Test all expected methods exist with correct signatures."""
        client = PoseStreamClient()
        
        # MCP methods
        assert hasattr(client, 'stream_start')
        assert hasattr(client, 'stream_stop')
        assert hasattr(client, 'stream_status')
        assert hasattr(client, 'mcp_initialize')
        assert hasattr(client, 'mcp_call')
        
        # HTTP delegated methods
        assert hasattr(client, 'status')
        assert hasattr(client, 'set_pose')
        assert hasattr(client, 'set_fps')
        assert hasattr(client, 'set_smoothing')
        assert hasattr(client, 'list_poses')
        assert hasattr(client, 'reload_poses')
        
        # Aliases
        assert hasattr(client, 'set_element')
        assert hasattr(client, 'set_intensity')
        assert hasattr(client, 'list_elements')
        assert hasattr(client, 'reload_elements')

    @pytest.mark.asyncio
    async def test_mcp_initialize_calls_correct_endpoint(self):
        """Test mcp_initialize makes correct request."""
        client = PoseStreamClient(token="test_token")
        
        with patch('aiohttp.ClientSession') as mock_session_class:
            mock_session = MagicMock()
            mock_resp = AsyncMock()
            mock_resp.headers = {"Mcp-Session-Id": "test-session-id"}
            
            # Create proper async context manager mock for session.post
            mock_cm = MagicMock()
            mock_cm.__aenter__ = AsyncMock(return_value=mock_resp)
            mock_cm.__aexit__ = AsyncMock(return_value=None)
            mock_session.post.return_value = mock_cm
            mock_session_class.return_value = mock_session
            
            client._session = mock_session
            
            session_id = await client.mcp_initialize()
            
            assert session_id == "test-session-id"
            assert client._session_id == "test-session-id"
            
            # Verify request
            call_args = mock_session.post.call_args
            assert call_args[0][0] == CONFIG.GATEWAY_MCP_URL
            assert call_args[1]['json']['method'] == 'initialize'
            assert call_args[1]['headers']['Authorization'] == 'Bearer test_token'

    @pytest.mark.asyncio
    async def test_mcp_call_calls_correct_endpoint(self):
        """Test mcp_call makes correct request."""
        client = PoseStreamClient(token="test_token")
        client._session_id = "existing-session"
        
        with patch('aiohttp.ClientSession') as mock_session_class:
            mock_session = MagicMock()
            mock_resp = AsyncMock()
            mock_resp.json.return_value = {"result": {"status": "ok"}}
            
            # Create proper async context manager mock for session.post
            mock_cm = MagicMock()
            mock_cm.__aenter__ = AsyncMock(return_value=mock_resp)
            mock_cm.__aexit__ = AsyncMock(return_value=None)
            mock_session.post.return_value = mock_cm
            mock_session_class.return_value = mock_session
            
            client._session = mock_session
            
            result = await client.mcp_call("test_tool", {"arg1": "value1"})
            
            assert result == {"status": "ok"}
            
            # Verify request
            call_args = mock_session.post.call_args
            assert call_args[0][0] == CONFIG.GATEWAY_MCP_URL
            assert call_args[1]['json']['method'] == 'tools/call'
            assert call_args[1]['json']['params']['name'] == 'test_tool'
            assert call_args[1]['json']['params']['arguments'] == {"arg1": "value1"}
            assert call_args[1]['headers']['Mcp-Session-Id'] == 'existing-session'

    @pytest.mark.asyncio
    async def test_stream_start_args(self):
        """Test stream_start passes correct args to MCP."""
        client = PoseStreamClient(token="test_token")
        
        with patch.object(client, 'mcp_call', new_callable=AsyncMock) as mock_mcp:
            mock_mcp.return_value = {"result": "started"}
            
            result = await client.stream_start()
            
            mock_mcp.assert_called_once_with("stackchan_follow_pose_stream", {
                "action": "start",
                "url": CONFIG.WS_SERVER_URL,
                "target": CONFIG.TARGET,
                "max_fps": CONFIG.MAX_FPS
            })
            assert result == {"result": "started"}

    @pytest.mark.asyncio
    async def test_stream_stop_args(self):
        """Test stream_stop passes correct args to MCP."""
        client = PoseStreamClient(token="test_token")
        
        with patch.object(client, 'mcp_call', new_callable=AsyncMock) as mock_mcp:
            mock_mcp.return_value = {"result": "stopped"}
            
            result = await client.stream_stop()
            
            mock_mcp.assert_called_once_with("stackchan_follow_pose_stream", {
                "action": "stop"
            })
            assert result == {"result": "stopped"}

    @pytest.mark.asyncio
    async def test_stream_status_args(self):
        """Test stream_status passes correct args to MCP."""
        client = PoseStreamClient(token="test_token")
        
        with patch.object(client, 'mcp_call', new_callable=AsyncMock) as mock_mcp:
            mock_mcp.return_value = {"result": "status"}
            
            result = await client.stream_status()
            
            mock_mcp.assert_called_once_with("stackchan_follow_pose_stream", {
                "action": "status"
            })
            assert result == {"result": "status"}