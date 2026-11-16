from typing import Callable, Any

_handlers: dict[str, Callable] = {}

def on_event(name: str):
    def deco(fn: Callable):
        _handlers[name] = fn
        return fn
    return deco

def get_handler(name: str) -> Callable | None:
    return _handlers.get(name)

def list_handlers() -> list[str]:
    return list(_handlers.keys())

def clear() -> None:
    _handlers.clear()