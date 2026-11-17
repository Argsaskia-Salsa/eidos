import asyncio
import math
import random
from reflexd import call, on_event

DEFAULT_PITCH = 12
THINKING_YAW_RANGE = (-45, 45)
THINKING_PITCH_RANGE = (5, 25)
HEAD_MOVE_INTERVAL = 3.0  # total time for one smooth movement (respects 3/sec rate limit)
HEAD_STEPS = 4
LED_CYCLE_INTERVAL = 0.6  # respects 3/sec rate limit for set_leds

# Rainbow colors for LED running effect
RAINBOW_COLORS = [
    (255, 0, 0),    # Red
    (255, 127, 0),  # Orange
    (255, 255, 0),  # Yellow
    (0, 255, 0),    # Green
    (0, 255, 255),  # Cyan
    (0, 0, 255),    # Blue
    (127, 0, 255),  # Purple
    (255, 0, 255),  # Magenta
]

NUM_LEDS = 12

_thinking_task: asyncio.Task | None = None
_led_task: asyncio.Task | None = None
_return_to_neutral_task: asyncio.Task | None = None

_current_yaw = 0.0
_current_pitch = float(DEFAULT_PITCH)


def _make_running_pattern(offset: int) -> list[dict]:
    """Create a running chaser pattern with rainbow colors."""
    pattern = []
    for i in range(NUM_LEDS):
        color_idx = (i + offset) % len(RAINBOW_COLORS)
        r, g, b = RAINBOW_COLORS[color_idx]
        pattern.append({"index": i, "r": r, "g": g, "b": b})
    return pattern


async def _running_led_loop() -> None:
    """Run rainbow colors along the LED strip (chaser effect)."""
    try:
        offset = 0
        while True:
            pattern = _make_running_pattern(offset)
            await call("set_leds", colors=pattern)
            offset += 1
            await asyncio.sleep(LED_CYCLE_INTERVAL)
    except asyncio.CancelledError:
        raise


async def _smooth_thinking_head_loop() -> None:
    """Smooth head movement with incremental changes in yaw/pitch range."""
    global _current_yaw, _current_pitch
    try:
        # Start from current position
        _current_yaw = 0.0
        _current_pitch = (THINKING_PITCH_RANGE[0] + THINKING_PITCH_RANGE[1]) / 2

        while True:
            # Target position within ranges
            target_yaw = random.uniform(*THINKING_YAW_RANGE)
            target_pitch = random.uniform(*THINKING_PITCH_RANGE)

            # Smooth interpolation over several steps
            step_delay = HEAD_MOVE_INTERVAL / HEAD_STEPS
            for i in range(1, HEAD_STEPS + 1):
                t = i / HEAD_STEPS
                # Ease in-out for natural movement
                eased = t * t * (3 - 2 * t)
                interp_yaw = _current_yaw + (target_yaw - _current_yaw) * eased
                interp_pitch = _current_pitch + (target_pitch - _current_pitch) * eased

                await call("move_head", yaw=int(interp_yaw), pitch=int(interp_pitch))
                await asyncio.sleep(step_delay)

            _current_yaw = target_yaw
            _current_pitch = target_pitch

    except asyncio.CancelledError:
        # Smooth return to center - respect rate limit (3/sec = 0.33s min interval)
        steps = 10
        return_delay = 3.5  # total time for return
        step_delay = return_delay / steps
        for i in range(1, steps + 1):
            t = i / steps
            eased = t * t * (3 - 2 * t)
            interp_yaw = _current_yaw * (1 - eased)
            interp_pitch = _current_pitch + (DEFAULT_PITCH - _current_pitch) * eased
            await call("move_head", yaw=int(interp_yaw), pitch=int(interp_pitch))
            await asyncio.sleep(step_delay)
        raise


async def _return_to_neutral_after_delay() -> None:
    """Wait 1 minute then return to neutral state."""
    try:
        await asyncio.sleep(60)
        await call("set_avatar", face="neutral")
        # Smooth return to center - respect rate limit
        steps = 10
        return_delay = 3.5
        step_delay = return_delay / steps
        for i in range(1, steps + 1):
            t = i / steps
            eased = t * t * (3 - 2 * t)
            interp_yaw = 0.0 * (1 - eased)  # from 0 to 0
            interp_pitch = DEFAULT_PITCH + (DEFAULT_PITCH - DEFAULT_PITCH) * eased  # stays at DEFAULT_PITCH
            # Actually just move directly since yaw is already 0
            await call("move_head", yaw=0, pitch=DEFAULT_PITCH)
            await asyncio.sleep(step_delay)
        await call("clear_leds")
    except asyncio.CancelledError:
        raise


@on_event("thinking_start")
async def on_thinking_start(_event):
    global _thinking_task, _led_task, _return_to_neutral_task, _current_yaw, _current_pitch

    # Cancel any existing thinking
    for task in [_thinking_task, _led_task, _return_to_neutral_task]:
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    # RED at start (solid)
    await call("set_all_leds", r=255, g=0, b=0)

    # Thinking face
    await call("set_avatar", face="thinking")

    # Start running rainbow LED cycle
    _led_task = asyncio.create_task(_running_led_loop())

    # Start smooth head movement
    _current_yaw = 0.0
    _current_pitch = (THINKING_PITCH_RANGE[0] + THINKING_PITCH_RANGE[1]) / 2
    _thinking_task = asyncio.create_task(_smooth_thinking_head_loop())


@on_event("thinking_end")
async def on_thinking_end(_event):
    global _thinking_task, _led_task, _return_to_neutral_task

    # Stop thinking loops
    for task in [_thinking_task, _led_task]:
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    # GREEN solid at end with happy face
    await call("set_all_leds", r=0, g=255, b=0)
    await call("set_avatar", face="happy")

    # Schedule return to neutral after 1 minute
    _return_to_neutral_task = asyncio.create_task(_return_to_neutral_after_delay())