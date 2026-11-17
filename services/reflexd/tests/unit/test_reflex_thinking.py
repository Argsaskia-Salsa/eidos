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

        # Should set RED LEDs at start
        led_call = next((c for c in calls if c.args[0] == "set_all_leds"), None)
        assert led_call is not None
        assert led_call.kwargs == {"r": 255, "g": 0, "b": 0}


@pytest.mark.asyncio
async def test_thinking_end_sets_happy_and_green_then_neutral_after_delay():
    mock_call = AsyncMock()

    with patch("reflexd.call", mock_call):
        import importlib

        import reflexes.thinking as thinking_module

        importlib.reload(thinking_module)

        handler = get_handler("thinking_end")
        assert handler is not None
        await handler({})

        # Should call set_avatar(face="happy") and set_all_leds(green)
        calls = mock_call.call_args_list
        tools_called = [c.args[0] for c in calls]
        assert "set_avatar" in tools_called
        assert "set_all_leds" in tools_called

        # Find the set_avatar call - should be happy, not neutral
        avatar_call = next(c for c in calls if c.args[0] == "set_avatar")
        assert avatar_call.kwargs == {"face": "happy"}

        # Find the set_all_leds call - should be GREEN (0, 255, 0)
        led_call = next(c for c in calls if c.args[0] == "set_all_leds")
        assert led_call.kwargs == {"r": 0, "g": 255, "b": 0}

        # clear_leds should NOT be called immediately - it happens after 60s delay
        assert "clear_leds" not in tools_called
        # move_head is called in the cancelled task's handler, not synchronously
        assert "move_head" not in tools_called