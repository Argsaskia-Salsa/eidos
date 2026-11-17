import asyncio
import random
from reflexd import call, on_event

DEFAULT_PITCH = 12
THINKING_YAW_RANGE = (-45, 45)
LED_CYCLE_INTERVAL = 0.3
HEAD_MOVE_INTERVAL = 1.5

# Rainbow colors for LED strip
RAINBOW_COLORS = [
    (255, 0, 0),    # Red
    (255, 127, 0),  # Orange
    (255, 255, 0),  # Yellow
    (0, 255, 0),    # Green
    (0, 0, 255),    # Blue
    (75, 0, 130),   # Indigo
    (148, 0, 211),  # Violet
]

_thinking_task: asyncio.Task | None = None
_led_task: asyncio.Task | None = None


async def _rainbow_led_loop() -> None:
    """Cycle through rainbow colors on all LEDs."""
    try:
        idx = 0
        while True:
            color = RAINBOW_COLORS[idx % len(RAINBOW_COLORS)]
            await call("set_all_leds", r=color[0], g=color[1], b=color[2])
            idx += 1
            await asyncio.sleep(LED_CYCLE_INTERVAL)
    except asyncio.CancelledError:
        await call("clear_leds")
        raise


async def _thinking_head_loop() -> None:
    """Slow wide head rotations while thinking."""
    try:
        while True:
            yaw = random.randint(*THINKING_YAW_RANGE)
            await call("move_head", yaw=yaw, pitch=DEFAULT_PITCH)
            await asyncio.sleep(HEAD_MOVE_INTERVAL)
    except asyncio.CancelledError:
        await call("move_head", yaw=0, pitch=DEFAULT_PITCH)
        raise


@on_event("thinking_start")
async def on_thinking_start(_event):
    global _thinking_task, _led_task

    # Cancel any existing thinking
    if _thinking_task and not _thinking_task.done():
        _thinking_task.cancel()
        try:
            await _thinking_task
        except asyncio.CancelledError:
            pass
    if _led_task and not _led_task.done():
        _led_task.cancel()
        try:
            await _led_task
        except asyncio.CancelledError:
            pass

    # Set thinking face
    await call("set_avatar", face="thinking")

    # Start rainbow LED cycle
    _led_task = asyncio.create_task(_rainbow_led_loop())

    # Start slow wide head rotations
    _thinking_task = asyncio.create_task(_thinking_head_loop())


@on_event("thinking_end")
async def on_thinking_end(_event):
    global _thinking_task, _led_task

    # Stop LED cycle
    if _led_task and not _led_task.done():
        _led_task.cancel()
        try:
            await _led_task
        except asyncio.CancelledError:
            pass

    # Stop head movement
    if _thinking_task and not _thinking_task.done():
        _thinking_task.cancel()
        try:
            await _thinking_task
        except asyncio.CancelledError:
            pass

    # Return to neutral
    await call("set_avatar", face="neutral")
    await call("move_head", yaw=0, pitch=DEFAULT_PITCH)
    await call("clear_leds")

    _thinking_task = None
    _led_task = None