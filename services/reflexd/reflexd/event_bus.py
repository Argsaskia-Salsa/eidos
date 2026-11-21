import asyncio
import logging
from collections.abc import Callable

logger = logging.getLogger(__name__)

_subscribers: dict[str, list[Callable]] = {}


async def publish(event: str, data: dict) -> None:
    """Publish an event to all subscribers."""
    for cb in _subscribers.get(event, []):
        try:
            asyncio.create_task(cb(data))
        except Exception:
            logger.exception("Error publishing event %s to subscriber", event)


def subscribe(event: str, callback: Callable) -> None:
    """Subscribe a callback to an event."""
    _subscribers.setdefault(event, []).append(callback)


def unsubscribe(event: str, callback: Callable) -> None:
    """Unsubscribe a callback from an event."""
    if event in _subscribers:
        try:
            _subscribers[event].remove(callback)
        except ValueError:
            pass
