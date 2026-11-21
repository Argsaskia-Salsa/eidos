import logging

import pytest

from reflexd.registry import clear, get_handler, list_handlers, on_event


@pytest.fixture(autouse=True)
def reset_registry():
    clear()
    yield
    clear()


def test_on_event_registers_handler():
    @on_event("test_event")
    async def handler(data):
        return data

    assert get_handler("test_event") is not None
    assert "test_event" in list_handlers()


def test_on_event_overwrite_warns(caplog):
    @on_event("test_event")
    async def handler1(data):
        return data

    with caplog.at_level(logging.WARNING):

        @on_event("test_event")
        async def handler2(data):
            return data

        assert len(caplog.records) == 1
        assert "overwrit" in caplog.records[0].message.lower()
        assert caplog.records[0].name == "reflexd.registry"


def test_get_handler_returns_none_for_missing():
    assert get_handler("nonexistent") is None


def test_list_handlers_returns_all_keys():
    @on_event("event1")
    async def handler1(data):
        pass

    @on_event("event2")
    async def handler2(data):
        pass

    handlers = list_handlers()
    assert "event1" in handlers
    assert "event2" in handlers
    assert len(handlers) == 2


def test_clear_removes_all_handlers():
    @on_event("event1")
    async def handler1(data):
        pass

    @on_event("event2")
    async def handler2(data):
        pass

    clear()
    assert list_handlers() == []
    assert get_handler("event1") is None
    assert get_handler("event2") is None
