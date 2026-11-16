from .gateway_client import get_gateway_client
from .registry import on_event


async def call(tool: str, args: dict) -> dict:
    client = get_gateway_client()
    if not client:
        raise RuntimeError("Gateway client not connected")
    return await client.call(tool, args)


__all__ = ["call", "on_event"]
