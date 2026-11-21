from unittest.mock import AsyncMock, patch

import pytest

from reflexd.registry import clear, get_handler


class PoseStreamError(Exception):
    """Custom exception for pose stream failures."""


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


class MockPoseClient:
    """Mock PoseStreamClient with proper async context manager."""
    def __init__(self, fail_on=None):
        self.set_pose = AsyncMock()
        self.set_smoothing = AsyncMock()
        self.set_fps = AsyncMock()
        self.status = AsyncMock()
        self.list_poses = AsyncMock()
        self.reload_poses = AsyncMock()
        self.stream_start = AsyncMock()
        self.stream_stop = AsyncMock()
        self.stream_status = AsyncMock()
        self._fail_on = fail_on or set()
    
    async def __aenter__(self):
        return self
    
    async def __aexit__(self, *args):
        pass
    
    def __getattr__(self, name):
        if name in self._fail_on:
            async def failing_method(*args, **kwargs):
                raise PoseStreamError(f"Connection refused: {name}")
            return failing_method
        return super().__getattr__(name)


@pytest.mark.asyncio
async def test_thinking_start_sets_avatar_and_starts_loops():
    """Test that thinking_start sets avatar to thinking and starts LED pattern."""
    mock_call = AsyncMock()
    mock_led = MockLEDClient()
    mock_pose = MockPoseClient()

    with patch("reflexd.call", mock_call):
        import importlib

        import reflexes.thinking as thinking_module

        importlib.reload(thinking_module)

        # Patch LEDStreamClient in the reloaded module
        with patch.object(thinking_module, "LEDStreamClient") as mock_led_class:
            mock_led_class.return_value = mock_led

            # Patch PoseStreamClient in the reloaded module
            with patch.object(thinking_module, "PoseStreamClient") as mock_pose_class:
                mock_pose_class.return_value = mock_pose

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

                # Should set LED pattern to "rainbow" via LED Stream Server
                mock_led.set_pattern.assert_called_once_with("rainbow")
                mock_led.set_fps.assert_called_once_with(20)
                mock_led.set_brightness.assert_called_once_with(1.0)

                # Should call PoseStreamClient methods
                mock_pose.set_pose.assert_called_once()
                pose_call = mock_pose.set_pose.call_args
                assert pose_call.args[0] == "thinking"
                assert pose_call.kwargs["yaw"] == 30.0
                assert pose_call.kwargs["pitch"] == 35.0
                assert pose_call.kwargs["hold_frames"] == 60

                mock_pose.set_smoothing.assert_called_once_with(0.5)
                mock_pose.set_fps.assert_called_once_with(20)


@pytest.mark.asyncio
async def test_thinking_end_sets_happy_and_green_then_neutral_after_delay():
    mock_call = AsyncMock()
    mock_pose = MockPoseClient()

    with patch("reflexd.call", mock_call):
        import importlib

        import reflexes.thinking as thinking_module

        importlib.reload(thinking_module)

        with patch.object(thinking_module, "PoseStreamClient") as mock_pose_class:
            mock_pose_class.return_value = mock_pose

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
            # move_head should not be called (using pose stream instead)
            assert "move_head" not in tools_called

            # Should call set_pose("off") via PoseStreamClient
            mock_pose.set_pose.assert_called_once()
            pose_call = mock_pose.set_pose.call_args
            assert pose_call.args[0] == "off"


@pytest.mark.asyncio
async def test_idle_sets_neutral_pose_and_clears_leds():
    """Test that cleanup_thinking clears LEDs and sets neutral pose."""
    mock_call = AsyncMock()
    mock_pose = MockPoseClient()

    with patch("reflexd.call", mock_call):
        import importlib

        import reflexes.thinking as thinking_module

        importlib.reload(thinking_module)

        with patch.object(thinking_module, "PoseStreamClient") as mock_pose_class:
            mock_pose_class.return_value = mock_pose

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

            # Should call set_pose("off") via PoseStreamClient (from _enter_idle via cleanup)
            off_calls = [c for c in mock_pose.set_pose.call_args_list if c.args[0] == "off"]
            assert len(off_calls) >= 1


@pytest.mark.asyncio
async def test_pose_stream_fallback_to_direct_call():
    """Test fallback to direct MCP call when PoseStreamClient fails."""
    mock_call = AsyncMock()
    # Make PoseStreamClient fail on set_pose by replacing the mock
    mock_pose = MockPoseClient()
    async def failing_set_pose(*args, **kwargs):
        raise PoseStreamError("Connection refused")
    mock_pose.set_pose = failing_set_pose

    with patch("reflexd.call", mock_call):
        import importlib

        import reflexes.thinking as thinking_module

        importlib.reload(thinking_module)

        with patch.object(thinking_module, "PoseStreamClient") as mock_pose_class:
            mock_pose_class.return_value = mock_pose

            handler = get_handler("thinking_start")
            assert handler is not None
            await handler({})

            # Should fall back to direct move_head call
            calls = mock_call.call_args_list
            tools_called = [c.args[0] for c in calls]
            assert "move_head" in tools_called

            # Find the move_head call with thinking pose params
            move_call = next(c for c in calls if c.args[0] == "move_head")
            assert move_call.kwargs["yaw"] == 30
            assert move_call.kwargs["pitch"] == 35
            assert move_call.kwargs["speed"] == 80


@pytest.mark.asyncio
async def test_thinking_idle_pose_called_during_micro_movements():
    """Test that thinking_idle pose is set once during micro-movements."""
    mock_call = AsyncMock()
    mock_pose = MockPoseClient()

    with patch("reflexd.call", mock_call):
        import importlib

        import reflexes.thinking as thinking_module

        importlib.reload(thinking_module)

        with patch.object(thinking_module, "PoseStreamClient") as mock_pose_class:
            mock_pose_class.return_value = mock_pose

            handler = get_handler("thinking_start")
            assert handler is not None
            await handler({})

            # Give time for micro_movements_loop to run
            import asyncio
            await asyncio.sleep(0.01)

            # Should call set_pose("thinking_idle") once with correct params
            thinking_idle_calls = [c for c in mock_pose.set_pose.call_args_list if c.args[0] == "thinking_idle"]
            assert len(thinking_idle_calls) == 1
            pose_call = thinking_idle_calls[0]
            assert pose_call.kwargs["base_yaw"] == 30.0
            assert pose_call.kwargs["base_pitch"] == 35.0
            assert pose_call.kwargs["jitter_yaw"] == 15.0
            assert pose_call.kwargs["jitter_pitch"] == 10.0
            assert pose_call.kwargs["frames"] == 30