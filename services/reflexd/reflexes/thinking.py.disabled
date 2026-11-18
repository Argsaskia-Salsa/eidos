import asyncio
import random
from reflexd import call, on_cleanup, on_event, trigger_event

DEFAULT_PITCH = 12
THINKING_YAW_RANGE = (-45, 45)
THINKING_PITCH_RANGE = (5, 25)
HEAD_MOVE_INTERVAL = 4.0
LED_CYCLE_INTERVAL = 2.0

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


async def _flow_led_loop() -> None:
    """Each LED has its own color, flowing rainbow wave along the strip."""
    try:
        hue_offset = 0.0
        hue_speed = 0.02  # cycles per frame
        while True:
            # Create pattern: each LED gets its own hue, shifted by position + global offset
            pattern = []
            for i in range(NUM_LEDS):
                # Each LED gets its own hue based on position + global animation offset
                hue = (hue_offset + i / NUM_LEDS) % 1.0
                r, g, b = _hsv_to_rgb(hue, 1.0, 1.0)
                pattern.append({"index": i, "r": r, "g": g, "b": b})
            
            await call("set_leds", colors=pattern)
            hue_offset = (hue_offset + hue_speed) % 1.0
            await asyncio.sleep(LED_CYCLE_INTERVAL)
    except asyncio.CancelledError:
        raise


async def _smooth_thinking_head_loop() -> None:
    global _current_yaw, _current_pitch
    _current_yaw = 0.0
    _current_pitch = (THINKING_PITCH_RANGE[0] + THINKING_PITCH_RANGE[1]) / 2

    try:
        while True:
            target_yaw = random.uniform(*THINKING_YAW_RANGE)
            target_pitch = random.uniform(*THINKING_PITCH_RANGE)

            steps = 5
            for i in range(1, steps + 1):
                t = i / steps
                eased = t * t * (3 - 2 * t)
                interp_yaw = _current_yaw + (target_yaw - _current_yaw) * eased
                interp_pitch = _current_pitch + (target_pitch - _current_pitch) * eased

                await call("move_head", yaw=int(interp_yaw), pitch=int(interp_pitch))
                await asyncio.sleep(HEAD_MOVE_INTERVAL / steps)

            _current_yaw = target_yaw
            _current_pitch = target_pitch

    except asyncio.CancelledError:
        steps = 10
        return_delay = 3.5
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
    await asyncio.sleep(60)
    await call("set_avatar", face="neutral")
    await call("move_head", yaw=0, pitch=DEFAULT_PITCH)
    await call("clear_leds")


@on_event("thinking_start", priority=10, conflict_group="head_movement")
async def on_thinking_start(_event):
    global _thinking_task, _led_task, _current_yaw, _current_pitch

    for task in [_thinking_task, _led_task, _return_to_neutral_task]:
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    # RED at start
    await call("set_all_leds", r=255, g=0, b=0)
    await call("set_avatar", face="thinking")

    _led_task = asyncio.create_task(_flow_led_loop())

    _current_yaw = 0.0
    _current_pitch = (THINKING_PITCH_RANGE[0] + THINKING_PITCH_RANGE[1]) / 2
    _thinking_task = asyncio.create_task(_smooth_thinking_head_loop())


@on_cleanup("thinking_start")
async def cleanup_thinking():
    for task in [_thinking_task, _led_task, _return_to_neutral_task]:
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
    await call("clear_leds")
    await call("set_avatar", face="neutral")
    await call("move_head", yaw=0, pitch=DEFAULT_PITCH)
    await trigger_event("led_rainbow_stop", {})


@on_event("thinking_end", priority=10, conflict_group="head_movement")
async def on_thinking_end(_event):
    global _return_to_neutral_task

    for task in [_thinking_task, _led_task]:
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    await call("set_all_leds", r=0, g=255, b=0)
    await call("set_avatar", face="happy")

    _return_to_neutral_task = asyncio.create_task(_return_to_neutral_after_delay())


@on_cleanup("thinking_end")
async def cleanup_thinking_end():
    if _return_to_neutral_task and not _return_to_neutral_task.done():
        _return_to_neutral_task.cancel()
        try:
            await _return_to_neutral_task
        except asyncio.CancelledError:
            pass