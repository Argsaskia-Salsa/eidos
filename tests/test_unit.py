"""Tests for body_bridge plugin - covers current functionality before refactoring."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import body_bridge as bb
import httpx
import pytest

pytestmark = pytest.mark.order(1)


class TestPlatformFilter:
    """Test platform filtering logic."""

    def test_local_platforms_skipped(self):
        """Local, desktop, cli platforms should be skipped."""
        for platform in ("local", "desktop", "cli", "LOCAL", "Desktop", "CLI"):
            assert bb._is_local_platform(platform) is True

    def test_remote_platforms_allowed(self):
        """Telegram, discord, slack, etc. should be allowed."""
        for platform in ("telegram", "discord", "slack", "whatsapp", "matrix"):
            assert bb._is_local_platform(platform) is False

    def test_none_platform_treated_as_unknown(self):
        """None platform should not be filtered as local."""
        assert bb._is_local_platform(None) is False

    def test_empty_platform_treated_as_unknown(self):
        """Empty string platform should not be filtered as local."""
        assert bb._is_local_platform("") is False


class TestPayloadBuilder:
    """Test payload construction for different event types."""

    def test_user_message_payload(self):
        payload = bb._build_payload(
            "user_message",
            text="Hello world",
            platform="telegram",
            chat_id="123",
            user_id="456",
        )
        assert payload["name"] == "user_message"
        assert payload["data"]["text"] == "Hello world"
        assert payload["data"]["platform"] == "telegram"
        assert payload["data"]["chat_id"] == "123"
        assert payload["data"]["user_id"] == "456"
        assert payload["type"] == "inject"

    def test_user_response_payload(self):
        payload = bb._build_payload(
            "user_response",
            text="Assistant reply",
            platform="discord",
            chat_id="789",
            user_id="012",
        )
        assert payload["name"] == "user_response"
        assert payload["data"]["text"] == "Assistant reply"

    def test_thinking_start_payload_empty_text(self):
        payload = bb._build_payload(
            "thinking_start",
            text="",
            platform="telegram",
            chat_id="123",
            user_id="456",
        )
        assert payload["name"] == "thinking_start"
        assert payload["data"]["text"] == ""

    def test_thinking_end_payload_empty_text(self):
        payload = bb._build_payload(
            "thinking_end",
            text="",
            platform="telegram",
            chat_id="123",
            user_id="456",
        )
        assert payload["name"] == "thinking_end"
        assert payload["data"]["text"] == ""

    def test_payload_handles_none_text(self):
        payload = bb._build_payload(
            "user_message",
            text=None,
            platform="telegram",
            chat_id="123",
            user_id="456",
        )
        assert payload["data"]["text"] == ""


class TestChatIdentityExtractor:
    """Test chat_id/user_id extraction from conversation history."""

    def test_extract_from_last_user_message_with_metadata(self):
        history = [
            {"role": "assistant", "content": "Hi"},
            {
                "role": "user",
                "content": "Hello",
                "metadata": {"chat_id": "chat-1", "user_id": "user-1"},
            },
        ]
        chat_id, user_id = bb._extract_chat_user_from_history(history)
        assert chat_id == "chat-1"
        assert user_id == "user-1"

    def test_extract_from_last_user_message_with_direct_fields(self):
        history = [
            {"role": "assistant", "content": "Hi"},
            {"role": "user", "content": "Hello", "chat_id": "chat-2", "user_id": "user-2"},
        ]
        chat_id, user_id = bb._extract_chat_user_from_history(history)
        assert chat_id == "chat-2"
        assert user_id == "user-2"

    def test_extract_falls_back_to_session_id(self):
        history = [
            {"role": "assistant", "content": "Hi"},
            {"role": "user", "content": "Hello"},
        ]
        chat_id, user_id = bb._extract_chat_user_from_history(history, session_id="sess-1")
        assert chat_id == "sess-1"
        assert user_id == "unknown"

    def test_empty_history_returns_unknown(self):
        chat_id, user_id = bb._extract_chat_user_from_history([])
        assert chat_id == "unknown"
        assert user_id == "unknown"

    def test_none_history_returns_unknown(self):
        chat_id, user_id = bb._extract_chat_user_from_history(None)
        assert chat_id == "unknown"
        assert user_id == "unknown"

    def test_non_dict_messages_skipped(self):
        history = [
            "not a dict",
            {"role": "user", "content": "Hello", "chat_id": "chat-3", "user_id": "user-3"},
        ]
        chat_id, user_id = bb._extract_chat_user_from_history(history)
        assert chat_id == "chat-3"
        assert user_id == "user-3"


class TestPostGatewayAdmissionHook:
    """Test post_gateway_admission hook."""

    @pytest.mark.asyncio
    async def test_hook_returns_false_none_for_continuation(self):
        result = await bb._post_gateway_admission_hook(
            session_key="sess-1",
            platform="telegram",
            source={"chat_id": "123", "user_id": "456", "platform": "telegram"},
            message_id="msg-1",
            text="Hello",
        )
        assert result == (False, None)

    @pytest.mark.asyncio
    async def test_hook_skips_empty_text(self):
        result = await bb._post_gateway_admission_hook(
            session_key="sess-1",
            platform="telegram",
            source={"chat_id": "123", "user_id": "456"},
            message_id="msg-1",
            text="",
        )
        assert result == (False, None)

    @pytest.mark.asyncio
    async def test_hook_skips_whitespace_only_text(self):
        result = await bb._post_gateway_admission_hook(
            session_key="sess-1",
            platform="telegram",
            source={"chat_id": "123", "user_id": "456"},
            message_id="msg-1",
            text="   ",
        )
        assert result == (False, None)

    @pytest.mark.asyncio
    async def test_hook_handles_none_source(self):
        result = await bb._post_gateway_admission_hook(
            session_key="sess-1",
            platform="telegram",
            source=None,
            message_id="msg-1",
            text="Hello",
        )
        assert result == (False, None)

    @pytest.mark.asyncio
    async def test_hook_uses_source_platform_over_param(self):
        with patch.object(bb, "_schedule_post") as mock_schedule:
            await bb._post_gateway_admission_hook(
                session_key="sess-1",
                platform="discord",
                source={"chat_id": "123", "user_id": "456", "platform": "telegram"},
                message_id="msg-1",
                text="Hello",
            )
            mock_schedule.assert_called_once()
            args = mock_schedule.call_args
            assert args.kwargs["platform"] == "telegram"


class TestPostLlmCallHook:
    """Test post_llm_call hook."""

    @pytest.mark.asyncio
    async def test_hook_returns_none_for_observer(self):
        result = await bb._post_llm_call_hook(
            session_id="sess-1",
            task_id="task-1",
            turn_id="turn-1",
            user_message="Hello",
            assistant_response="Hi there",
            conversation_history=[],
            model="gpt-4",
            platform="telegram",
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_hook_skips_empty_response(self):
        result = await bb._post_llm_call_hook(
            session_id="sess-1",
            task_id="task-1",
            turn_id="turn-1",
            user_message="Hello",
            assistant_response="",
            conversation_history=[],
            model="gpt-4",
            platform="telegram",
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_hook_extracts_chat_user_from_history(self):
        history = [
            {"role": "user", "content": "Hello", "chat_id": "chat-1", "user_id": "user-1"},
        ]
        with patch.object(bb, "_schedule_post") as mock_schedule:
            await bb._post_llm_call_hook(
                session_id="sess-1",
                task_id="task-1",
                turn_id="turn-1",
                user_message="Hello",
                assistant_response="Hi there",
                conversation_history=history,
                model="gpt-4",
                platform="telegram",
            )
            mock_schedule.assert_called_once()
            args = mock_schedule.call_args
            assert args.kwargs["chat_id"] == "chat-1"
            assert args.kwargs["user_id"] == "user-1"


class TestLlmExecutionMiddleware:
    """Test llm_execution middleware."""

    def test_middleware_returns_request_when_no_next_call(self):
        request = {"messages": []}
        result = bb._llm_execution_middleware(request=request, next_call=None)
        assert result is request

    def test_middleware_skips_local_platform(self):
        request = {"messages": []}
        next_call = MagicMock(return_value={"response": "ok"})
        result = bb._llm_execution_middleware(
            request=request,
            next_call=next_call,
            platform="local",
            session_id="sess-1",
            chat_id="chat-1",
            user_id="user-1",
        )
        next_call.assert_called_once_with(request)
        assert result == {"response": "ok"}

    def test_middleware_calls_next_call_for_remote_platform(self):
        request = {"messages": []}
        next_call = MagicMock(return_value={"response": "ok"})
        with patch("asyncio.run_coroutine_threadsafe") as mock_run:
            mock_future = MagicMock()

            def consume_coro(coro, loop):
                if hasattr(coro, "close"):
                    coro.close()
                return mock_future

            mock_run.side_effect = consume_coro

            bb._llm_execution_middleware(
                request=request,
                next_call=next_call,
                platform="telegram",
                session_id="sess-1",
                chat_id="chat-1",
                user_id="user-1",
            )
            next_call.assert_called_once_with(request)
            assert mock_run.call_count == 2
            calls = [c.args[0].cr_code.co_name for c in mock_run.call_args_list]
            assert calls == ["_post", "_post"]

    def test_middleware_fires_thinking_end_even_on_exception(self):
        request = {"messages": []}
        next_call = MagicMock(side_effect=Exception("LLM failed"))
        with patch("asyncio.run_coroutine_threadsafe") as mock_run:
            mock_future = MagicMock()

            def consume_coro(coro, loop):
                # Consume the coroutine to avoid "never awaited" warning
                if hasattr(coro, "close"):
                    coro.close()
                return mock_future

            mock_run.side_effect = consume_coro

            with pytest.raises(Exception, match="LLM failed"):
                bb._llm_execution_middleware(
                    request=request,
                    next_call=next_call,
                    platform="telegram",
                    session_id="sess-1",
                    chat_id="chat-1",
                    user_id="user-1",
                )
            calls = [c.args[0].cr_code.co_name for c in mock_run.call_args_list]
            assert calls == ["_post", "_post"]


class TestWorkerLoop:
    """Test worker loop management (via EventDispatcher)."""

    def test_ensure_worker_creates_loop(self):
        loop = bb._dispatcher._ensure_worker()
        assert loop is not None
        assert loop.is_running()

    def test_ensure_worker_returns_same_loop(self):
        loop1 = bb._dispatcher._ensure_worker()
        loop2 = bb._dispatcher._ensure_worker()
        assert loop1 is loop2

    def test_schedule_post_uses_worker_loop(self):
        with patch.object(bb._dispatcher, "_ensure_worker") as mock_ensure:
            mock_loop = MagicMock()
            mock_loop.is_running.return_value = True
            mock_ensure.return_value = mock_loop

            with patch("asyncio.run_coroutine_threadsafe") as mock_run:
                mock_future = MagicMock()

                def consume_coro(coro, loop):
                    if hasattr(coro, "close"):
                        coro.close()
                    return mock_future

                mock_run.side_effect = consume_coro

                bb._dispatcher.schedule_post("user_message", "text", "telegram", "chat-1", "user-1")
                mock_run.assert_called_once()


class TestHttpClient:
    """Test httpx client management (reuse, close)."""

    @pytest.mark.asyncio
    async def test_get_client_creates_client(self):
        dispatcher = bb.EventDispatcher()
        dispatcher._worker_loop = MagicMock()
        dispatcher._worker_loop.is_running.return_value = True

        client = await dispatcher._get_client()
        assert client is not None
        assert isinstance(client, httpx.AsyncClient)
        assert not client.is_closed

    @pytest.mark.asyncio
    async def test_get_client_reuses_client(self):
        dispatcher = bb.EventDispatcher()
        dispatcher._worker_loop = MagicMock()
        dispatcher._worker_loop.is_running.return_value = True

        client1 = await dispatcher._get_client()
        client2 = await dispatcher._get_client()
        assert client1 is client2

    @pytest.mark.asyncio
    async def test_get_client_recreates_after_close(self):
        dispatcher = bb.EventDispatcher()
        dispatcher._worker_loop = MagicMock()
        dispatcher._worker_loop.is_running.return_value = True

        client1 = await dispatcher._get_client()
        await client1.aclose()

        client2 = await dispatcher._get_client()
        assert client1 is not client2
        assert not client2.is_closed

    def test_close_client_schedules_aclose_on_loop(self):
        dispatcher = bb.EventDispatcher()
        mock_loop = MagicMock()
        mock_loop.is_running.return_value = True

        dispatcher._client = AsyncMock()
        dispatcher._client.is_closed = False

        dispatcher._close_client(mock_loop)

        mock_loop.call_soon_threadsafe.assert_called_once()
        assert dispatcher._client is None

    def test_close_client_skips_if_none(self):
        dispatcher = bb.EventDispatcher()
        mock_loop = MagicMock()
        dispatcher._client = None

        dispatcher._close_client(mock_loop)

        mock_loop.call_soon_threadsafe.assert_not_called()

    def test_close_client_skips_if_already_closed(self):
        dispatcher = bb.EventDispatcher()
        mock_loop = MagicMock()
        dispatcher._client = MagicMock()
        dispatcher._client.is_closed = True

        dispatcher._close_client(mock_loop)

        mock_loop.call_soon_threadsafe.assert_not_called()


class TestSchedulePost:
    """Test _schedule_post function (via EventDispatcher)."""

    def test_schedule_post_skips_local_platform(self):
        with patch.object(bb._dispatcher, "_ensure_worker") as mock_ensure:
            bb._dispatcher.schedule_post("user_message", "text", "local", "chat-1", "user-1")
            mock_ensure.assert_not_called()

    def test_schedule_post_skips_desktop_platform(self):
        with patch.object(bb._dispatcher, "_ensure_worker") as mock_ensure:
            bb._dispatcher.schedule_post("user_message", "text", "desktop", "chat-1", "user-1")
            mock_ensure.assert_not_called()

    def test_schedule_post_skips_cli_platform(self):
        with patch.object(bb._dispatcher, "_ensure_worker") as mock_ensure:
            bb._dispatcher.schedule_post("user_message", "text", "cli", "chat-1", "user-1")
            mock_ensure.assert_not_called()


class TestRegister:
    """Test plugin registration."""

    def test_register_calls_all_registrations(self):
        mock_ctx = MagicMock()
        with patch.object(bb._dispatcher, "_ensure_worker"):
            bb.register(mock_ctx)

        assert mock_ctx.register_hook.call_count == 2
        assert mock_ctx.register_middleware.call_count == 1

        hook_names = [call.args[0] for call in mock_ctx.register_hook.call_args_list]
        assert "post_gateway_admission" in hook_names
        assert "post_llm_call" in hook_names

        middleware_name = mock_ctx.register_middleware.call_args.args[0]
        assert middleware_name == "llm_execution"


class TestHttpPost:
    """Test HTTP POST to reflexd (mocked)."""

    @pytest.mark.asyncio
    async def test_post_sends_correct_jsonrpc_envelope(self):
        mock_response = AsyncMock()
        mock_response.status_code = 200

        # Use isolated dispatcher to avoid state leakage
        dispatcher = bb.EventDispatcher()
        dispatcher._worker_loop = MagicMock()
        dispatcher._worker_loop.is_running.return_value = True

        with patch("httpx.AsyncClient.post", return_value=mock_response) as mock_post:
            await dispatcher._post_to_reflexd(
                event_type="user_message",
                text="Hello",
                platform="telegram",
                chat_id="123",
                user_id="456",
            )

            mock_post.assert_called_once()
            call_args = mock_post.call_args
            assert call_args.kwargs["json"]["jsonrpc"] == "2.0"
            assert call_args.kwargs["json"]["method"] == "tools/call"
            assert call_args.kwargs["json"]["params"]["name"] == "inject_event"
            assert call_args.kwargs["json"]["params"]["arguments"]["name"] == "user_message"

    @pytest.mark.asyncio
    async def test_post_logs_warning_on_http_error(self):
        mock_response = AsyncMock()
        mock_response.status_code = 500

        dispatcher = bb.EventDispatcher()
        dispatcher._worker_loop = MagicMock()
        dispatcher._worker_loop.is_running.return_value = True

        with (
            patch("httpx.AsyncClient.post", return_value=mock_response),
            patch.object(bb.logger, "warning") as mock_warn,
        ):
            await dispatcher._post_to_reflexd(
                event_type="user_message",
                text="Hello",
                platform="telegram",
                chat_id="123",
                user_id="456",
            )
            mock_warn.assert_called_once()
            # Logger uses format string with %s placeholders
            assert "HTTP %s" in mock_warn.call_args.args[0]

    @pytest.mark.asyncio
    async def test_post_logs_warning_on_exception(self):
        dispatcher = bb.EventDispatcher()
        dispatcher._worker_loop = MagicMock()
        dispatcher._worker_loop.is_running.return_value = True

        with (
            patch("httpx.AsyncClient.post", side_effect=Exception("Connection refused")),
            patch.object(bb.logger, "warning") as mock_warn,
        ):
            await dispatcher._post_to_reflexd(
                event_type="user_message",
                text="Hello",
                platform="telegram",
                chat_id="123",
                user_id="456",
            )
            mock_warn.assert_called_once()
            assert "failed" in mock_warn.call_args.args[0].lower()


# Fixture to ensure clean state between tests
@pytest.fixture(autouse=True)
def reset_dispatcher():
    """Reset dispatcher state before each test."""
    # Cancel any pending futures
    for fut in list(bb._dispatcher._pending_futures):
        fut.cancel()
    bb._dispatcher._pending_futures.clear()
    bb._dispatcher._worker_loop = None
    bb._dispatcher._worker_thread = None
    bb._dispatcher._worker_ready.clear()
    yield
    # Don't call shutdown() here as the worker loop/thread are mocked
    # Reset state again after test
    for fut in list(bb._dispatcher._pending_futures):
        fut.cancel()
    bb._dispatcher._pending_futures.clear()
    bb._dispatcher._worker_loop = None
    bb._dispatcher._worker_thread = None
    bb._dispatcher._worker_ready.clear()


@pytest.fixture(autouse=True)
def mock_worker_loop(monkeypatch):
    """Mock _ensure_worker to return a mock loop for all tests."""
    mock_loop = MagicMock()
    mock_loop.is_running.return_value = True

    def consume_coro(coro, loop):
        if hasattr(coro, "close"):
            coro.close()
        mock_future = MagicMock()
        return mock_future

    # Patch global dispatcher
    monkeypatch.setattr(bb._dispatcher, "_ensure_worker", lambda: mock_loop)
    monkeypatch.setattr(bb._dispatcher, "_worker_loop", mock_loop)

    # Patch EventDispatcher class method to catch any new instances
    def patched_ensure(self):
        return mock_loop

    monkeypatch.setattr(bb.EventDispatcher, "_ensure_worker", patched_ensure)
    # Also patch _worker_loop for new instances
    monkeypatch.setattr(bb.EventDispatcher, "_worker_loop", mock_loop, raising=False)

    # Patch run_coroutine_threadsafe to consume coroutines and avoid "never awaited" warnings
    monkeypatch.setattr(asyncio, "run_coroutine_threadsafe", consume_coro)
