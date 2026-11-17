import asyncio
import math
import random
from reflexd import call, on_event

DEFAULT_PITCH = 12
THINKING_YAW_RANGE = (-45, 45)
THINKING_PITCH_RANGE = (5, 25)
HEAD_MOVE_INTERVAL = 4.0  # total time for one smooth movement (respects 3/sec rate limit)
HEAD_STEPS = 4
LED_CYCLE_INTERVAL = 0.8  # respects 3/sec rate limit for set_leds (1.25/sec + 1/sec = 2.25/sec)

NUM_LEDS = 12

_thinking_task: asyncio.Task | None = None
_led_task: asyncio.Task | None = None
_return_to_neutral_task: asyncio.Task | None = None

_current_yaw = 0.0
_current_pitch = float(DEFAULT_PITCH)


def _hsv_to_rgb(h: float, s: float, v: float) -> tuple[int, int, int]:
    """Convert HSV to RGB (0-255). h in [0, 1), s,v in [0, 1]."""
    h = h * 6.0
    i = int(h)
    f = h - i
    p = v * (1 - s)
    q = v * (1 - s * f)
    t = v * (1 - s * (1 - f))

    if i == 0:
        r, g, b = v, t, p
    elif i == 1:
        r, g, b = q, v, p
    elif i == 2:
        r, g, b = p, v, t
    elif i == 3:
        r, g, b = p, q, v
    elif i == 4:
        r, g, b = t, p, v
    else:
        r, g, b = v, p, q

    return (int(r * 255), int(g * 255), int(b * 255))


def _make_flow_gradient(hue_offset: float) -> list[dict]:
    """Create smooth rainbow gradient flowing across LED strip."""
    pattern = []
    for i in range(NUM_LEDS):
        # Hue varies along strip + global offset for animation
        hue = (hue_offset + i / NUM_LEDS) % 1.0
        r, g, b = _hsv_to_rgb(hue, 1.0, 1.0)  # full saturation, full brightness
        pattern.append({"index": i, "r": r, "g": g, "b": b})
    return pattern


async def _flow_led_loop() -> None:
    """Smooth rainbow gradient flowing across LED strip."""
    try:
        hue_offset = 0.0
        hue_speed = 0.02  # cycles per frame
        while True:
            pattern = _make_flow_gradient(hue_offset)
            await call("set_leds", colors=pattern)
            hue_offset = (hue_offset + hue_speed) % 1.0
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

    # Start smooth flowing rainbow LED cycle
    _led_task = asyncio.create_task(_flow_led_loop())

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