from unittest.mock import AsyncMock, patch

import pytest

from reflexd.registry import clear, get_handler


@pytest.fixture(autouse=True)
def reset_registry():
    clear()
    yield
    clear()


class MockLEDClient:
    """Mock LEDStreamClient with proper async context manager."""

    def __init__(self):
        self.set_pattern = AsyncMock()
        self.set_fps = AsyncMock()
        self.set_brightness = AsyncMock()
        self.reload_patterns = AsyncMock()
        self.status = AsyncMock()
        self.list_patterns = AsyncMock()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


@pytest.mark.asyncio
async def test_thinking_start_sets_avatar_and_starts_loops():
    """Test that thinking_start sets avatar to thinking and starts LED pattern."""
    mock_call = AsyncMock()
    mock_led = MockLEDClient()

    with patch("reflexd.call", mock_call):
        import importlib

        import reflexes.thinking as thinking_module

        importlib.reload(thinking_module)

        # Patch LEDStreamClient in the reloaded module
        with patch.object(thinking_module, "LEDStreamClient") as mock_led_class:
            mock_led_class.return_value = mock_led

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

            # Should call move_head for thinking pose
            assert "move_head" in tools_called
            move_call = next(c for c in calls if c.args[0] == "move_head")
            assert move_call.kwargs["yaw"] == 30
            assert move_call.kwargs["pitch"] == 30
            assert move_call.kwargs["speed"] == 80

            # Should set LED pattern to "rainbow" via LED Stream Server
            mock_led.set_pattern.assert_called_once_with("rainbow")
            mock_led.set_fps.assert_called_once_with(20)
            mock_led.set_brightness.assert_called_once_with(1.0)


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

        # Should call move_head for return to neutral
        assert "move_head" in tools_called
        move_call = next(c for c in calls if c.args[0] == "move_head")
        assert move_call.kwargs["yaw"] == 0
        assert move_call.kwargs["pitch"] == 12
        assert move_call.kwargs["speed"] == 80


@pytest.mark.asyncio
async def test_idle_sets_neutral_pose_and_clears_leds():
    """Test that cleanup_thinking clears LEDs and sets neutral pose."""
    mock_call = AsyncMock()

    with patch("reflexd.call", mock_call):
        import importlib

        import reflexes.thinking as thinking_module

        importlib.reload(thinking_module)

        # First trigger thinking_start to set state to THINKING
        start_handler = get_handler("thinking_start")
        await start_handler({})

        # Call cleanup for thinking_start (simulates event cleanup - goes to idle)
        from reflexes.thinking import _thinking_reflex

        await _thinking_reflex.cleanup_thinking()

        # Should call clear_leds and set_avatar(face="neutral") via call()
        calls = mock_call.call_args_list
        tools_called = [c.args[0] for c in calls]
        assert "clear_leds" in tools_called
        assert "set_avatar" in tools_called

        # Should call move_head for neutral position (LAST move_head call)
        move_calls = [c for c in calls if c.args[0] == "move_head"]
        assert len(move_calls) >= 2  # initial thinking + idle return
        # Last move_head should be neutral position
        last_move = move_calls[-1]
        assert last_move.kwargs["yaw"] == 0
        assert last_move.kwargs["pitch"] == 12
        assert last_move.kwargs["speed"] == 80


@pytest.mark.asyncio
async def test_micro_movements_use_direct_mcp():
    """Test that micro-movements use direct MCP move_head calls."""
    mock_call = AsyncMock()

    with patch("reflexd.call", mock_call):
        import importlib

        import reflexes.thinking as thinking_module

        importlib.reload(thinking_module)

        handler = get_handler("thinking_start")
        assert handler is not None
        await handler({})

        # Give time for micro_movements_loop to run (interval is 3s, so wait a bit)
        import asyncio

        await asyncio.sleep(0.1)

        # Should have called move_head at least once for initial pose
        calls = mock_call.call_args_list
        move_calls = [c for c in calls if c.args[0] == "move_head"]

        # At least initial thinking pose call
        assert len(move_calls) >= 1

        # Check that initial move is to thinking pose
        initial_move = move_calls[0]
        assert initial_move.kwargs["yaw"] == 30
        assert initial_move.kwargs["pitch"] == 30
        assert initial_move.kwargs["speed"] == 80