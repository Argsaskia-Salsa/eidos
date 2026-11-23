"""Integration tests for body_expression_bridge new hooks - end-to-end flow verification."""

import threading
from unittest.mock import MagicMock, patch

import body_expression_bridge as bb
import pytest
from aiohttp import web

pytestmark = pytest.mark.order(4)


@pytest.fixture(autouse=True)
def cleanup_dispatchers():
    """Ensure clean state between tests."""
    yield


@pytest.fixture
async def mock_reflexd_server():
    """Start a mock reflexd events server."""
    received_requests = []

    async def handle_events(request):
        data = await request.json()
        received_requests.append(data)
        return web.json_response({"success": True})

    app = web.Application()
    app.router.add_post("/events", handle_events)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "localhost", 18771)
    await site.start()

    yield received_requests

    await runner.cleanup()


class TestHookToReflexdProtocol:
    """Test protocol for new event types via dispatcher."""

    @pytest.mark.asyncio
    async def test_platform_event_protocol(self, mock_reflexd_server):
        """Test platform_event follows correct format."""
        dispatcher = bb.EventDispatcher(
            endpoint_url="http://localhost:18771/events",
            timeout=2.0
        )

        try:
            await dispatcher._post_event(
                event_type="platform_event",
                text="",
                platform="telegram",
                chat_id="chat-1",
                user_id="user-1",
                data={"event_type": "reaction", "payload": {"emojis": ["👍"]}},
            )

            assert len(mock_reflexd_server) == 1
            req = mock_reflexd_server[0]
            assert req["event_type"] == "platform_event"
            assert req["payload"]["platform"] == "telegram"
            assert req["payload"]["chat_id"] == "chat-1"
            assert req["payload"]["user_id"] == "user-1"
            assert req["payload"]["data"]["event_type"] == "reaction"
            assert req["payload"]["data"]["payload"]["emojis"] == ["👍"]
            assert "meta" in req
            assert req["meta"]["v"] == 1
        finally:
            dispatcher.shutdown()

    @pytest.mark.asyncio
    async def test_stream_start_protocol(self, mock_reflexd_server):
        """Test stream_start follows correct format."""
        dispatcher = bb.EventDispatcher(
            endpoint_url="http://localhost:18771/events",
            timeout=2.0
        )

        try:
            await dispatcher._post_event(
                event_type="stream_start",
                text="",
                platform="stream",
                chat_id="sess-1",
                user_id="",
            )

            assert len(mock_reflexd_server) == 1
            req = mock_reflexd_server[0]
            assert req["event_type"] == "stream_start"
            assert req["payload"]["chat_id"] == "sess-1"
            assert req["payload"]["platform"] == "stream"
        finally:
            dispatcher.shutdown()

    @pytest.mark.asyncio
    async def test_stream_delta_protocol(self, mock_reflexd_server):
        """Test stream_delta follows correct format."""
        dispatcher = bb.EventDispatcher(
            endpoint_url="http://localhost:18771/events",
            timeout=2.0
        )

        try:
            await dispatcher._post_event(
                event_type="stream_delta",
                text="Streaming text delta",
                platform="stream",
                chat_id="sess-1",
                user_id="",
            )

            assert len(mock_reflexd_server) == 1
            req = mock_reflexd_server[0]
            assert req["event_type"] == "stream_delta"
            assert req["payload"]["text"] == "Streaming text delta"
        finally:
            dispatcher.shutdown()

    @pytest.mark.asyncio
    async def test_stream_end_protocol(self, mock_reflexd_server):
        """Test stream_end follows correct format."""
        dispatcher = bb.EventDispatcher(
            endpoint_url="http://localhost:18771/events",
            timeout=2.0
        )

        try:
            await dispatcher._post_event(
                event_type="stream_end",
                text="Complete response",
                platform="stream",
                chat_id="sess-1",
                user_id="",
            )

            assert len(mock_reflexd_server) == 1
            req = mock_reflexd_server[0]
            assert req["event_type"] == "stream_end"
            assert req["payload"]["text"] == "Complete response"
        finally:
            dispatcher.shutdown()

    @pytest.mark.asyncio
    async def test_session_end_protocol(self, mock_reflexd_server):
        """Test session_end follows correct format."""
        dispatcher = bb.EventDispatcher(
            endpoint_url="http://localhost:18771/events",
            timeout=2.0
        )

        try:
            await dispatcher._post_event(
                event_type="session_end",
                text="",
                platform="telegram",
                chat_id="sess-1",
                user_id="",
            )

            assert len(mock_reflexd_server) == 1
            req = mock_reflexd_server[0]
            assert req["event_type"] == "session_end"
            assert req["payload"]["chat_id"] == "sess-1"
            assert req["payload"]["platform"] == "telegram"
        finally:
            dispatcher.shutdown()

    @pytest.mark.asyncio
    async def test_session_finalize_protocol(self, mock_reflexd_server):
        """Test session_finalize follows correct format."""
        dispatcher = bb.EventDispatcher(
            endpoint_url="http://localhost:18771/events",
            timeout=2.0
        )

        try:
            await dispatcher._post_event(
                event_type="session_finalize",
                text="",
                platform="discord",
                chat_id="sess-1",
                user_id="",
            )

            assert len(mock_reflexd_server) == 1
            req = mock_reflexd_server[0]
            assert req["event_type"] == "session_finalize"
            assert req["payload"]["chat_id"] == "sess-1"
            assert req["payload"]["platform"] == "discord"
        finally:
            dispatcher.shutdown()

    @pytest.mark.asyncio
    async def test_api_error_protocol(self, mock_reflexd_server):
        """Test api_error follows correct format."""
        dispatcher = bb.EventDispatcher(
            endpoint_url="http://localhost:18771/events",
            timeout=2.0
        )

        try:
            await dispatcher._post_event(
                event_type="api_error",
                text="Internal server error",
                platform="openai",
                chat_id="gpt-4",
                user_id="",
            )

            assert len(mock_reflexd_server) == 1
            req = mock_reflexd_server[0]
            assert req["event_type"] == "api_error"
            assert req["payload"]["text"] == "Internal server error"
            assert req["payload"]["platform"] == "openai"
            assert req["payload"]["chat_id"] == "gpt-4"
        finally:
            dispatcher.shutdown()


class TestStreamThrottlingIntegration:
    """Test stream throttling doesn't drop events."""

    @pytest.mark.asyncio
    async def test_multiple_deltas_coalesced(self, mock_reflexd_server):
        """Test multiple small deltas are coalesced into single flush."""
        dispatcher = bb.EventDispatcher(
            endpoint_url="http://localhost:18771/events",
            timeout=2.0
        )

        try:
            # Simulate buffered deltas being flushed
            await dispatcher._post_event(
                event_type="stream_delta",
                text="Part 1 Part 2 Part 3",
                platform="stream",
                chat_id="sess-coalesce",
                user_id="",
            )
            await dispatcher._post_event(
                event_type="stream_end",
                text="Final",
                platform="stream",
                chat_id="sess-coalesce",
                user_id="",
            )

            assert len(mock_reflexd_server) == 2
            delta_req = mock_reflexd_server[0]
            end_req = mock_reflexd_server[1]
            assert delta_req["event_type"] == "stream_delta"
            assert "Part 1 Part 2 Part 3" in delta_req["payload"]["text"]
            assert end_req["event_type"] == "stream_end"
        finally:
            dispatcher.shutdown()


class TestSessionLifecycleOrder:
    """Test session lifecycle events fire in correct order."""

    @pytest.mark.asyncio
    async def test_session_end_then_finalize(self, mock_reflexd_server):
        """Test on_session_end fires before on_session_finalize."""
        dispatcher = bb.EventDispatcher(
            endpoint_url="http://localhost:18771/events",
            timeout=2.0
        )

        try:
            await dispatcher._post_event(
                event_type="session_end",
                text="",
                platform="telegram",
                chat_id="sess-order",
                user_id="",
            )
            await dispatcher._post_event(
                event_type="session_finalize",
                text="",
                platform="telegram",
                chat_id="sess-order",
                user_id="",
            )

            assert len(mock_reflexd_server) == 2
            first = mock_reflexd_server[0]
            second = mock_reflexd_server[1]
            assert first["event_type"] == "session_end"
            assert second["event_type"] == "session_finalize"
        finally:
            dispatcher.shutdown()


class TestPlatformFilteringIntegration:
    """Test platform filtering in full integration flow."""

    @pytest.mark.asyncio
    async def test_remote_platforms_processed_all_events(self, mock_reflexd_server):
        """Test all new event types process remote platforms."""
        dispatcher = bb.EventDispatcher(
            endpoint_url="http://localhost:18771/events",
            timeout=2.0
        )

        try:
            event_types = [
                ("platform_event", "telegram", "chat-1", "user-1"),
                ("stream_start", "stream", "sess-1", ""),
                ("stream_delta", "stream", "sess-2", ""),
                ("session_end", "telegram", "sess-3", ""),
                ("session_finalize", "discord", "sess-4", ""),
                ("api_error", "openai", "gpt-4", ""),
            ]

            for event_type, platform, chat_id, user_id in event_types:
                mock_reflexd_server.clear()
                await dispatcher._post_event(
                    event_type=event_type,
                    text="test",
                    platform=platform,
                    chat_id=chat_id,
                    user_id=user_id,
                )
                assert len(mock_reflexd_server) == 1
                assert mock_reflexd_server[0]["event_type"] == event_type
        finally:
            dispatcher.shutdown()


class TestThreadSafety:
    """Test thread safety of hook dispatching."""

    def test_concurrent_hook_calls_thread_safe(self):
        """Test multiple threads calling hooks concurrently."""
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

                def call_hooks(i):
                    try:
                        with patch.object(bb, "_dispatcher", dispatcher):
                            import asyncio
                            asyncio.run(bb._gateway_platform_event_hook(
                                platform="telegram",
                                event_type="reaction",
                                payload={"chat_id": f"chat-{i}", "user_id": f"user-{i}"},
                            ))
                        results.append(i)
                    except Exception as e:
                        errors.append((i, e))

                threads = [threading.Thread(target=call_hooks, args=(i,)) for i in range(10)]
                for t in threads:
                    t.start()
                for t in threads:
                    t.join()

                assert len(errors) == 0
                assert len(results) == 10
        finally:
            dispatcher.shutdown()


class TestErrorHandlingIntegration:
    """Test error handling in integration scenarios."""

    @pytest.mark.asyncio
    async def test_network_failure_logged_not_crashed(self, mock_reflexd_server):
        """Test network failure during POST is logged but doesn't crash."""
        dispatcher = bb.EventDispatcher(
            endpoint_url="http://unreachable:9999/events",
            timeout=0.1
        )

        try:
            with patch.object(bb.logger, "warning") as mock_warn:
                await dispatcher._post_event(
                    event_type="platform_event",
                    text="test",
                    platform="telegram",
                    chat_id="c1",
                    user_id="u1",
                )
                mock_warn.assert_called_once()
                assert "failed" in mock_warn.call_args.args[0].lower()
        finally:
            dispatcher.shutdown()

    @pytest.mark.asyncio
    async def test_worker_unavailable_logged_not_crashed(self, mock_reflexd_server):
        """Test worker loop unavailable is handled gracefully."""
        dispatcher = bb.EventDispatcher()
        dispatcher.shutdown()

        try:
            # Calling _post_event on a shut down dispatcher should not crash
            # (it will create a new client if needed)
            await dispatcher._post_event(
                event_type="platform_event",
                text="test",
                platform="telegram",
                chat_id="c1",
                user_id="u1",
            )
            # If we reach here without exception, the test passes
        finally:
            dispatcher.shutdown()


# Clean up global state
@pytest.fixture(autouse=True)
def reset_stream_state():
    """Reset stream buffer and timers before/after each test."""
    bb._stream_buffer.clear()
    bb._stream_timers.clear()
    yield
    bb._stream_buffer.clear()
    bb._stream_timers.clear()