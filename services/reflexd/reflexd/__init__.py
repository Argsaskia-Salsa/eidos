from .event_bus import publish as trigger_event
from .event_bus import subscribe, unsubscribe
from .gateway_client import get_gateway_client
from .registry import on_cleanup, on_event

__all__ = [
    "call",
    "on_cleanup",
    "on_event",
    "subscribe",
    "trigger_event",
    "unsubscribe",
]


async def call(tool: str, args: dict | None = None, **kwargs) -> dict:
    client = get_gateway_client()
    if not client:
        raise RuntimeError("Gateway client not connected")
    if args is None:
        args = {}
    if kwargs:
        args = {**args, **kwargs}
    return await client.call(tool, args)
