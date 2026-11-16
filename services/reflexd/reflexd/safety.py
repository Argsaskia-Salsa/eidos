import time


def clamp_move_head(yaw: int, pitch: int, speed: int = 100) -> tuple[int, int, int]:
    yaw = max(-90, min(90, int(yaw)))
    pitch = max(5, min(85, int(pitch)))
    speed = max(10, min(200, int(speed)))
    return yaw, pitch, speed


_head_times: list[float] = []


def check_rate_limit() -> bool:
    now = time.time()
    _head_times[:] = [t for t in _head_times if now - t < 1.0]
    if len(_head_times) >= 3:
        return False
    _head_times.append(now)
    return True
