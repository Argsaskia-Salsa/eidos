from unittest.mock import AsyncMock, patch

import pytest

from reflexd.registry import clear, get_handler


@pytest.fixture(autouse=True)
def reset_registry():
    clear()
    yield
    clear()


@pytest.mark.asyncio
async def test_thinking_start_sets_avatar_and_starts_loops():
    mock_call = AsyncMock()

    with patch("reflexd.call", mock_call):
        import importlib

        import reflexes.thinking as thinking_module

        importlib.reload(thinking_module)

        handler = get_handler("thinking_start")
        assert handler is not None
        await handler({})

        # Should call set_avatar(face="thinking") at minimum
        calls = mock_call.call_args_list
        tools_called = [c.args[0] for c in calls]
        assert "set_avatar" in tools_called

        # Find the set_avatar call
        avatar_call = next(c for c in calls if c.args[0] == "set_avatar")
        assert avatar_call.kwargs == {"face": "thinking"}


@pytest.mark.asyncio
async def test_thinking_end_sets_neutral_and_stops_loops():
    mock_call = AsyncMock()

    with patch("reflexd.call", mock_call):
        import importlib

        import reflexes.thinking as thinking_module

        importlib.reload(thinking_module)

        handler = get_handler("thinking_end")
        assert handler is not None
        await handler({})

        # Should call set_avatar(face="neutral") and clear_leds and move_head(yaw=0)
        calls = mock_call.call_args_list
        tools_called = [c.args[0] for c in calls]
        assert "set_avatar" in tools_called
        assert "clear_leds" in tools_called
        assert "move_head" in tools_called

        # Find the set_avatar call
        avatar_call = next(c for c in calls if c.args[0] == "set_avatar")
        assert avatar_call.kwargs == {"face": "neutral"}

        # Find the move_head call - should return to center
        move_call = next(c for c in calls if c.args[0] == "move_head")
        assert move_call.kwargs["yaw"] == 0
        assert move_call.kwargs["pitch"] == 12  # DEFAULT_PITCH