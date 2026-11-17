import asyncio
import os
import time


def clamp_move_head(yaw: int, pitch: int, speed: int = 100) -> tuple[int, int, int]:
    yaw = max(-90, min(90, int(yaw)))
    pitch = max(5, min(85, int(pitch)))
    speed = max(10, min(200, int(speed)))
    return yaw, pitch, speed


def _get_rate_limit_config() -> tuple[int, float]:
    try:
        limit = int(os.getenv("MOVE_HEAD_RATE_LIMIT", "3"))
    except ValueError:
        limit = 3
    try:
        window = float(os.getenv("MOVE_HEAD_RATE_WINDOW", "1.0"))
    except ValueError:
        window = 1.0
    return limit, window


_head_times: list[float] = []
_head_times_lock: asyncio.Lock | None = None


def _get_head_times_lock() -> asyncio.Lock:
    global _head_times_lock
    if _head_times_lock is None:
        _head_times_lock = asyncio.Lock()
    return _head_times_lock


async def check_rate_limit() -> bool:
    limit, window = _get_rate_limit_config()
    lock = _get_head_times_lock()
    async with lock:
        now = time.time()
        _head_times[:] = [t for t in _head_times if now - t < window]
        if len(_head_times) >= limit:
            return False
        _head_times.append(now)
        return True
