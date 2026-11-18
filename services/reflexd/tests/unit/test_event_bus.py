import asyncio

import pytest

from reflexd.event_bus import _subscribers, publish, subscribe, unsubscribe


@pytest.fixture(autouse=True)
def clear_subscribers():
    _subscribers.clear()
    yield
    _subscribers.clear()


class TestEventBus:
    @pytest.mark.asyncio
    async def test_publish_to_single_subscriber(self):
        received = []

        async def handler(data):
            received.append(data)

        subscribe("test_event", handler)
        await publish("test_event", {"key": "value"})

        await asyncio.sleep(0.01)
        assert received == [{"key": "value"}]

    @pytest.mark.asyncio
    async def test_publish_to_multiple_subscribers(self):
        received1 = []
        received2 = []

        async def handler1(data):
            received1.append(data)

        async def handler2(data):
            received2.append(data)

        subscribe("multi_event", handler1)
        subscribe("multi_event", handler2)
        await publish("multi_event", {"data": 123})

        await asyncio.sleep(0.01)
        assert received1 == [{"data": 123}]
        assert received2 == [{"data": 123}]

    @pytest.mark.asyncio
    async def test_publish_to_no_subscribers(self):
        await publish("nonexistent", {"data": "test"})

    @pytest.mark.asyncio
    async def test_unsubscribe(self):
        received = []

        async def handler(data):
            received.append(data)

        subscribe("unsub_event", handler)
        unsubscribe("unsub_event", handler)
        await publish("unsub_event", {"data": "test"})

        await asyncio.sleep(0.01)
        assert received == []

    @pytest.mark.asyncio
    async def test_unsubscribe_nonexistent(self):
        async def handler(data):
            pass

        unsubscribe("nonexistent", handler)

    @pytest.mark.asyncio
    async def test_multiple_events_independent(self):
        received_a = []
        received_b = []

        async def handler_a(data):
            received_a.append(data)

        async def handler_b(data):
            received_b.append(data)

        subscribe("event_a", handler_a)
        subscribe("event_b", handler_b)

        await publish("event_a", {"event": "a"})
        await publish("event_b", {"event": "b"})

        await asyncio.sleep(0.01)
        assert received_a == [{"event": "a"}]
        assert received_b == [{"event": "b"}]

    @pytest.mark.asyncio
    async def test_subscriber_exception_isolated(self):
        received = []

        async def bad_handler(data):
            raise ValueError("handler error")

        async def good_handler(data):
            received.append(data)

        subscribe("error_event", bad_handler)
        subscribe("error_event", good_handler)
        await publish("error_event", {"data": "test"})

        await asyncio.sleep(0.01)
        assert received == [{"data": "test"}]