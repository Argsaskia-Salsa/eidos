def clamp_move_head(yaw: int, pitch: int, speed: int = 100) -> tuple[int, int, int]:
    yaw = max(-90, min(90, int(yaw)))
    pitch = max(5, min(85, int(pitch)))
    speed = max(10, min(200, int(speed)))
    return yaw, pitch, speed
