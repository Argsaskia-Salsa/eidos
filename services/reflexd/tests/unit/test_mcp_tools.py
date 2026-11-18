import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import web

from reflexd.gateway_client import GatewayClient
from reflexd.registry import clear, on_event
from reflexd.server import _running, handle_jsonrpc


@pytest.fixture(autouse=True)
def reset_state():
    clear()
    _running.clear()
    yield
    clear()
    _running.clear()


class TestMCPTools:
    @pytest.mark.asyncio
    async def test_cancel_reflex_tool_success(self):
        mock_client = AsyncMock(spec=GatewayClient)

        cancelled = False

        async def handler(data):
            nonlocal cancelled
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                cancelled = True
                raise

        on_event("test_event")(handler)

        request = MagicMock(spec=web.Request)
        request.json = AsyncMock(return_value={
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {"name": "cancel_reflex", "arguments": {"name": "test_event"}},
            "id": 1,
        })

        with patch("reflexd.server.get_gateway_client", return_value=mock_client):
            task = asyncio.create_task(handle_inject_event("test_event", {}))
            await asyncio.sleep(0.01)

            resp = await handle_jsonrpc(request)

            assert resp.status == 200
            data = json.loads(resp.text)
            assert data["result"]["status"] == "ok"
            assert data["result"]["cancelled"] is True

            try:
                await task
            except Exception:  # noqa: BLE001,S110 - ignore cleanup errors in test
                pass

    @pytest.mark.asyncio
    async def test_cancel_reflex_tool_not_found(self):
        mock_client = AsyncMock(spec=GatewayClient)

        request = MagicMock(spec=web.Request)
        request.json = AsyncMock(return_value={
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {"name": "cancel_reflex", "arguments": {"name": "nonexistent"}},
            "id": 1,
        })

        with patch("reflexd.server.get_gateway_client", return_value=mock_client):
            resp = await handle_jsonrpc(request)

            assert resp.status == 200
            data = json.loads(resp.text)
            assert data["result"]["status"] == "ok"
            assert data["result"]["cancelled"] is False

    @pytest.mark.asyncio
    async def test_cancel_reflex_tool_missing_name(self):
        mock_client = AsyncMock(spec=GatewayClient)

        request = MagicMock(spec=web.Request)
        request.json = AsyncMock(return_value={
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {"name": "cancel_reflex", "arguments": {}},
            "id": 1,
        })

        with patch("reflexd.server.get_gateway_client", return_value=mock_client):
            resp = await handle_jsonrpc(request)

            assert resp.status == 400
            data = json.loads(resp.text)
            assert data["error"]["code"] == -32602
            assert "Missing reflex name" in data["error"]["message"]

    @pytest.mark.asyncio
    async def test_list_running_reflexes_tool(self):
        mock_client = AsyncMock(spec=GatewayClient)

        async def handler1(data):
            await asyncio.sleep(10)

        async def handler2(data):
            await asyncio.sleep(10)

        on_event("event1")(handler1)
        on_event("event2")(handler2)

        request = MagicMock(spec=web.Request)
        request.json = AsyncMock(return_value={
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {"name": "list_running_reflexes", "arguments": {}},
            "id": 1,
        })

        with patch("reflexd.server.get_gateway_client", return_value=mock_client):
            task1 = asyncio.create_task(handle_inject_event("event1", {}))
            task2 = asyncio.create_task(handle_inject_event("event2", {}))
            await asyncio.sleep(0.01)

            resp = await handle_jsonrpc(request)

            assert resp.status == 200
            data = json.loads(resp.text)
            assert set(data["result"]["running"]) == {"event1", "event2"}

            for task in [task1, task2]:
                try:
                    await task
                except Exception:  # noqa: BLE001,S110 - ignore cleanup errors in test
                    pass


# Import handle_inject_event for the tests above
from reflexd.server import handle_inject_event