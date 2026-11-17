from .gateway_client import get_gateway_client
from .registry import on_event


async def call(tool: str, args: dict | None = None, **kwargs) -> dict:
    client = get_gateway_client()
    if not client:
        raise RuntimeError("Gateway client not connected")
    # Support both call(tool, args_dict) and call(tool, key=value, ...)
    if args is None:
        args = {}
    if kwargs:
        args = {**args, **kwargs}
    return await client.call(tool, args)


__all__ = ["call", "on_event"]
