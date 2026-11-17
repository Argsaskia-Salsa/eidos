import asyncio
import sys
from unittest.mock import AsyncMock, patch

import pytest
from aiohttp.test_utils import TestClient, TestServer

import reflexd.gateway_client as gateway_client_module
from reflexd.gateway_client import GatewayClient
from reflexd.registry import clear
from reflexd.server import create_app


@pytest.fixture(autouse=True)
def reset_registry():
    clear()
    yield
    clear()


@pytest.fixture
def mock_gateway_client():
    client = AsyncMock(spec=GatewayClient)
    client.call = AsyncMock(return_value={"result": "success"})
    return client


@pytest.fixture
def app(mock_gateway_client):
    # Patch the global _gateway_client variable
    with patch.object(gateway_client_module, "_gateway_client", mock_gateway_client):
        clear()
        yield create_app()


@pytest.fixture
async def client(app):
    async with TestClient(TestServer(app)) as client:
        yield client


class TestHealthEndpoint:
    @pytest.mark.asyncio
    async def test_health_endpoint(self, client):
        resp = await client.get("/health")
        assert resp.status == 200
        data = await resp.json()
        assert data["status"] == "ok"
        assert "gateway_connected" in data
        assert "reflexes_loaded" in data
        assert "uptime_seconds" in data
        assert isinstance(data["uptime_seconds"], (int, float))
        assert data["uptime_seconds"] >= 0


class TestJSONRPCErrors:
    @pytest.mark.asyncio
    async def test_invalid_json_rpc_parse_error(self, client):
        resp = await client.post(
            "/mcp", data="not json", headers={"Content-Type": "application/json"}
        )
        assert resp.status == 400
        data = await resp.json()
        assert data["jsonrpc"] == "2.0"
        assert data["error"]["code"] == -32700
        assert "Parse error" in data["error"]["message"]

    @pytest.mark.asyncio
    async def test_invalid_jsonrpc_version(self, client):
        resp = await client.post(
            "/mcp", json={"jsonrpc": "1.0", "method": "tools/call", "id": 1}
        )
        assert resp.status == 400
        data = await resp.json()
        assert data["error"]["code"] == -32600

    @pytest.mark.asyncio
    async def test_method_not_found(self, client):
        resp = await client.post(
            "/mcp", json={"jsonrpc": "2.0", "method": "unknown/method", "id": 1}
        )
        assert resp.status == 404
        data = await resp.json()
        assert data["error"]["code"] == -32601


class TestToolsCall:
    @pytest.mark.asyncio
    async def test_tools_call_inject_event(self, client, mock_gateway_client):
        from reflexd.registry import on_event

        @on_event("test_event")
        async def handler(data):
            return {"handled": True}

        resp = await client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "inject_event",
                    "arguments": {"name": "test_event", "data": {"key": "value"}},
                },
                "id": 1,
            },
        )
        assert resp.status == 200
        data = await resp.json()
        assert data["result"]["status"] == "ok"
        assert data["result"]["event"] == "test_event"

    @pytest.mark.asyncio
    async def test_tools_call_inject_event_cancel(self, client, mock_gateway_client):
        from reflexd.registry import on_event

        cancel_event = asyncio.Event()

        @on_event("long_event")
        async def handler(data):
            cancel_event.set()
            await asyncio.sleep(10)

        asyncio.create_task(
            client.post(
                "/mcp",
                json={
                    "jsonrpc": "2.0",
                    "method": "tools/call",
                    "params": {
                        "name": "inject_event",
                        "arguments": {"name": "long_event", "data": {}},
                    },
                    "id": 1,
                },
            )
        )

        await asyncio.wait_for(cancel_event.wait(), timeout=1.0)
        await asyncio.sleep(0.05)

        resp2 = await client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "inject_event",
                    "arguments": {"name": "long_event", "data": {}},
                },
                "id": 2,
            },
        )

        data2 = await resp2.json()
        assert data2["result"]["status"] == "ok"

        await asyncio.sleep(0.05)

    @pytest.mark.asyncio
    async def test_tools_call_call(self, client, mock_gateway_client):
        mock_gateway_client.call.return_value = {"position": {"yaw": 10, "pitch": 60}}

        resp = await client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "call",
                    "arguments": {
                        "tool": "move_head",
                        "arguments": {"yaw": 10, "pitch": 60},
                    },
                },
                "id": 1,
            },
        )
        assert resp.status == 200
        data = await resp.json()
        assert data["result"] == {"position": {"yaw": 10, "pitch": 60}}
        mock_gateway_client.call.assert_called_once_with(
            "move_head", {"yaw": 10, "pitch": 60}
        )

    @pytest.mark.asyncio
    async def test_tools_call_list_reflexes(self, client, mock_gateway_client):
        from reflexd.registry import on_event

        @on_event("reflex1")
        async def h1(data):
            pass

        @on_event("reflex2")
        async def h2(data):
            pass

        resp = await client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {"name": "list_reflexes", "arguments": {}},
                "id": 1,
            },
        )
        assert resp.status == 200
        data = await resp.json()
        assert set(data["result"]["reflexes"]) == {"reflex1", "reflex2"}

    @pytest.mark.asyncio
    async def test_tools_call_list_gateway_tools(self, client, mock_gateway_client):
        from reflexd.gateway_tools import GATEWAY_TOOLS

        resp = await client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {"name": "list_gateway_tools", "arguments": {}},
                "id": 1,
            },
        )
        assert resp.status == 200
        data = await resp.json()
        assert data["result"]["tools"] == GATEWAY_TOOLS

    @pytest.mark.asyncio
    async def test_tools_call_reload_reflexes(
        self, client, mock_gateway_client, tmp_path
    ):
        reflexes_dir = tmp_path / "reflexes"
        reflexes_dir.mkdir()
        (reflexes_dir / "test_reflex.py").write_text(
            "from reflexd import on_event\n\n@on_event('reloaded')\nasync def h(d): pass\n"
        )

        sys.path.insert(0, str(reflexes_dir.parent))
        with patch("reflexd.main.REFLEXES_DIR", str(reflexes_dir)):
            resp = await client.post(
                "/mcp",
                json={
                    "jsonrpc": "2.0",
                    "method": "tools/call",
                    "params": {"name": "reload_reflexes", "arguments": {}},
                    "id": 1,
                },
            )
        assert resp.status == 200
        data = await resp.json()
        assert data["result"]["status"] == "ok"
        assert "test_reflex" in data["result"]["loaded"]

    @pytest.mark.asyncio
    async def test_tools_call_user_message(self, client, mock_gateway_client):
        from reflexd.registry import on_event

        @on_event("user_message")
        async def handler(data):
            return {"handled": True}

        resp = await client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "user_message",
                    "arguments": {"data": {"text": "hello"}},
                },
                "id": 1,
            },
        )
        assert resp.status == 200
        data = await resp.json()
        assert data["result"]["status"] == "ok"

    @pytest.mark.asyncio
    async def test_tools_call_thinking_start(self, client, mock_gateway_client):
        from reflexd.registry import on_event

        @on_event("thinking_start")
        async def handler(data):
            return {"handled": True}

        resp = await client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {"name": "thinking_start", "arguments": {}},
                "id": 1,
            },
        )
        assert resp.status == 200
        data = await resp.json()
        assert data["result"]["status"] == "ok"

    @pytest.mark.asyncio
    async def test_tools_call_unknown_tool(self, client, mock_gateway_client):
        resp = await client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {"name": "unknown_tool", "arguments": {}},
                "id": 1,
            },
        )
        assert resp.status == 404
        data = await resp.json()
        assert "error" in data
        assert data["error"]["code"] == -32601
        assert "Unknown tool" in data["error"]["message"]

    @pytest.mark.asyncio
    async def test_concurrent_inject_events(self, client, mock_gateway_client):
        from reflexd.registry import on_event

        results = []

        @on_event("concurrent_event")
        async def handler(data):
            await asyncio.sleep(0.1)
            results.append(data.get("id"))

        tasks = [
            client.post(
                "/mcp",
                json={
                    "jsonrpc": "2.0",
                    "method": "tools/call",
                    "params": {
                        "name": "inject_event",
                        "arguments": {"name": "concurrent_event", "data": {"id": i}},
                    },
                    "id": i,
                },
            )
            for i in range(5)
        ]

        responses = await asyncio.gather(*tasks)
        for resp in responses:
            assert resp.status == 200
            data = await resp.json()
            if "result" in data:
                assert data["result"]["status"] in ("ok", "cancelled")
            else:
                assert "error" in data


class TestReflexModuleTests:
    pass
