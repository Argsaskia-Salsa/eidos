import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from reflexd.registry import clear, get_handler


@pytest.fixture(autouse=True)
def reset_registry():
    clear()
    yield
    clear()


@pytest.mark.asyncio
async def test_idle_runs_loop():
    mock_call = AsyncMock()
    mock_sleep = AsyncMock(side_effect=[None, asyncio.CancelledError()])

    with patch("reflexd.call", mock_call), patch(
        "reflexes.idle.asyncio.sleep", mock_sleep
    ):
        import importlib

        import reflexes.idle as idle_module

        importlib.reload(idle_module)

        handler = get_handler("idle_tick")
        assert handler is not None

        try:
            await handler({})
        except asyncio.CancelledError:
            pass

        assert mock_call.call_count >= 1
        call_args = mock_call.call_args_list[0]
        tool = call_args.args[0]
        params = call_args.kwargs
        assert tool == "move_head"
        assert "yaw" in params


@pytest.mark.asyncio
async def test_idle_cancelled_returns_neutral():
    mock_call = AsyncMock()
    mock_sleep = AsyncMock(side_effect=asyncio.CancelledError())

    with patch("reflexd.call", mock_call), patch(
        "reflexes.idle.asyncio.sleep", mock_sleep
    ):
        import importlib

        import reflexes.idle as idle_module

        importlib.reload(idle_module)

        handler = get_handler("idle_tick")
        assert handler is not None

        try:
            await handler({})
        except asyncio.CancelledError:
            pass

        neutral_call = None
        for call_args in mock_call.call_args_list:
            tool = call_args.args[0]
            params = call_args.kwargs
            if tool == "move_head" and params.get("yaw") == 0:
                neutral_call = params
                break

        assert neutral_call is not None
