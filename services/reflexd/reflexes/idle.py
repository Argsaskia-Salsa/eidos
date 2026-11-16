import asyncio
import random

from reflexd import call, on_event


@on_event("idle_tick")
async def on_idle(event):
    try:
        while True:
            yaw = random.randint(-30, 30)
            await call("move_head", yaw=yaw, pitch=60)
            await asyncio.sleep(random.uniform(2, 5))
    except asyncio.CancelledError:
        await call("move_head", yaw=0, pitch=60)
        raise
