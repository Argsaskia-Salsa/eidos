import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from reflexd.gateway_client import GatewayClient
from reflexd.registry import clear
from reflexd.server import (
    handle_inject_event,
    handle_call,
    handle_list_reflexes,
    handle_list_gateway_tools,
    handle_reload_reflexes,
)
from reflexd.gateway_tools import GATEWAY_TOOLS


@pytest.fixture(autouse=True)
def reset_registry():
    clear()
    yield
    clear()


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
            except asyncio.CancelledError:
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
            )

            statuses = [r["status"] for r in results]
            assert "ok" in statuses
            assert "cancelled" in statuses

    @pytest.mark.asyncio
    async def test_handle_inject_event_no_handler(self):
        mock_client = AsyncMock(spec=GatewayClient)

        with patch("reflexd.server.get_gateway_client", return_value=mock_client):
            result = await handle_inject_event("nonexistent", {})
            assert result["status"] == "no_handler"
            assert result["event"] == "nonexistent"

    @pytest.mark.asyncio
    async def test_handle_inject_event_handler_error(self):
        mock_client = AsyncMock(spec=GatewayClient)

        with patch("reflexd.server.get_gateway_client", return_value=mock_client):
            async def failing_handler(data):
                raise ValueError("test error")

            from reflexd.registry import on_event
            on_event("failing_event")(failing_handler)

            result = await handle_inject_event("failing_event", {})
            assert result["status"] == "error"
            assert "test error" in result["error"]


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
        with patch("reflexd.server.get_gateway_client", return_value=None):
            with pytest.raises(RuntimeError, match="Gateway client not connected"):
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

        (reflexes_dir / "reflex1.py").write_text("from reflexd import on_event\n\n@on_event('test1')\nasync def h(d): pass\n")
        (reflexes_dir / "reflex2.py").write_text("from reflexd import on_event\n\n@on_event('test2')\nasync def h(d): pass\n")

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

        (reflexes_dir / "_private.py").write_text("from reflexd import on_event\n\n@on_event('test')\nasync def h(d): pass\n")
        (reflexes_dir / "public.py").write_text("from reflexd import on_event\n\n@on_event('test')\nasync def h(d): pass\n")

        sys.path.insert(0, str(reflexes_dir.parent))

        result = await handle_reload_reflexes(str(reflexes_dir))

        assert result["status"] == "ok"
        assert "_private" not in result["loaded"]
        assert "public" in result["loaded"]

    @pytest.mark.asyncio
    async def test_handle_reload_reflexes_reports_errors(self, tmp_path):
        reflexes_dir = tmp_path / "reflexes"
        reflexes_dir.mkdir()

        (reflexes_dir / "good.py").write_text("from reflexd import on_event\n\n@on_event('test')\nasync def h(d): pass\n")
        (reflexes_dir / "bad.py").write_text("import nonexistent_module\n")

        sys.path.insert(0, str(reflexes_dir.parent))

        result = await handle_reload_reflexes(str(reflexes_dir))

        assert result["status"] == "ok"
        assert "good" in result["loaded"]
        assert len(result["errors"]) == 1
        assert result["errors"][0]["module"] == "bad"