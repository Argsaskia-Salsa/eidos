import pytest

from reflexd.registry import (
    clear,
    get_cleanup,
    get_conflict_group,
    get_metadata,
    get_priority,
    on_cleanup,
    on_event,
)


@pytest.fixture(autouse=True)
def reset_registry():
    clear()
    yield
    clear()


class TestRegistryMetadata:
    def test_on_event_with_priority(self):
        @on_event("test_priority", priority=10)
        async def handler(data):
            pass

        assert get_priority("test_priority") == 10
        assert get_metadata("test_priority")["priority"] == 10

    def test_on_event_with_conflict_group(self):
        @on_event("test_group", conflict_group="head_movement")
        async def handler(data):
            pass

        assert get_conflict_group("test_group") == "head_movement"
        assert get_metadata("test_group")["conflict_group"] == "head_movement"

    def test_on_event_with_cleanup(self):
        async def cleanup_fn():
            pass

        @on_event("test_cleanup", cleanup=cleanup_fn)
        async def handler(data):
            pass

        assert get_cleanup("test_cleanup") is cleanup_fn
        assert get_metadata("test_cleanup")["cleanup"] is cleanup_fn

    def test_on_event_with_all_metadata(self):
        async def cleanup_fn():
            pass

        @on_event("test_all", priority=5, cleanup=cleanup_fn, conflict_group="group1")
        async def handler(data):
            pass

        meta = get_metadata("test_all")
        assert meta["priority"] == 5
        assert meta["cleanup"] is cleanup_fn
        assert meta["conflict_group"] == "group1"

    def test_on_event_defaults(self):
        @on_event("test_defaults")
        async def handler(data):
            pass

        assert get_priority("test_defaults") == 0
        assert get_cleanup("test_defaults") is None
        assert get_conflict_group("test_defaults") is None

    def test_on_cleanup_decorator(self):
        @on_event("existing_event")
        async def handler(data):
            pass

        @on_cleanup("existing_event")
        async def cleanup_fn():
            pass

        assert get_cleanup("existing_event") is cleanup_fn

    def test_on_cleanup_creates_metadata_if_missing(self):
        @on_cleanup("new_event")
        async def cleanup_fn():
            pass

        assert get_cleanup("new_event") is cleanup_fn
        assert get_priority("new_event") == 0
        assert get_conflict_group("new_event") is None

    def test_get_metadata_nonexistent(self):
        assert get_metadata("nonexistent") == {}

    def test_get_priority_nonexistent(self):
        assert get_priority("nonexistent") == 0

    def test_get_cleanup_nonexistent(self):
        assert get_cleanup("nonexistent") is None

    def test_get_conflict_group_nonexistent(self):
        assert get_conflict_group("nonexistent") is None

    def test_clear_removes_metadata(self):
        @on_event("test_clear", priority=10, conflict_group="group1")
        async def handler(data):
            pass

        clear()

        assert get_priority("test_clear") == 0
        assert get_conflict_group("test_clear") is None
        assert get_cleanup("test_clear") is None