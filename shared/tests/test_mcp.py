from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from eidos_shared.mcp import MCPClient, create_mcp_server


@pytest.mark.asyncio
async def test_mcp_client_call_tool() -> None:
    client = MCPClient(base_url="http://localhost:8780")

    with patch.object(client._client, "post", new_callable=AsyncMock) as mock_post:
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json = MagicMock(return_value={"result": "success"})
        mock_post.return_value = mock_response

        result = await client.call_tool("test_tool", {"arg": "value"})

        assert result == {"result": "success"}
        mock_post.assert_called_once_with(
            "http://localhost:8780/tools/test_tool",
            json={"arguments": {"arg": "value"}}
        )


@pytest.mark.asyncio
async def test_mcp_client_health_check_success() -> None:
    client = MCPClient(base_url="http://localhost:8780")

    with patch.object(client._client, "get", new_callable=AsyncMock) as mock_get:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_get.return_value = mock_response

        result = await client.health_check()

        assert result is True
        mock_get.assert_called_once_with("http://localhost:8780/health")


@pytest.mark.asyncio
async def test_mcp_client_health_check_failure() -> None:
    client = MCPClient(base_url="http://localhost:8780")

    with patch.object(client._client, "get", new_callable=AsyncMock) as mock_get:
        mock_get.side_effect = Exception("Connection failed")

        result = await client.health_check()

        assert result is False


@pytest.mark.asyncio
async def test_mcp_client_close() -> None:
    client = MCPClient(base_url="http://localhost:8780")

    with patch.object(client._client, "aclose", new_callable=AsyncMock) as mock_close:
        await client.close()
        mock_close.assert_called_once()


def test_create_mcp_server_import_error() -> None:
    with patch.dict("sys.modules", {"fastmcp": None}):
        with pytest.raises(RuntimeError, match="fastmcp is required"):
            create_mcp_server("test", {})


def test_create_mcp_server_success() -> None:
    mock_fastmcp = MagicMock()
    mock_mcp = MagicMock()
    mock_fastmcp.FastMCP.return_value = mock_mcp

    with patch.dict("sys.modules", {"fastmcp": mock_fastmcp}):
        tools = {"tool1": lambda x: x}
        server = create_mcp_server("test", tools)

        assert server == mock_mcp
        mock_fastmcp.FastMCP.assert_called_once_with("test")
        mock_mcp.tool.assert_called()
