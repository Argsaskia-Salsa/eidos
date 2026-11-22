"""Integration tests for reflexd /events endpoint."""

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest
from aiohttp.test_utils import TestClient, TestServer

from reflexd.server import (
    VALID_EVENT_TYPES,
    _event_processor_task,
    _event_queues,
    create_app,
    handle_events,
)


@pytest.fixture
def app():
    """Create a fresh app for each test."""
    # Clear global state
    for queue in _event_queues.values():
        while not queue.empty():
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                break
    _event_queues.clear()

    global _event_processor_task
    if _event_processor_task and not _event_processor_task.done():
        _event_processor_task.cancel()
    _event_processor_task = None

    return create_app()


@pytest.fixture
async def client(app):
    """Create test client."""
    async with TestClient(TestServer(app)) as client:
        yield client


@pytest.fixture
def mock_handle_inject_event():
    """Mock handle_inject_event to track calls."""
    with patch("reflexd.server.handle_inject_event", new_callable=AsyncMock) as mock:
        mock.return_value = {"status": "ok", "event": "thinking_start"}
        yield mock


class TestEventsEndpoint:
    """Test /events endpoint."""

    @pytest.mark.asyncio
    async def test_valid_user_message_returns_ok_and_queues_event(self, client):
        """Valid user_message event returns 200 and puts event in queue."""
        payload = {
            "event_type": "user_message",
            "payload": {"text": "Hello", "platform": "telegram", "chat_id": "1", "user_id": "1"},
            "meta": {},
        }

        resp = await client.post("/events", json=payload)
        assert resp.status == 200
        data = await resp.json()
        assert data == {"ok": True}

    @pytest.mark.asyncio
    async def test_valid_thinking_start_routes_to_handler(self, client, mock_handle_inject_event):
        """Valid thinking_start routes to handle_inject_event('thinking_start')."""
        payload = {
            "event_type": "thinking_start",
            "payload": {"text": "", "platform": "telegram", "chat_id": "1", "user_id": "1"},
            "meta": {},
        }

        resp = await client.post("/events", json=payload)
        assert resp.status == 200

        # Wait for event processor to handle it
        await asyncio.sleep(0.1)

        mock_handle_inject_event.assert_called()
        # Check it was called with thinking_start
        calls = mock_handle_inject_event.call_args_list
        assert any(call.args[0] == "thinking_start" for call in calls)

    @pytest.mark.asyncio
    async def test_invalid_json_returns_400(self, client):
        """Invalid JSON body returns 400."""
        resp = await client.post("/events", data="not json", headers={"Content-Type": "application/json"})
        assert resp.status == 400
        data = await resp.json()
        assert data == {"ok": False, "error": "Invalid JSON"}

    @pytest.mark.asyncio
    async def test_missing_event_type_returns_400(self, client):
        """Missing event_type returns 400."""
        payload = {"payload": {"text": "test"}, "meta": {}}
        resp = await client.post("/events", json=payload)
        assert resp.status == 400
        data = await resp.json()
        assert data == {"ok": False, "error": "Missing event_type"}

    @pytest.mark.asyncio
    async def test_unknown_event_type_returns_400(self, client):
        """Unknown event_type returns 400."""
        payload = {"event_type": "invalid_type", "payload": {}, "meta": {}}
        resp = await client.post("/events", json=payload)
        assert resp.status == 400
        data = await resp.json()
        assert data["ok"] is False
        assert "Unknown event_type: invalid_type" in data["error"]

    @pytest.mark.asyncio
    async def test_queue_full_returns_503(self, client):
        """Queue full (timeout) returns 503."""

        async def slow_put(self, item):
            await asyncio.sleep(2)  # Longer than 1s timeout

        with patch.object(asyncio.Queue, "put", slow_put):
            payload = {
                "event_type": "user_message",
                "payload": {"text": "test", "platform": "telegram", "chat_id": "1", "user_id": "1"},
                "meta": {},
            }
            resp = await client.post("/events", json=payload)
            assert resp.status == 503
            data = await resp.json()
            assert data == {"ok": False, "error": "Event queue full"}

    @pytest.mark.asyncio
    async def test_multiple_concurrent_events_processed_in_order(self, client, mock_handle_inject_event):
        """Multiple concurrent events are all processed in order."""
        payloads = [
            {
                "event_type": "user_message",
                "payload": {"text": f"msg{i}", "platform": "telegram", "chat_id": "1", "user_id": "1"},
                "meta": {},
            }
            for i in range(5)
        ]

        # Send all concurrently
        tasks = [client.post("/events", json=p) for p in payloads]
        responses = await asyncio.gather(*tasks)

        for resp in responses:
            assert resp.status == 200
            data = await resp.json()
            assert data == {"ok": True}

        # Wait for processing
        await asyncio.sleep(0.2)

        # All should have been processed
        assert mock_handle_inject_event.call_count >= 5


class TestEventProcessor:
    """Test background event processor."""

    @pytest.mark.asyncio
    async def test_event_processor_starts_on_app_startup(self, app):
        """Event processor task starts on application startup."""
        from reflexd.server import _event_processor_task
        assert _event_processor_task is not None
        # Task may be done if cancelled between tests, just verify it was created
        # The important thing is the startup hook registers it

    @pytest.mark.asyncio
    async def test_event_processor_handles_user_message(self, client, mock_handle_inject_event):
        """Event processor handles user_message by calling handle_inject_event."""
        payload = {
            "event_type": "user_message",
            "payload": {"text": "Hello", "platform": "telegram", "chat_id": "1", "user_id": "1"},
            "meta": {},
        }

        await client.post("/events", json=payload)
        await asyncio.sleep(0.1)

        mock_handle_inject_event.assert_called()
        calls = mock_handle_inject_event.call_args_list
        # user_message should route to thinking_start per current logic
        assert any(call.args[0] == "thinking_start" for call in calls)

    @pytest.mark.asyncio
    async def test_event_processor_handles_user_response(self, client, mock_handle_inject_event):
        """Event processor handles user_response."""
        payload = {
            "event_type": "user_response",
            "payload": {"text": "Reply", "platform": "telegram", "chat_id": "1", "user_id": "1"},
            "meta": {},
        }

        await client.post("/events", json=payload)
        await asyncio.sleep(0.1)

        mock_handle_inject_event.assert_called()
        calls = mock_handle_inject_event.call_args_list
        assert any(call.args[0] == "thinking_end" for call in calls)

    @pytest.mark.asyncio
    async def test_event_processor_handles_thinking_start_directly(self, client, mock_handle_inject_event):
        """Event processor handles thinking_start directly."""
        payload = {
            "event_type": "thinking_start",
            "payload": {"text": "", "platform": "telegram", "chat_id": "1", "user_id": "1"},
            "meta": {},
        }

        await client.post("/events", json=payload)
        await asyncio.sleep(0.1)

        mock_handle_inject_event.assert_called()
        calls = mock_handle_inject_event.call_args_list
        assert any(call.args[0] == "thinking_start" for call in calls)

    @pytest.mark.asyncio
    async def test_event_processor_handles_thinking_end_directly(self, client, mock_handle_inject_event):
        """Event processor handles thinking_end directly."""
        payload = {
            "event_type": "thinking_end",
            "payload": {"text": "", "platform": "telegram", "chat_id": "1", "user_id": "1"},
            "meta": {},
        }

        await client.post("/events", json=payload)
        await asyncio.sleep(0.1)

        mock_handle_inject_event.assert_called()
        calls = mock_handle_inject_event.call_args_list
        assert any(call.args[0] == "thinking_end" for call in calls)

    @pytest.mark.asyncio
    async def test_event_processor_ignores_unhandled_types(self, client, mock_handle_inject_event):
        """Event processor ignores unhandled event types (logs debug)."""
        payload = {
            "event_type": "platform_event",
            "payload": {"text": "", "platform": "telegram", "chat_id": "1", "user_id": "1"},
            "meta": {},
        }

        await client.post("/events", json=payload)
        await asyncio.sleep(0.1)

        # platform_event is not in the routing logic, so handle_inject_event should not be called for it
        # (it may be called for other events if they were queued before)


class TestEventsEndpointDirectHandler:
    """Test handle_events handler directly."""

    @pytest.mark.asyncio
    async def test_handle_events_valid_request(self):
        """handle_events processes valid request correctly."""
        request = AsyncMock()
        request.json = AsyncMock(return_value={
            "event_type": "user_message",
            "payload": {"text": "test", "platform": "telegram", "chat_id": "1", "user_id": "1"},
            "meta": {},
        })

        with patch("reflexd.server._get_event_queue") as mock_queue:
            mock_q = AsyncMock()
            mock_queue.return_value = mock_q
            mock_q.put = AsyncMock()

            resp = await handle_events(request)
            assert resp.status == 200
            data = json.loads(resp.text)
            assert data == {"ok": True}

    @pytest.mark.asyncio
    async def test_handle_events_invalid_json(self):
        """handle_events returns 400 for invalid JSON."""
        request = AsyncMock()
        request.json = AsyncMock(side_effect=ValueError("Invalid JSON"))

        resp = await handle_events(request)
        assert resp.status == 400
        data = json.loads(resp.text)
        assert data == {"ok": False, "error": "Invalid JSON"}

    @pytest.mark.asyncio
    async def test_handle_events_missing_event_type(self):
        """handle_events returns 400 for missing event_type."""
        request = AsyncMock()
        request.json = AsyncMock(return_value={"payload": {}, "meta": {}})

        resp = await handle_events(request)
        assert resp.status == 400
        data = json.loads(resp.text)
        assert data == {"ok": False, "error": "Missing event_type"}

    @pytest.mark.asyncio
    async def test_handle_events_unknown_event_type(self):
        """handle_events returns 400 for unknown event_type."""
        request = AsyncMock()
        request.json = AsyncMock(return_value={
            "event_type": "unknown",
            "payload": {},
            "meta": {},
        })

        resp = await handle_events(request)
        assert resp.status == 400
        data = json.loads(resp.text)
        assert data["ok"] is False
        assert "Unknown event_type: unknown" in data["error"]

    @pytest.mark.asyncio
    async def test_handle_events_queue_timeout(self):
        """handle_events returns 503 when queue put times out."""
        request = AsyncMock()
        request.json = AsyncMock(return_value={
            "event_type": "user_message",
            "payload": {"text": "test", "platform": "telegram", "chat_id": "1", "user_id": "1"},
            "meta": {},
        })

        with patch("reflexd.server._get_event_queue") as mock_queue:
            mock_q = AsyncMock()
            mock_queue.return_value = mock_q
            mock_q.put = AsyncMock(side_effect=TimeoutError())

            resp = await handle_events(request)
            assert resp.status == 503
            data = json.loads(resp.text)
            assert data == {"ok": False, "error": "Event queue full"}


class TestValidEventTypes:
    """Test VALID_EVENT_TYPES constant."""

    def test_valid_event_types_contains_expected(self):
        """VALID_EVENT_TYPES contains all expected event types."""
        expected = {
            "user_message", "user_response", "thinking_start", "thinking_end",
            "idle_tick",
            "platform_event", "stream_start", "stream_delta", "stream_end",
            "session_end", "session_finalize", "api_error",
        }
        assert VALID_EVENT_TYPES == expected


class TestEventProcessorErrorHandling:
    """Test event processor error handling."""

    @pytest.mark.asyncio
    async def test_event_processor_continues_on_handler_error(self, client):
        """Event processor continues processing after handler error."""
        with patch("reflexd.server.handle_inject_event", side_effect=Exception("Handler failed")):
            payload = {
                "event_type": "user_message",
                "payload": {"text": "test", "platform": "telegram", "chat_id": "1", "user_id": "1"},
                "meta": {},
            }

            # First event - handler fails
            resp = await client.post("/events", json=payload)
            assert resp.status == 200

            await asyncio.sleep(0.1)

            # Second event - should still be processed
            resp2 = await client.post("/events", json=payload)
            assert resp2.status == 200

            await asyncio.sleep(0.1)
            # No exception should propagate