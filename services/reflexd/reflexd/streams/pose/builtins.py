"""
Built-in pose patterns.
Pure functions returning list[Frame] where Frame = list[float] (yaw, pitch, roll in degrees).
"""

import math
import random
from collections.abc import Callable

from .config import CONFIG

NUM_JOINTS = CONFIG.NUM_JOINTS
Frame = list[float]  # [yaw, pitch, roll]


def wave(
    num_joints: int = NUM_JOINTS, amplitude: float = 30.0, speed: float = 1.0
) -> list[Frame]:
    """Head waves side to side (yaw)."""
    patterns = []
    frames = int(60 / speed)
    for frame in range(frames):
        yaw = amplitude * math.sin(frame * 2 * math.pi / frames)
        pitch = 0.0
        roll = 0.0
        patterns.append([yaw, pitch, roll])
    return patterns


def nod(
    num_joints: int = NUM_JOINTS, amplitude: float = 20.0, speed: float = 1.5
) -> list[Frame]:
    """Head nods up and down (pitch)."""
    patterns = []
    frames = int(60 / speed)
    for frame in range(frames):
        yaw = 0.0
        pitch = amplitude * math.sin(frame * 2 * math.pi / frames)
        roll = 0.0
        patterns.append([yaw, pitch, roll])
    return patterns


def track(num_joints: int = NUM_JOINTS, smooth: float = 0.3) -> list[Frame]:
    """Smooth tracking - simulates following a moving point."""
    patterns = []
    frames = 120
    target_yaw = 45.0 * math.sin(0)
    target_pitch = 30.0 * math.sin(0)
    current_yaw = 0.0
    current_pitch = 0.0

    for frame in range(frames):
        t = frame / frames
        target_yaw = 45.0 * math.sin(t * 4 * math.pi)
        target_pitch = 30.0 * math.sin(t * 3 * math.pi)

        current_yaw += (target_yaw - current_yaw) * smooth
        current_pitch += (target_pitch - current_pitch) * smooth
        roll = 0.0

        patterns.append([current_yaw, current_pitch, roll])
    return patterns


def idle(num_joints: int = NUM_JOINTS, jitter: float = 2.0) -> list[Frame]:
    """Random micro-movements for natural idle behavior."""
    patterns = []
    frames = 120
    for frame in range(frames):
        yaw = random.uniform(-jitter, jitter)
        pitch = random.uniform(-jitter, jitter)
        roll = random.uniform(-jitter * 0.5, jitter * 0.5)
        patterns.append([yaw, pitch, roll])
    return patterns


def off(num_joints: int = NUM_JOINTS) -> list[Frame]:
    """All joints at zero (neutral position)."""
    return [[0.0, 0.0, 0.0]]


def thinking(
    num_joints: int = NUM_JOINTS,
    yaw: float = 30.0,
    pitch: float = 35.0,
    hold_frames: int = 60,
) -> list[Frame]:
    """Static thinking pose - head tilted up and slightly turned. hold_frames keeps pose active."""
    return [[yaw, pitch, 0.0]] * hold_frames


def thinking_idle(
    num_joints: int = NUM_JOINTS,
    base_yaw: float = 30.0,
    base_pitch: float = 35.0,
    jitter_yaw: float = 15.0,
    jitter_pitch: float = 10.0,
    frames: int = 30,
) -> list[Frame]:
    """Micro-movements around thinking pose to prevent idle timeout."""
    patterns = []
    for _ in range(frames):
        yaw = base_yaw + random.uniform(-jitter_yaw, jitter_yaw)
        pitch = base_pitch + random.uniform(-jitter_pitch, jitter_pitch)
        patterns.append([yaw, pitch, 0.0])
    return patterns


# Pattern specifications for registry
from ..base.registry import SpecProtocol


class PoseSpec:
    """Pose specification with metadata for CLI generation."""

    def __init__(
        self, name: str, func: Callable, args: dict | None = None, description: str = ""
    ):
        self.name = name
        self.func = func
        self.args = args or {}
        self.description = description


# Built-in pose specs
BUILTIN_POSES: dict[str, SpecProtocol] = {
    "wave": PoseSpec(
        "wave", wave, {"amplitude": 30.0, "speed": 1.0}, "Head waves side to side"
    ),
    "nod": PoseSpec(
        "nod", nod, {"amplitude": 20.0, "speed": 1.5}, "Head nods up and down"
    ),
    "track": PoseSpec(
        "track", track, {"smooth": 0.3}, "Smooth tracking of moving point"
    ),
    "idle": PoseSpec("idle", idle, {"jitter": 2.0}, "Random micro-movements"),
    "off": PoseSpec("off", off, {}, "All joints at neutral position"),
    "thinking": PoseSpec(
        "thinking",
        thinking,
        {"yaw": 30.0, "pitch": 35.0, "hold_frames": 60},
        "Static thinking pose",
    ),
    "thinking_idle": PoseSpec(
        "thinking_idle",
        thinking_idle,
        {
            "base_yaw": 30.0,
            "base_pitch": 35.0,
            "jitter_yaw": 15.0,
            "jitter_pitch": 10.0,
            "frames": 30,
        },
        "Micro-movements during thinking",
    ),
}
