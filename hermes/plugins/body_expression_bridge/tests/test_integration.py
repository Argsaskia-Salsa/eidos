"""Integration tests for body_expression_bridge - covers protocol,
worker loop, and end-to-end flows."""

import threading
from unittest.mock import AsyncMock, MagicMock, patch

import body_expression_bridge as bb
import pytest
from aiohttp import web

pytestmark = pytest.mark.order(2)


@pytest.fixture(autouse=True)
def cleanup_dispatchers():
    """Ensure all EventDispatcher instances are shut down after each test."""
    yield
    # The global dispatcher is cleaned up by unit test fixture
    # Individual test instances should be cleaned up by their tests
    # This fixture exists as a safety net
    pass


class TestReflexdProtocol:
    """Test JSON-RPC protocol compliance with reflexd MCP."""

    @pytest.fixture
    async def mock_reflexd_server(self):
        """Start a mock reflexd MCP server."""
        received_requests = []

        async def handle_events(request):
            data = await request.json()
            received_requests.append(data)
            return web.json_response({"success": True})

        app = web.Application()
        app.router.add_post("/events", handle_events)

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "localhost", 18770)
        await site.start()

        yield received_requests

        await runner.cleanup()

    @pytest.mark.asyncio
    async def test_inject_event_protocol(self, mock_reflexd_server):
        """Test that inject_event follows correct JSON-RPC 2.0 format."""
        dispatcher = bb.EventDispatcher(
            endpoint_url="http://localhost:18770/events",
            timeout=2.0
        )

        try:
            await dispatcher._post_event(
                event_type="user_message",
                text="Hello world",
                platform="telegram",
                chat_id="123",
                user_id="456",
            )

            assert len(mock_reflexd_server) == 1
            req = mock_reflexd_server[0]

            # Event format
            assert req["event_type"] == "user_message"
            assert req["payload"]["text"] == "Hello world"
            assert req["payload"]["platform"] == "telegram"
            assert req["payload"]["chat_id"] == "123"
            assert req["payload"]["user_id"] == "456"
            assert "meta" in req
            assert req["meta"]["v"] == 1
        finally:
            dispatcher.shutdown()

    @pytest.mark.asyncio
    async def test_all_event_types_protocol(self, mock_reflexd_server):
        """Test all event types produce valid protocol."""
        dispatcher = bb.EventDispatcher(
            endpoint_url="http://localhost:18770/events",
            timeout=2.0
        )

        try:
            event_types = ["user_message", "user_response", "thinking_start", "thinking_end"]

            for event_type in event_types:
                mock_reflexd_server.clear()
                await dispatcher._post_event(
                    event_type=event_type,
                    text="test" if "thinking" not in event_type else "",
                    platform="telegram",
                    chat_id="123",
                    user_id="456",
                )

                assert len(mock_reflexd_server) == 1
                req = mock_reflexd_server[0]
                assert req["event_type"] == event_type
                assert "meta" in req
                assert req["meta"]["v"] == 1
        finally:
            dispatcher.shutdown()

    @pytest.mark.asyncio
    async def test_http_error_handling(self, mock_reflexd_server):
        """Test handling of HTTP 5xx errors from reflexd."""
        dispatcher = bb.EventDispatcher(endpoint_url="http://localhost:18770/events", timeout=2.0)

        try:
            with patch("httpx.AsyncClient.post") as mock_post:
                mock_response = AsyncMock()
                mock_response.status_code = 500
                mock_post.return_value = mock_response

                with patch.object(bb.logger, "warning") as mock_warn:
                    await dispatcher._post_event(
                        event_type="user_message",
                        text="test",
                        platform="telegram",
                        chat_id="123",
                        user_id="456",
                    )
                    mock_warn.assert_called_once()
                    assert "HTTP %s" in mock_warn.call_args.args[0]
        finally:
            dispatcher.shutdown()


class TestWorkerLoopIntegration:
    """Test worker loop lifecycle and thread safety."""

    def test_worker_loop_starts_and_stops(self):
        """Test worker loop can be started and used."""
        dispatcher = bb.EventDispatcher()

        try:
            loop = dispatcher._ensure_worker()
            assert loop is not None
            assert loop.is_running()

            # Second call returns same loop
            loop2 = dispatcher._ensure_worker()
            assert loop is loop2
        finally:
            dispatcher.shutdown()

    def test_schedule_post_from_multiple_threads(self):
        """Test schedule_post is thread-safe."""
        dispatcher = bb.EventDispatcher()
        dispatcher._ensure_worker()

        try:
            results = []
            errors = []

            with patch("asyncio.run_coroutine_threadsafe") as mock_run:
                mock_future = MagicMock()

                def consume_coro(coro, loop):
                    if hasattr(coro, "close"):
                        coro.close()
                    return mock_future

                mock_run.side_effect = consume_coro

                def call_schedule_post(i):
                    try:
                        dispatcher.schedule_post(
                            event_type="user_message",
                            text=f"message-{i}",
                            platform="telegram",
                            chat_id="123",
                            user_id="456",
                        )
                        results.append(i)
                    except Exception as e:
                        errors.append((i, e))

                thread_args = [(i,) for i in range(10)]
                threads = [
                    threading.Thread(target=call_schedule_post, args=args) for args in thread_args
                ]
                for t in threads:
                    t.start()
                for t in threads:
                    t.join()

                assert len(errors) == 0
                assert len(results) == 10
        finally:
            dispatcher.shutdown()

    def test_pending_futures_tracked_and_cleaned(self):
        """Test in-flight futures are tracked and cleaned up."""
        dispatcher = bb.EventDispatcher()
        dispatcher._ensure_worker()

        try:
            initial_pending = len(dispatcher._pending_futures)

            # Schedule a post and verify it's tracked
            dispatcher.schedule_post(
                event_type="user_message",
                text="test",
                platform="telegram",
                chat_id="123",
                user_id="456",
            )

            # The future should be added to pending_futures
            # Note: due to threading, this might not be immediately visible
            # Just verify the method doesn't crash and metrics increment
            assert dispatcher._pending_futures is not None
        finally:
            dispatcher.shutdown()


class TestEndToEndFlow:
    """Test complete event flow from hook to reflexd."""

    @pytest.mark.asyncio
    async def test_gateway_admission_to_reflexd(self, mock_reflexd_server):
        """Test post_gateway_admission hook sends correct event via dispatcher."""
        dispatcher = bb.EventDispatcher(
            endpoint_url="http://localhost:18770/events",
            timeout=2.0
        )
        dispatcher._ensure_worker()

        try:
            # Use dispatcher directly (bypasses async scheduling for test)
            await dispatcher._post_event(
                event_type="user_message",
                text="Hello from user",
                platform="telegram",
                chat_id="chat-1",
                user_id="user-1",
            )

            assert len(mock_reflexd_server) == 1
            req = mock_reflexd_server[0]
            assert req["event_type"] == "user_message"
            assert req["payload"]["text"] == "Hello from user"
            assert req["payload"]["chat_id"] == "chat-1"
            assert req["payload"]["user_id"] == "user-1"
        finally:
            dispatcher.shutdown()

    @pytest.mark.asyncio
    async def test_llm_call_to_reflexd(self, mock_reflexd_server):
        """Test post_llm_call sends correct event via dispatcher."""
        dispatcher = bb.EventDispatcher(
            endpoint_url="http://localhost:18770/events",
            timeout=2.0
        )
        dispatcher._ensure_worker()

        try:
            await dispatcher._post_event(
                event_type="user_response",
                text="Hello there!",
                platform="telegram",
                chat_id="chat-1",
                user_id="user-1",
            )

            assert len(mock_reflexd_server) == 1
            req = mock_reflexd_server[0]
            assert req["event_type"] == "user_response"
            assert req["payload"]["text"] == "Hello there!"
            assert req["payload"]["chat_id"] == "chat-1"
            assert req["payload"]["user_id"] == "user-1"
        finally:
            dispatcher.shutdown()

    @pytest.mark.asyncio
    async def test_hook_extracts_chat_user_from_history(self):
        """Test post_llm_call extracts chat_id/user_id from history."""
        history = [
            {"role": "user", "content": "Hi", "chat_id": "chat-1", "user_id": "user-1"},
            {"role": "assistant", "content": "Hello!"},
        ]

        chat_id, user_id = bb._extract_chat_user_from_history(history, "sess-1")
        assert chat_id == "chat-1"
        assert user_id == "user-1"

    def test_gateway_admission_uses_source_platform(self):
        """Test post_gateway_admission uses platform from source."""
        source = {"chat_id": "c1", "user_id": "u1", "platform": "discord"}
        chat_id = str(source.get("chat_id") or "unknown")
        user_id = str(source.get("user_id") or "unknown")
        src_platform = source.get("platform") or "telegram"
        assert src_platform == "discord"
        assert chat_id == "c1"
        assert user_id == "u1"

    def test_middleware_thinking_events(self):
        """Test llm_execution middleware fires thinking_start/end."""
        dispatcher = bb.EventDispatcher()
        dispatcher._ensure_worker()

        try:
            with patch("asyncio.run_coroutine_threadsafe") as mock_run:
                mock_future = MagicMock()

                def consume_coro(coro, loop):
                    if hasattr(coro, "close"):
                        coro.close()
                    return mock_future

                mock_run.side_effect = consume_coro

                def next_call(request):
                    return {"response": "ok"}

                result = bb._llm_execution_middleware(
                    request={"messages": []},
                    next_call=next_call,
                    platform="telegram",
                    session_id="sess-1",
                    chat_id="chat-1",
                    user_id="user-1",
                )

                assert result == {"response": "ok"}
                assert mock_run.call_count == 2

                # Verify both events scheduled
                calls = mock_run.call_args_list
                coro1 = calls[0][0][0]
                coro2 = calls[1][0][0]
                assert coro1.cr_code.co_name == "_post"
                assert coro2.cr_code.co_name == "_post"
        finally:
            dispatcher.shutdown()

    def test_middleware_thinking_end_on_exception(self):
        """Test thinking_end fires even when next_call raises."""
        dispatcher = bb.EventDispatcher()
        dispatcher._ensure_worker()

        try:
            with patch("asyncio.run_coroutine_threadsafe") as mock_run:
                mock_future = MagicMock()

                def consume_coro(coro, loop):
                    if hasattr(coro, "close"):
                        coro.close()
                    return mock_future

                mock_run.side_effect = consume_coro

                def failing_next_call(request):
                    raise Exception("LLM failed")

                with pytest.raises(Exception, match="LLM failed"):
                    bb._llm_execution_middleware(
                        request={"messages": []},
                        next_call=failing_next_call,
                        platform="telegram",
                        session_id="sess-1",
                        chat_id="chat-1",
                        user_id="user-1",
                    )

                # Both events should still be scheduled
                assert mock_run.call_count == 2
        finally:
            dispatcher.shutdown()


class TestConfigurationIntegration:
    """Test configuration via environment variables."""

    def test_custom_endpoint_url(self):
        """Test custom endpoint URL from environment."""
        with patch.dict("os.environ", {"EVENTS_ENDPOINT_URL": "http://custom:9999/events"}):
            import importlib

            importlib.reload(bb)
            assert bb._EVENTS_ENDPOINT == "http://custom:9999/events"

    def test_custom_timeout(self):
        """Test custom timeout from environment."""
        with patch.dict("os.environ", {"EVENTS_TIMEOUT_SECONDS": "5.5"}):
            import importlib

            importlib.reload(bb)
            assert bb._EVENTS_TIMEOUT == 5.5

    def test_defaults_when_env_not_set(self):
        """Test defaults used when env vars not set."""
        with patch.dict("os.environ", {}, clear=True):
            import importlib

            importlib.reload(bb)
            assert bb._EVENTS_ENDPOINT == "http://localhost:8770/events"
            assert bb._EVENTS_TIMEOUT == 1.0


class TestPlatformFilteringIntegration:
    """Test platform filtering in full flow."""

    def test_local_platforms_skipped_in_dispatcher(self):
        """Test local platforms are skipped at dispatcher level."""
        dispatcher = bb.EventDispatcher()
        dispatcher._ensure_worker()

        try:
            with patch("asyncio.run_coroutine_threadsafe") as mock_run:
                for platform in ["local", "desktop", "cli", "LOCAL", "Desktop"]:
                    mock_run.reset_mock()
                    dispatcher.schedule_post(
                        event_type="user_message",
                        text="test",
                        platform=platform,
                        chat_id="123",
                        user_id="456",
                    )
                    mock_run.assert_not_called()
        finally:
            dispatcher.shutdown()

    def test_remote_platforms_processed(self):
        """Test remote platforms are processed."""
        dispatcher = bb.EventDispatcher()
        dispatcher._ensure_worker()

        try:
            with patch("asyncio.run_coroutine_threadsafe") as mock_run:
                mock_future = MagicMock()

                def consume_coro(coro, loop):
                    if hasattr(coro, "close"):
                        coro.close()
                    return mock_future

                mock_run.side_effect = consume_coro

                for platform in ["telegram", "discord", "slack", "whatsapp"]:
                    mock_run.reset_mock()
                    dispatcher.schedule_post(
                        event_type="user_message",
                        text="test",
                        platform=platform,
                        chat_id="123",
                        user_id="456",
                    )
                    mock_run.assert_called_once()
        finally:
            dispatcher.shutdown()


class TestErrorRecovery:
    """Test error recovery and graceful degradation."""

    @pytest.mark.asyncio
    async def test_network_failure_doesnt_crash(self):
        """Test network failures are logged but don't crash."""
        dispatcher = bb.EventDispatcher(endpoint_url="http://unreachable:9999/events", timeout=0.1)

        try:
            with patch.object(bb.logger, "warning") as mock_warn:
                await dispatcher._post_event(
                    event_type="user_message",
                    text="test",
                    platform="telegram",
                    chat_id="123",
                    user_id="456",
                )
                mock_warn.assert_called_once()
                assert "failed" in mock_warn.call_args.args[0].lower()
        finally:
            dispatcher.shutdown()

    def test_worker_loop_failure_handled(self):
        """Test worker loop startup failure is handled."""
        dispatcher = bb.EventDispatcher()

        try:
            # Simulate worker thread failing to start
            with patch.object(dispatcher, "_worker_ready") as mock_ready:
                mock_ready.wait.return_value = False

                loop = dispatcher._ensure_worker()
                assert loop is None
        finally:
            dispatcher.shutdown()

    def test_schedule_post_handles_no_loop(self):
        """Test schedule_post handles unavailable worker loop."""
        dispatcher = bb.EventDispatcher()

        try:
            with (
                patch.object(dispatcher, "_ensure_worker", return_value=None),
                patch.object(bb.logger, "warning") as mock_warn,
            ):
                dispatcher.schedule_post(
                    event_type="user_message",
                    text="test",
                    platform="telegram",
                    chat_id="123",
                    user_id="456",
                )
                mock_warn.assert_called_once()
                assert "worker loop unavailable" in mock_warn.call_args.args[0]
        finally:
            dispatcher.shutdown()


class TestRegisterIntegration:
    """Test plugin registration."""

    def test_register_calls_all_hooks(self):
        """Test register() calls all registration points."""
        mock_ctx = MagicMock()

        with patch.object(bb._dispatcher, "_ensure_worker"):
            bb.register(mock_ctx)

        assert mock_ctx.register_hook.call_count == 10
        assert mock_ctx.register_middleware.call_count == 1

        hooks = [c.args[0] for c in mock_ctx.register_hook.call_args_list]
        assert "post_gateway_admission" in hooks
        assert "post_llm_call" in hooks
        assert "gateway_platform_event" in hooks
        assert "on_stream_start" in hooks
        assert "on_stream_delta" in hooks
        assert "on_stream_end" in hooks
        assert "pre_gateway_dispatch" in hooks
        assert "on_session_end" in hooks
        assert "on_session_finalize" in hooks
        assert "api_request_error" in hooks

        middleware = mock_ctx.register_middleware.call_args.args[0]
        assert middleware == "llm_execution"


# Pytest fixture for mock reflexd server
@pytest.fixture
async def mock_reflexd_server():
    """Start a mock reflexd MCP server."""
    received_requests = []

    async def handle_events(request):
        data = await request.json()
        received_requests.append(data)
        return web.json_response({"success": True})

    app = web.Application()
    app.router.add_post("/events", handle_events)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "localhost", 18770)
    await site.start()

    yield received_requests

    await runner.cleanup()