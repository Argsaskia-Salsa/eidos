import asyncio
import random

from reflexd import call, on_event

DEFAULT_PITCH = 12


@on_event("idle_tick")
async def on_idle(_event):
    try:
        while True:
            yaw = random.randint(-30, 30)
            await call("move_head", yaw=yaw, pitch=DEFAULT_PITCH)
            await asyncio.sleep(random.uniform(2, 5))
    except asyncio.CancelledError:
        await call("move_head", yaw=0, pitch=DEFAULT_PITCH)
        raise
