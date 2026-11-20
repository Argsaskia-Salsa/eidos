"""
Custom pose: Shake
Head shakes side to side rapidly.
"""

import math

from reflexd.streams.pose.builtins import PoseSpec


def shake(num_joints: int = 3, amplitude: float = 30.0, speed: float = 3.0) -> list[list[float]]:
    """Rapid side-to-side shake."""
    patterns = []
    frames = int(60 / speed)
    for frame in range(frames):
        yaw = amplitude * math.sin(frame * 2 * math.pi / frames)
        pitch = 0.0
        roll = 0.0
        patterns.append([yaw, pitch, roll])
    return patterns


# Export pattern specification
POSE = PoseSpec(
    name="shake",
    func=shake,
    args={"amplitude": 30.0, "speed": 3.0},
    description="Rapid side-to-side head shake"
)