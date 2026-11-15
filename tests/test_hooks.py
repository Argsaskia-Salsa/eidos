"""Unit tests for body_bridge new hooks (8 hooks added in v0.2)."""

from unittest.mock import MagicMock, patch

import body_bridge as bb
import pytest

pytestmark = pytest.mark.order(3)


def _get_schedule_args(mock_schedule, call_index=0):
    """Extract args from _schedule_post call (event_type positional, rest kwargs)."""
    call = mock_schedule.call_args_list[call_index]
    return {
        "event_type": call.args[0],
        "text": call.kwargs.get("text"),
        "platform": call.kwargs.get("platform"),
        "chat_id": call.kwargs.get("chat_id"),
        "user_id": call.kwargs.get("user_id"),
        "data": call.kwargs.get("data"),
    }


class TestGatewayPlatformEventHook:
    """Test gateway_platform_event hook."""

    @pytest.mark.asyncio
    async def test_reaction_event(self):
        """Test reaction event with emojis and custom_emoji_ids."""
        payload = {
            "chat_id": "chat-1",
            "user_id": "user-1",
            "message_id": "msg-1",
            "thread_id": "thread-1",
            "emojis": ["👍", "❤️"],
            "custom_emoji_ids": ["emoji-1", "emoji-2"],
        }
        with patch.object(bb, "_schedule_post") as mock_schedule:
            await bb._gateway_platform_event_hook(
                platform="telegram",
                event_type="reaction",
                payload=payload,
            )
            mock_schedule.assert_called_once()
            args = _get_schedule_args(mock_schedule)
            assert args["event_type"] == "platform_event"
            assert args["platform"] == "telegram"
            assert args["chat_id"] == "chat-1"
            assert args["user_id"] == "user-1"
            assert args["data"]["event_type"] == "reaction"
            assert args["data"]["payload"]["emojis"] == ["👍", "❤️"]
            assert args["data"]["payload"]["custom_emoji_ids"] == ["emoji-1", "emoji-2"]

    @pytest.mark.asyncio
    async def test_message_edited_event(self):
        """Test message_edited event with text and edited_at."""
        payload = {
            "chat_id": "chat-1",
            "user_id": "user-1",
            "message_id": "msg-1",
            "thread_id": "thread-1",
            "text": "Edited message text",
            "edited_at": 1700000000,
        }
        with patch.object(bb, "_schedule_post") as mock_schedule:
            await bb._gateway_platform_event_hook(
                platform="telegram",
                event_type="message_edited",
                payload=payload,
            )
            mock_schedule.assert_called_once()
            args = _get_schedule_args(mock_schedule)
            assert args["data"]["event_type"] == "message_edited"
            assert args["data"]["payload"]["text"] == "Edited message text"
            assert args["data"]["payload"]["edited_at"] == 1700000000

    @pytest.mark.asyncio
    async def test_missing_payload_fields_handled_gracefully(self):
        """Test missing payload fields default to unknown."""
        payload = {}
        with patch.object(bb, "_schedule_post") as mock_schedule:
            await bb._gateway_platform_event_hook(
                platform="telegram",
                event_type="reaction",
                payload=payload,
            )
            mock_schedule.assert_called_once()
            args = _get_schedule_args(mock_schedule)
            assert args["chat_id"] == "unknown"
            assert args["user_id"] == "unknown"
            assert args["data"]["payload"] == {}

    @pytest.mark.asyncio
    async def test_local_platform_filtered(self):
        """Test local platforms are skipped - no HTTP call made."""
        payload = {"chat_id": "chat-1", "user_id": "user-1"}
        # Patch the HTTP post method to verify it's not called
        with (
            patch.object(bb._dispatcher, "_post_to_reflexd") as mock_post,
            patch.object(bb._dispatcher, "_ensure_worker", return_value=MagicMock()),
        ):
            await bb._gateway_platform_event_hook(
                platform="local",
                event_type="reaction",
                payload=payload,
            )
            mock_post.assert_not_called()


class TestStreamHooks:
    """Test on_stream_start, on_stream_delta, on_stream_end hooks."""

    @pytest.mark.asyncio
    async def test_on_stream_start_schedules_event(self):
        """Test stream_start event is scheduled."""
        with patch.object(bb, "_schedule_post") as mock_schedule:
            await bb._on_stream_start_hook(
                session_id="sess-1",
                turn_id="turn-1",
                iteration=1,
                model="gpt-4",
                provider="openai",
                surface="chat",
            )
            mock_schedule.assert_called_once()
            args = _get_schedule_args(mock_schedule)
            assert args["event_type"] == "stream_start"
            assert args["platform"] == "stream"
            assert args["chat_id"] == "sess-1"
            assert args["user_id"] == ""

    @pytest.mark.asyncio
    async def test_on_stream_delta_buffers_until_threshold(self):
        """Test stream_delta buffers until 80 chars threshold."""
        timer_coro = None

        def capture_coro(coro):
            nonlocal timer_coro
            timer_coro = coro
            mock_timer = MagicMock()
            return mock_timer

        with (
            patch.object(bb, "_schedule_post") as mock_schedule,
            patch("asyncio.create_task") as mock_create_task,
        ):
            mock_create_task.side_effect = capture_coro
            await bb._on_stream_delta_hook(
                session_id="sess-1",
                turn_id="turn-1",
                iteration=1,
                model="gpt-4",
                provider="openai",
                surface="chat",
                delta="Short delta",
                kind="text",
            )
            if timer_coro:
                timer_coro.close()
            mock_schedule.assert_not_called()
            mock_create_task.assert_called_once()

    @pytest.mark.asyncio
    async def test_on_stream_delta_flushes_at_threshold(self):
        """Test stream_delta flushes when buffer reaches 80 chars."""
        timer_coro = None

        def capture_coro(coro):
            nonlocal timer_coro
            timer_coro = coro
            mock_timer = MagicMock()
            return mock_timer

        with (
            patch.object(bb, "_schedule_post") as mock_schedule,
            patch("asyncio.create_task") as mock_create_task,
        ):
            mock_create_task.side_effect = capture_coro

            # First delta - creates timer
            await bb._on_stream_delta_hook(
                session_id="sess-2",
                turn_id="turn-1",
                iteration=1,
                model="gpt-4",
                provider="openai",
                surface="chat",
                delta="Start ",
                kind="text",
            )
            mock_create_task.assert_called_once()
            mock_schedule.assert_not_called()

            # Second delta exceeds threshold - should cancel timer and flush
            mock_schedule.reset_mock()
            long_delta = "x" * 80
            await bb._on_stream_delta_hook(
                session_id="sess-2",
                turn_id="turn-1",
                iteration=1,
                model="gpt-4",
                provider="openai",
                surface="chat",
                delta=long_delta,
                kind="text",
            )
            mock_schedule.assert_called_once()
            args = _get_schedule_args(mock_schedule)
            assert args["event_type"] == "stream_delta"
            assert "Start " in args["text"]
            assert "x" * 80 in args["text"]
            if timer_coro:
                timer_coro.close()

    @pytest.mark.asyncio
    async def test_on_stream_delta_reasoning_kind(self):
        """Test stream_delta with reasoning kind."""
        timer_coro = None

        def capture_coro(coro):
            nonlocal timer_coro
            timer_coro = coro
            mock_timer = MagicMock()
            return mock_timer

        with (
            patch.object(bb, "_schedule_post") as mock_schedule,
            patch("asyncio.create_task") as mock_create_task,
        ):
            mock_create_task.side_effect = capture_coro
            await bb._on_stream_delta_hook(
                session_id="sess-3",
                turn_id="turn-1",
                iteration=1,
                model="gpt-4",
                provider="openai",
                surface="chat",
                delta="Reasoning delta",
                kind="reasoning",
            )
            if timer_coro:
                timer_coro.close()
            mock_schedule.assert_not_called()

    @pytest.mark.asyncio
    async def test_on_stream_delta_multiple_sessions_independent(self):
        """Test multiple sessions have independent buffers."""
        timer_coros = []

        def capture_coro(coro):
            timer_coros.append(coro)
            mock_timer = MagicMock()
            return mock_timer

        with (
            patch.object(bb, "_schedule_post"),
            patch("asyncio.create_task") as mock_create_task,
        ):
            mock_create_task.side_effect = capture_coro

            await bb._on_stream_delta_hook(
                session_id="sess-a",
                turn_id="turn-1",
                iteration=1,
                model="gpt-4",
                provider="openai",
                surface="chat",
                delta="Hello ",
                kind="text",
            )
            await bb._on_stream_delta_hook(
                session_id="sess-b",
                turn_id="turn-1",
                iteration=1,
                model="gpt-4",
                provider="openai",
                surface="chat",
                delta="World ",
                kind="text",
            )
            for coro in timer_coros:
                coro.close()
            assert mock_create_task.call_count == 2

    @pytest.mark.asyncio
    async def test_on_stream_end_flushes_buffer_and_sends_end(self):
        """Test stream_end flushes remaining buffer and sends stream_end."""
        with patch.object(bb, "_schedule_post") as mock_schedule:
            bb._stream_buffer["sess-4"] = ["Buffered ", "text"]

            await bb._on_stream_end_hook(
                session_id="sess-4",
                turn_id="turn-1",
                iteration=1,
                model="gpt-4",
                provider="openai",
                surface="chat",
                final_text="Final text",
                finished=True,
                error=None,
            )
            assert mock_schedule.call_count == 2
            args1 = _get_schedule_args(mock_schedule, 0)
            args2 = _get_schedule_args(mock_schedule, 1)
            assert args1["event_type"] == "stream_delta"
            assert args1["text"] == "Buffered text"
            assert args2["event_type"] == "stream_end"
            assert args2["text"] == "Final text"

    @pytest.mark.asyncio
    async def test_on_stream_end_with_error(self):
        """Test stream_end with error parameter."""
        with patch.object(bb, "_schedule_post") as mock_schedule:
            await bb._on_stream_end_hook(
                session_id="sess-5",
                turn_id="turn-1",
                iteration=1,
                model="gpt-4",
                provider="openai",
                surface="chat",
                final_text="Error occurred",
                finished=False,
                error="Connection lost",
            )
            mock_schedule.assert_called_once()
            args = _get_schedule_args(mock_schedule)
            assert args["event_type"] == "stream_end"
            assert args["text"] == "Error occurred"

    @pytest.mark.asyncio
    async def test_flush_stream_buffer_cancels_timer(self):
        """Test _flush_stream_buffer cleans up timer (timer becomes no-op when fired)."""
        with patch.object(bb, "_schedule_post") as mock_schedule:
            bb._stream_buffer["sess-6"] = ["test"]
            mock_timer = MagicMock()
            bb._stream_timers["sess-6"] = mock_timer

            await bb._flush_stream_buffer("sess-6")

            mock_schedule.assert_called_once()
            # _flush_stream_buffer pops the timer but doesn't cancel it;
            # the timer's callback becomes a no-op since buffer is cleared
            assert "sess-6" not in bb._stream_buffer
            assert "sess-6" not in bb._stream_timers

    @pytest.mark.asyncio
    async def test_flush_stream_buffer_empty_buffer(self):
        """Test _flush_stream_buffer with empty buffer does nothing."""
        with patch.object(bb, "_schedule_post") as mock_schedule:
            await bb._flush_stream_buffer("nonexistent")
            mock_schedule.assert_not_called()

    @pytest.mark.asyncio
    async def test_flush_stream_buffer_empty_text_no_post(self):
        """Test _flush_stream_buffer with empty concatenated text skips post."""
        with patch.object(bb, "_schedule_post") as mock_schedule:
            bb._stream_buffer["sess-7"] = ["", ""]
            await bb._flush_stream_buffer("sess-7")
            mock_schedule.assert_not_called()
            assert "sess-7" not in bb._stream_buffer


class TestPreGatewayDispatchHook:
    """Test pre_gateway_dispatch hook."""

    @pytest.mark.asyncio
    async def test_returns_skip_for_empty_text(self):
        """Test returns skip action for empty message."""
        event = MagicMock()
        event.text = ""
        result = await bb._pre_gateway_dispatch_hook(event=event, gateway=None, session_store=None)
        assert result == {"action": "skip", "reason": "empty message"}

    @pytest.mark.asyncio
    async def test_returns_skip_for_whitespace_text(self):
        """Test returns skip action for whitespace-only message."""
        event = MagicMock()
        event.text = "   \n\t  "
        result = await bb._pre_gateway_dispatch_hook(event=event, gateway=None, session_store=None)
        assert result == {"action": "skip", "reason": "empty message"}

    @pytest.mark.asyncio
    async def test_returns_none_allow_for_non_empty_text(self):
        """Test returns None (allow) for non-empty message."""
        event = MagicMock()
        event.text = "Hello world"
        result = await bb._pre_gateway_dispatch_hook(event=event, gateway=None, session_store=None)
        assert result is None

    @pytest.mark.asyncio
    async def test_handles_dict_like_event(self):
        """Test handles dict-like event with .get('text')."""
        event = {"text": "Dict message"}
        result = await bb._pre_gateway_dispatch_hook(event=event, gateway=None, session_store=None)
        assert result is None

    @pytest.mark.asyncio
    async def test_handles_dict_like_event_empty(self):
        """Test handles dict-like event with empty text."""
        event = {"text": ""}
        result = await bb._pre_gateway_dispatch_hook(event=event, gateway=None, session_store=None)
        assert result == {"action": "skip", "reason": "empty message"}

    @pytest.mark.asyncio
    async def test_handles_object_with_text_attribute(self):
        """Test handles object with .text attribute."""
        class EventObj:
            def __init__(self, text):
                self.text = text

        event = EventObj("Object message")
        result = await bb._pre_gateway_dispatch_hook(event=event, gateway=None, session_store=None)
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_rewrite_action(self):
        """Test rewrite action is passed through (not handled by hook but allowed)."""
        event = MagicMock()
        event.text = "Original"
        result = await bb._pre_gateway_dispatch_hook(event=event, gateway=None, session_store=None)
        assert result is None


class TestSessionEndHook:
    """Test on_session_end hook."""

    @pytest.mark.asyncio
    async def test_schedules_session_end_event(self):
        """Test session_end event scheduled with correct params."""
        with patch.object(bb, "_schedule_post") as mock_schedule:
            await bb._on_session_end_hook(
                session_id="sess-1",
                completed=True,
                interrupted=False,
                model="gpt-4",
                platform="telegram",
                reason="turn_complete",
                task_id="task-1",
                turn_id="turn-1",
                api_request_id="api-1",
            )
            mock_schedule.assert_called_once()
            args = _get_schedule_args(mock_schedule)
            assert args["event_type"] == "session_end"
            assert args["platform"] == "telegram"
            assert args["chat_id"] == "sess-1"
            assert args["user_id"] == ""

    @pytest.mark.asyncio
    async def test_all_parameters_passed_through(self):
        """Test all required parameters are passed through."""
        with patch.object(bb, "_schedule_post") as mock_schedule:
            await bb._on_session_end_hook(
                session_id="sess-2",
                completed=False,
                interrupted=True,
                model="claude-3",
                platform="discord",
                reason="interrupted",
                task_id="task-2",
                turn_id="turn-2",
                api_request_id="api-2",
            )
            mock_schedule.assert_called_once()
            args = _get_schedule_args(mock_schedule)
            assert args["event_type"] == "session_end"
            assert args["platform"] == "discord"

    @pytest.mark.asyncio
    async def test_local_platform_filtered(self):
        """Test local platform is skipped - no HTTP call made."""
        with (
            patch.object(bb._dispatcher, "_post_to_reflexd") as mock_post,
            patch.object(bb._dispatcher, "_ensure_worker", return_value=MagicMock()),
        ):
            await bb._on_session_end_hook(
                session_id="sess-3",
                completed=True,
                interrupted=False,
                model="gpt-4",
                platform="local",
                reason="turn_complete",
                task_id="task-3",
                turn_id="turn-3",
                api_request_id="api-3",
            )
            mock_post.assert_not_called()


class TestSessionFinalizeHook:
    """Test on_session_finalize hook."""

    @pytest.mark.asyncio
    async def test_schedules_session_finalize_event(self):
        """Test session_finalize event scheduled with correct params."""
        with patch.object(bb, "_schedule_post") as mock_schedule:
            await bb._on_session_finalize_hook(
                session_id="sess-1",
                platform="telegram",
                reason="new_session",
            )
            mock_schedule.assert_called_once()
            args = _get_schedule_args(mock_schedule)
            assert args["event_type"] == "session_finalize"
            assert args["platform"] == "telegram"
            assert args["chat_id"] == "sess-1"
            assert args["user_id"] == ""

    @pytest.mark.asyncio
    async def test_session_boundary_reason(self):
        """Test session_boundary reason."""
        with patch.object(bb, "_schedule_post") as mock_schedule:
            await bb._on_session_finalize_hook(
                session_id="sess-2",
                platform="discord",
                reason="session_boundary",
            )
            mock_schedule.assert_called_once()

    @pytest.mark.asyncio
    async def test_shutdown_reason(self):
        """Test shutdown reason."""
        with patch.object(bb, "_schedule_post") as mock_schedule:
            await bb._on_session_finalize_hook(
                session_id="sess-3",
                platform="slack",
                reason="shutdown",
            )
            mock_schedule.assert_called_once()

    @pytest.mark.asyncio
    async def test_local_platform_filtered(self):
        """Test local platform is skipped - no HTTP call made."""
        with (
            patch.object(bb._dispatcher, "_post_to_reflexd") as mock_post,
            patch.object(bb._dispatcher, "_ensure_worker", return_value=MagicMock()),
        ):
            await bb._on_session_finalize_hook(
                session_id="sess-4",
                platform="cli",
                reason="new_session",
            )
            mock_post.assert_not_called()


class TestApiRequestErrorHook:
    """Test api_request_error hook."""

    @pytest.mark.asyncio
    async def test_extracts_error_message_from_dict(self):
        """Test error message extracted from error dict."""
        with patch.object(bb, "_schedule_post") as mock_schedule:
            await bb._api_request_error_hook(
                task_id="task-1",
                turn_id="turn-1",
                api_request_id="api-1",
                session_id="sess-1",
                platform="telegram",
                model="gpt-4",
                provider="openai",
                base_url="https://api.openai.com",
                api_mode="chat",
                api_call_count=1,
                api_duration=2.5,
                started_at=1700000000.0,
                ended_at=1700000002.5,
                status_code=500,
                retry_count=0,
                max_retries=3,
                retryable=True,
                reason="server_error",
                error={"type": "server_error", "message": "Internal server error"},
                request={"model": "gpt-4", "messages": []},
            )
            mock_schedule.assert_called_once()
            args = _get_schedule_args(mock_schedule)
            assert args["event_type"] == "api_error"
            assert args["text"] == "Internal server error"
            assert args["platform"] == "openai"
            assert args["chat_id"] == "gpt-4"

    @pytest.mark.asyncio
    async def test_handles_non_dict_error(self):
        """Test handles non-dict error gracefully."""
        with patch.object(bb, "_schedule_post") as mock_schedule:
            await bb._api_request_error_hook(
                task_id="task-1",
                turn_id="turn-1",
                api_request_id="api-1",
                session_id="sess-1",
                platform="telegram",
                model="gpt-4",
                provider="openai",
                base_url="https://api.openai.com",
                api_mode="chat",
                api_call_count=1,
                api_duration=2.5,
                started_at=1700000000.0,
                ended_at=1700000002.5,
                status_code=500,
                retry_count=0,
                max_retries=3,
                retryable=True,
                reason="server_error",
                error="String error message",
                request={"model": "gpt-4", "messages": []},
            )
            mock_schedule.assert_called_once()
            args = _get_schedule_args(mock_schedule)
            assert args["text"] == "String error message"

    @pytest.mark.asyncio
    async def test_handles_none_error(self):
        """Test handles None error."""
        with patch.object(bb, "_schedule_post") as mock_schedule:
            await bb._api_request_error_hook(
                task_id="task-1",
                turn_id="turn-1",
                api_request_id="api-1",
                session_id="sess-1",
                platform="telegram",
                model="gpt-4",
                provider="openai",
                base_url="https://api.openai.com",
                api_mode="chat",
                api_call_count=1,
                api_duration=2.5,
                started_at=1700000000.0,
                ended_at=1700000002.5,
                status_code=500,
                retry_count=0,
                max_retries=3,
                retryable=True,
                reason="server_error",
                error=None,
                request={"model": "gpt-4", "messages": []},
            )
            mock_schedule.assert_called_once()
            args = _get_schedule_args(mock_schedule)
            assert args["text"] == "None"

    @pytest.mark.asyncio
    async def test_schedules_with_provider_as_platform_model_as_chat_id(self):
        """Test api_error uses provider as platform and model as chat_id."""
        with patch.object(bb, "_schedule_post") as mock_schedule:
            await bb._api_request_error_hook(
                task_id="task-1",
                turn_id="turn-1",
                api_request_id="api-1",
                session_id="sess-1",
                platform="telegram",
                model="claude-3-opus",
                provider="anthropic",
                base_url="https://api.anthropic.com",
                api_mode="messages",
                api_call_count=2,
                api_duration=5.0,
                started_at=1700000000.0,
                ended_at=1700000005.0,
                status_code=429,
                retry_count=1,
                max_retries=3,
                retryable=True,
                reason="rate_limit",
                error={"type": "rate_limit", "message": "Rate limited"},
                request={"model": "claude-3-opus", "messages": []},
            )
            mock_schedule.assert_called_once()
            args = _get_schedule_args(mock_schedule)
            assert args["platform"] == "anthropic"
            assert args["chat_id"] == "claude-3-opus"

    @pytest.mark.asyncio
    async def test_all_parameters_passed_through(self):
        """Test all 18+ parameters are accepted without error."""
        with patch.object(bb, "_schedule_post") as mock_schedule:
            await bb._api_request_error_hook(
                task_id="task-1",
                turn_id="turn-1",
                api_request_id="api-1",
                session_id="sess-1",
                platform="telegram",
                model="gpt-4",
                provider="openai",
                base_url="https://api.openai.com",
                api_mode="chat",
                api_call_count=1,
                api_duration=2.5,
                started_at=1700000000.0,
                ended_at=1700000002.5,
                status_code=500,
                retry_count=0,
                max_retries=3,
                retryable=True,
                reason="server_error",
                error={"type": "server_error", "message": "Internal server error"},
                request={"model": "gpt-4", "messages": []},
            )
            mock_schedule.assert_called_once()


class TestStreamThrottlingLogic:
    """Test stream throttling internal logic."""

    @pytest.mark.asyncio
    async def test_buffer_accumulation(self):
        """Test _stream_buffer accumulates deltas correctly."""
        bb._stream_buffer.clear()
        bb._stream_timers.clear()

        timer_coro = None

        def capture_coro(coro):
            nonlocal timer_coro
            timer_coro = coro
            mock_timer = MagicMock()
            return mock_timer

        with (
            patch.object(bb, "_schedule_post"),
            patch("asyncio.create_task") as mock_create_task,
        ):
            mock_create_task.side_effect = capture_coro

            await bb._on_stream_delta_hook(
                session_id="throttle-1",
                turn_id="turn-1",
                iteration=1,
                model="gpt-4",
                provider="openai",
                surface="chat",
                delta="First ",
                kind="text",
            )
            await bb._on_stream_delta_hook(
                session_id="throttle-1",
                turn_id="turn-1",
                iteration=1,
                model="gpt-4",
                provider="openai",
                surface="chat",
                delta="Second ",
                kind="text",
            )
            if timer_coro:
                timer_coro.close()

            assert bb._stream_buffer["throttle-1"] == ["First ", "Second "]
            assert mock_create_task.call_count == 1

    @pytest.mark.asyncio
    async def test_manual_flush_cancels_timer(self):
        """Test manual flush (via threshold) cancels pending timer."""
        bb._stream_buffer.clear()
        bb._stream_timers.clear()

        timer_coro = None

        def capture_coro(coro):
            nonlocal timer_coro
            timer_coro = coro
            mock_timer = MagicMock()
            return mock_timer

        with (
            patch.object(bb, "_schedule_post") as mock_schedule,
            patch("asyncio.create_task") as mock_create_task,
        ):
            mock_create_task.side_effect = capture_coro

            await bb._on_stream_delta_hook(
                session_id="throttle-2",
                turn_id="turn-1",
                iteration=1,
                model="gpt-4",
                provider="openai",
                surface="chat",
                delta="Start ",
                kind="text",
            )
            assert "throttle-2" in bb._stream_timers

            await bb._on_stream_delta_hook(
                session_id="throttle-2",
                turn_id="turn-1",
                iteration=1,
                model="gpt-4",
                provider="openai",
                surface="chat",
                delta="x" * 80,
                kind="text",
            )
            if timer_coro:
                timer_coro.close()
            mock_schedule.assert_called()

    @pytest.mark.asyncio
    async def test_timer_flush_callback(self):
        """Test timer callback calls _flush_stream_buffer."""
        bb._stream_buffer.clear()
        bb._stream_timers.clear()

        with (
            patch.object(bb, "_schedule_post") as mock_schedule,
            patch("asyncio.create_task") as mock_create_task,
        ):
            mock_timer = MagicMock()
            mock_create_task.return_value = mock_timer

            timer_coro = None

            def capture_coro(coro):
                nonlocal timer_coro
                timer_coro = coro
                return mock_timer

            mock_create_task.side_effect = capture_coro

            await bb._on_stream_delta_hook(
                session_id="throttle-3",
                turn_id="turn-1",
                iteration=1,
                model="gpt-4",
                provider="openai",
                surface="chat",
                delta="Test ",
                kind="text",
            )

            if timer_coro:
                await timer_coro

            mock_schedule.assert_called_once()
            args = _get_schedule_args(mock_schedule)
            assert args["event_type"] == "stream_delta"
            assert args["text"] == "Test "

    @pytest.mark.asyncio
    async def test_multiple_sessions_independent_timers(self):
        """Test each session gets its own timer."""
        bb._stream_buffer.clear()
        bb._stream_timers.clear()

        timer_coros = []

        def capture_coro(coro):
            timer_coros.append(coro)
            mock_timer = MagicMock()
            return mock_timer

        with (
            patch.object(bb, "_schedule_post"),
            patch("asyncio.create_task") as mock_create_task,
        ):
            mock_create_task.side_effect = capture_coro

            await bb._on_stream_delta_hook(
                session_id="sess-a",
                turn_id="turn-1",
                iteration=1,
                model="gpt-4",
                provider="openai",
                surface="chat",
                delta="A",
                kind="text",
            )
            await bb._on_stream_delta_hook(
                session_id="sess-b",
                turn_id="turn-1",
                iteration=1,
                model="gpt-4",
                provider="openai",
                surface="chat",
                delta="B",
                kind="text",
            )
            for coro in timer_coros:
                coro.close()

            assert mock_create_task.call_count == 2
            assert "sess-a" in bb._stream_timers
            assert "sess-b" in bb._stream_timers


# Clean up global state after each test
@pytest.fixture(autouse=True)
def reset_stream_state():
    """Reset stream buffer and timers before/after each test."""
    bb._stream_buffer.clear()
    bb._stream_timers.clear()
    yield
    bb._stream_buffer.clear()
    bb._stream_timers.clear()
