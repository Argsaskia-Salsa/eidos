import warnings
from typing import Callable, Any

_handlers: dict[str, Callable] = {}

def on_event(name: str):
    def deco(fn: Callable):
        if name in _handlers:
            warnings.warn(f"Overwriting handler for event: {name}", stacklevel=2)
        _handlers[name] = fn
        return fn
    return deco

def get_handler(name: str) -> Callable | None:
    return _handlers.get(name)

def list_handlers() -> list[str]:
    return list(_handlers.keys())

def clear() -> None:
    _handlers.clear()