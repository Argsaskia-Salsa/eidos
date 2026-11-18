import asyncio
import random
from reflexd import call, on_cleanup, on_event, trigger_event

DEFAULT_PITCH = 12
THINKING_YAW_RANGE = (-45, 45)
THINKING_PITCH_RANGE = (5, 25)
HEAD_MOVE_INTERVAL = 0.5
LED_CYCLE_INTERVAL = 0.2

RAINBOW_COLORS = [
    (255, 0, 0),
    (255, 127, 0),
    (255, 255, 0),
    (0, 255, 0),
    (0, 255, 255),
    (0, 0, 255),
    (127, 0, 255),
    (255, 0, 255),
]

_thinking_task: asyncio.Task | None = None
_led_task: asyncio.Task | None = None
_return_to_neutral_task: asyncio.Task | None = None

_current_yaw = 0.0
_current_pitch = float(DEFAULT_PITCH)


async def _rainbow_led_loop() -> None:
    idx = 0
    while True:
        color = RAINBOW_COLORS[idx % len(RAINBOW_COLORS)]
        await call("set_all_leds", r=color[0], g=color[1], b=color[2])
        idx += 1
        await asyncio.sleep(LED_CYCLE_INTERVAL)


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

    await call("set_all_leds", r=255, g=0, b=0)
    await call("set_avatar", face="thinking")

    _led_task = asyncio.create_task(_rainbow_led_loop())

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