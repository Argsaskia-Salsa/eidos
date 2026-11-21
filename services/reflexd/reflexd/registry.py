import logging
from collections.abc import Callable

logger = logging.getLogger(__name__)

_handlers: dict[str, Callable] = {}
_metadata: dict[str, dict] = {}


def on_event(
    name: str,
    *,
    priority: int = 0,
    cleanup: Callable | None = None,
    conflict_group: str | None = None,
):
    def deco(fn: Callable):
        if name in _handlers:
            logger.warning("Handler for event '%s' being overwritten", name)
        _handlers[name] = fn
        _metadata[name] = {
            "priority": priority,
            "cleanup": cleanup,
            "conflict_group": conflict_group,
        }
        return fn

    return deco


def on_cleanup(event: str):
    """Register a cleanup handler for when event is interrupted."""

    def deco(fn: Callable):
        meta = _metadata.setdefault(event, {})
        meta["cleanup"] = fn
        return fn

    return deco


def get_handler(name: str) -> Callable | None:
    return _handlers.get(name)


def get_metadata(name: str) -> dict:
    return _metadata.get(name, {})


def get_priority(name: str) -> int:
    return _metadata.get(name, {}).get("priority", 0)


def get_cleanup(name: str) -> Callable | None:
    return _metadata.get(name, {}).get("cleanup")


def get_conflict_group(name: str) -> str | None:
    return _metadata.get(name, {}).get("conflict_group")


def list_handlers() -> list[str]:
    return list(_handlers.keys())


def clear() -> None:
    _handlers.clear()
    _metadata.clear()
