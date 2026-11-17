import logging
from collections.abc import Callable

logger = logging.getLogger(__name__)

_handlers: dict[str, Callable] = {}


def on_event(name: str):
    def deco(fn: Callable):
        if name in _handlers:
            logger.warning("Handler for event '%s' being overwritten", name)
        _handlers[name] = fn
        return fn

    return deco


def get_handler(name: str) -> Callable | None:
    return _handlers.get(name)


def list_handlers() -> list[str]:
    return list(_handlers.keys())


def clear() -> None:
    _handlers.clear()
