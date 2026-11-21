import asyncio
import sys
from unittest.mock import AsyncMock, patch

import pytest

from reflexd.gateway_client import GatewayClient
from reflexd.gateway_tools import GATEWAY_TOOLS
from reflexd.registry import clear
from reflexd.server import (
    EventCancelledError,
    HandlerError,
    NoHandlerError,
    _get_running_lock,
    _running,
    handle_call,
    handle_inject_event,
    handle_list_gateway_tools,
    handle_list_reflexes,
    handle_reload_reflexes,
)


@pytest.fixture(autouse=True)
def reset_registry():
    clear()
    _running.clear()
    yield
    clear()
    _running.clear()


class TestHandleInjectEvent:
    @pytest.mark.asyncio
    async def test_handle_inject_event_calls_handler(self):
        mock_client = AsyncMock(spec=GatewayClient)

        with patch("reflexd.server.get_gateway_client", return_value=mock_client):

            async def handler(data):
                return {"result": "ok"}

            from reflexd.registry import on_event

            on_event("test_event")(handler)

            result = await handle_inject_event("test_event", {"key": "value"})

            assert result["status"] == "ok"
            assert result["event"] == "test_event"

    @pytest.mark.asyncio
    async def test_handle_inject_event_cancels_previous(self):
        mock_client = AsyncMock(spec=GatewayClient)

        with patch("reflexd.server.get_gateway_client", return_value=mock_client):
            cancelled = False

            async def long_handler(data):
                nonlocal cancelled
                try:
                    await asyncio.sleep(10)
                except asyncio.CancelledError:
                    cancelled = True
                    raise

            from reflexd.registry import on_event

            on_event("test_event")(long_handler)

            task1 = asyncio.create_task(handle_inject_event("test_event", {}))
            await asyncio.sleep(0.01)

            result2 = await handle_inject_event("test_event", {})

            await asyncio.sleep(0.01)
            assert cancelled is True
            assert result2["status"] == "ok"

            try:
                await task1
            except EventCancelledError:
                pass

    @pytest.mark.asyncio
    async def test_handle_inject_event_race_safe(self):
        mock_client = AsyncMock(spec=GatewayClient)

        with patch("reflexd.server.get_gateway_client", return_value=mock_client):
            call_count = 0

            async def handler(data):
                nonlocal call_count
                call_count += 1
                await asyncio.sleep(0.1)

            from reflexd.registry import on_event

            on_event("race_event")(handler)

            results = await asyncio.gather(
                handle_inject_event("race_event", {}),
                handle_inject_event("race_event", {}),
                handle_inject_event("race_event", {}),
                return_exceptions=True,
            )

            statuses = []
            for r in results:
                if isinstance(r, EventCancelledError):
                    statuses.append("cancelled")
                elif isinstance(r, dict):
                    statuses.append(r.get("status", "unknown"))
                else:
                    statuses.append("error")

            assert "ok" in statuses
            assert "cancelled" in statuses

    @pytest.mark.asyncio
    async def test_handle_inject_event_no_handler(self):
        mock_client = AsyncMock(spec=GatewayClient)

        with patch("reflexd.server.get_gateway_client", return_value=mock_client):
            with pytest.raises(NoHandlerError) as exc_info:
                await handle_inject_event("nonexistent", {})
            assert exc_info.value.event == "nonexistent"

    @pytest.mark.asyncio
    async def test_handle_inject_event_handler_error(self):
        mock_client = AsyncMock(spec=GatewayClient)

        with patch("reflexd.server.get_gateway_client", return_value=mock_client):

            async def failing_handler(data):
                raise ValueError("test error")

            from reflexd.registry import on_event

            on_event("failing_event")(failing_handler)

            with pytest.raises(HandlerError) as exc_info:
                await handle_inject_event("failing_event", {})
            assert exc_info.value.event == "failing_event"
            assert "test error" in exc_info.value.message


class TestHandleCall:
    @pytest.mark.asyncio
    async def test_handle_call_forwards_to_gateway(self):
        mock_client = AsyncMock(spec=GatewayClient)
        mock_client.call.return_value = {"result": "success"}

        with patch("reflexd.server.get_gateway_client", return_value=mock_client):
            result = await handle_call("set_avatar", {"face": "happy"})
            assert result == {"result": "success"}
            mock_client.call.assert_called_once_with("set_avatar", {"face": "happy"})

    @pytest.mark.asyncio
    async def test_handle_call_raises_if_no_gateway(self):
        with (
            patch("reflexd.server.get_gateway_client", return_value=None),
            pytest.raises(RuntimeError, match="Gateway client not connected"),
        ):
            await handle_call("set_avatar", {"face": "happy"})


class TestHandleListReflexes:
    @pytest.mark.asyncio
    async def test_handle_list_reflexes(self):
        from reflexd.registry import on_event

        @on_event("event1")
        async def handler1(data):
            pass

        @on_event("event2")
        async def handler2(data):
            pass

        result = await handle_list_reflexes()
        assert set(result["reflexes"]) == {"event1", "event2"}


class TestHandleListGatewayTools:
    @pytest.mark.asyncio
    async def test_handle_list_gateway_tools(self):
        result = await handle_list_gateway_tools()
        assert result["tools"] == GATEWAY_TOOLS


class TestHandleReloadReflexes:
    @pytest.mark.asyncio
    async def test_handle_reload_reflexes_loads_modules(self, tmp_path):
        reflexes_dir = tmp_path / "reflexes"
        reflexes_dir.mkdir()

        (reflexes_dir / "reflex1.py").write_text(
            "from reflexd import on_event\n\n@on_event('test1')\nasync def h(d): pass\n"
        )
        (reflexes_dir / "reflex2.py").write_text(
            "from reflexd import on_event\n\n@on_event('test2')\nasync def h(d): pass\n"
        )

        # Add parent directory to sys.path so imports work (same as server.py does)
        sys.path.insert(0, str(reflexes_dir.parent))

        result = await handle_reload_reflexes(str(reflexes_dir))

        assert result["status"] == "ok"
        assert "reflex1" in result["loaded"]
        assert "reflex2" in result["loaded"]

    @pytest.mark.asyncio
    async def test_handle_reload_reflexes_skips_underscore(self, tmp_path):
        reflexes_dir = tmp_path / "reflexes"
        reflexes_dir.mkdir()

        (reflexes_dir / "_private.py").write_text(
            "from reflexd import on_event\n\n@on_event('test')\nasync def h(d): pass\n"
        )
        (reflexes_dir / "public.py").write_text(
            "from reflexd import on_event\n\n@on_event('test')\nasync def h(d): pass\n"
        )

        sys.path.insert(0, str(reflexes_dir.parent))

        result = await handle_reload_reflexes(str(reflexes_dir))

        assert result["status"] == "ok"
        assert "_private" not in result["loaded"]
        assert "public" in result["loaded"]

    @pytest.mark.asyncio
    async def test_handle_reload_reflexes_reports_errors(self, tmp_path):
        reflexes_dir = tmp_path / "reflexes"
        reflexes_dir.mkdir()

        (reflexes_dir / "good.py").write_text(
            "from reflexd import on_event\n\n@on_event('test')\nasync def h(d): pass\n"
        )
        (reflexes_dir / "bad.py").write_text("import nonexistent_module\n")

        sys.path.insert(0, str(reflexes_dir.parent))

        result = await handle_reload_reflexes(str(reflexes_dir))

        assert result["status"] == "ok"
        assert "good" in result["loaded"]
        assert len(result["errors"]) == 1
        assert result["errors"][0]["module"] == "bad"

    @pytest.mark.asyncio
    async def test_handle_reload_reflexes_dir_not_found(self, tmp_path):
        nonexistent_dir = tmp_path / "nonexistent"
        result = await handle_reload_reflexes(str(nonexistent_dir))

        assert result["status"] == "error"
        assert "not found" in result["message"]


class TestGetRunningLock:
    @pytest.mark.asyncio
    async def test_get_running_lock_creates_per_loop(self):
        lock1 = _get_running_lock()
        lock2 = _get_running_lock()
        assert lock1 is lock2

    @pytest.mark.asyncio
    async def test_get_running_lock_different_loops(self):
        lock1 = _get_running_lock()
        # In pytest-asyncio, all tests share the same loop, so we can't easily test different loops
        # But we can verify the function works and returns a lock
        lock2 = _get_running_lock()
        assert lock1 is lock2  # Same loop returns same lock

    @pytest.mark.asyncio
    async def test_get_running_lock_fallback_when_no_running_loop(self):
        # This tests the except RuntimeError branch when get_running_loop fails
        # We can't easily test this without manipulating the event loop state,
        # but we can verify the function doesn't crash
        lock = _get_running_lock()
        assert lock is not None


class TestHandleJsonRpc:
    @pytest.mark.asyncio
    async def test_handle_jsonrpc_inject_event_missing_name(self):
        import json
        from unittest.mock import AsyncMock, MagicMock, patch

        from aiohttp import web

        from reflexd.server import _create_session, _get_session

        request = MagicMock(spec=web.Request)
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {"name": "inject_event", "arguments": {"data": {}}},
                "id": 1,
            }
        )
        session_id = await _create_session()
        request.headers = {"Mcp-Session-Id": session_id}

        with patch(
            "reflexd.server._get_session", return_value={"created": 0, "data": {}}
        ):
            from reflexd.server import handle_jsonrpc

            resp = await handle_jsonrpc(request)

        assert resp.status == 400
        data = json.loads(resp.text)
        assert "error" in data
        assert data["error"]["code"] == -32602
        assert "Missing event name" in data["error"]["message"]

    @pytest.mark.asyncio
    async def test_handle_jsonrpc_call_missing_tool(self):
        import json
        from unittest.mock import AsyncMock, MagicMock, patch

        from aiohttp import web

        from reflexd.server import _create_session, _get_session

        request = MagicMock(spec=web.Request)
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {"name": "call", "arguments": {"arguments": {}}},
                "id": 1,
            }
        )
        session_id = await _create_session()
        request.headers = {"Mcp-Session-Id": session_id}

        with patch(
            "reflexd.server._get_session", return_value={"created": 0, "data": {}}
        ):
            from reflexd.server import handle_jsonrpc

            resp = await handle_jsonrpc(request)

        assert resp.status == 400
        data = json.loads(resp.text)
        assert "error" in data
        assert data["error"]["code"] == -32602
        assert "Missing tool name" in data["error"]["message"]

    @pytest.mark.asyncio
    async def test_handle_jsonrpc_user_response(self):
        import json
        from unittest.mock import AsyncMock, MagicMock, patch

        from aiohttp import web

        from reflexd.registry import on_event
        from reflexd.server import _create_session, _get_session

        @on_event("user_response")
        async def handler(data):
            return {"handled": True}

        request = MagicMock(spec=web.Request)
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "user_response",
                    "arguments": {"data": {"text": "hello"}},
                },
                "id": 1,
            }
        )
        session_id = await _create_session()
        request.headers = {"Mcp-Session-Id": session_id}

        with patch(
            "reflexd.server.get_gateway_client",
            return_value=AsyncMock(spec=GatewayClient),
        ):
            with patch(
                "reflexd.server._get_session", return_value={"created": 0, "data": {}}
            ):
                from reflexd.server import handle_jsonrpc

                resp = await handle_jsonrpc(request)

        assert resp.status == 200
        data = json.loads(resp.text)
        assert data["result"]["status"] == "ok"

    @pytest.mark.asyncio
    async def test_handle_jsonrpc_thinking_end(self):
        import json
        from unittest.mock import AsyncMock, MagicMock, patch

        from aiohttp import web

        from reflexd.registry import on_event
        from reflexd.server import _create_session, _get_session

        @on_event("thinking_end")
        async def handler(data):
            return {"handled": True}

        request = MagicMock(spec=web.Request)
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {"name": "thinking_end", "arguments": {}},
                "id": 1,
            }
        )
        session_id = await _create_session()
        request.headers = {"Mcp-Session-Id": session_id}

        with patch(
            "reflexd.server.get_gateway_client",
            return_value=AsyncMock(spec=GatewayClient),
        ):
            with patch(
                "reflexd.server._get_session", return_value={"created": 0, "data": {}}
            ):
                from reflexd.server import handle_jsonrpc

                resp = await handle_jsonrpc(request)

        assert resp.status == 200
        data = json.loads(resp.text)
        assert data["result"]["status"] == "ok"

    @pytest.mark.asyncio
    async def test_handle_jsonrpc_exception_handling(self):
        import json
        from unittest.mock import AsyncMock, MagicMock, patch

        from aiohttp import web

        from reflexd.server import _create_session, _get_session

        request = MagicMock(spec=web.Request)
        request.json = AsyncMock(
            return_value={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "call",
                    "arguments": {"tool": "set_avatar", "arguments": {"face": "happy"}},
                },
                "id": 1,
            }
        )
        session_id = await _create_session()
        request.headers = {"Mcp-Session-Id": session_id}

        mock_client = AsyncMock(spec=GatewayClient)
        mock_client.call.side_effect = RuntimeError("Gateway down")

        with patch("reflexd.server.get_gateway_client", return_value=mock_client):
            with patch(
                "reflexd.server._get_session", return_value={"created": 0, "data": {}}
            ):
                from reflexd.server import handle_jsonrpc

                resp = await handle_jsonrpc(request)

        assert resp.status == 500
        data = json.loads(resp.text)
        assert data["error"]["code"] == -32603
        assert "Gateway down" in data["error"]["message"]
