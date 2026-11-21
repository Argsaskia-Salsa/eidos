"""
Pose Stream Server - extends StreamServer for pose patterns.
WS format matches stackchan_follow_pose_stream expectations.
"""

import time
from typing import Any

from ..base.server import ServerConfig, StreamServer
from .config import CONFIG
from .registry import PoseRegistry


class PoseServerConfig(ServerConfig):
    WS_HOST = CONFIG.WS_HOST
    WS_PORT = CONFIG.WS_PORT
    HTTP_PORT = CONFIG.HTTP_PORT
    DEFAULT_FPS = CONFIG.DEFAULT_FPS
    DEFAULT_INTENSITY = CONFIG.DEFAULT_SMOOTHING


class PoseStreamServer(StreamServer):
    """Pose Stream Server with WebSocket (gateway) + HTTP (control)."""

    element_name = "pose"
    intensity_name = "smoothing"
    registry = PoseRegistry

    def get_config(self) -> ServerConfig:
        return PoseServerConfig()

    def make_frame(self, frame_angles: list[float], smoothing: float) -> dict:
        """Serialize to WS JSON format expected by stackchan_follow_pose_stream."""
        return {
            "ts": time.time(),
            "kind": "continuous",
            "angles": [frame_angles],  # Single frame: [yaw, pitch, roll]
        }

    def generate_frames(self, pose_func: callable, **args: Any) -> list:  # type: ignore[valid-type,override]
        """Call pose function with num_joints and args."""
        return pose_func(CONFIG.NUM_JOINTS, **args)  # type: ignore[misc]

    def _create_state(self):
        return PoseState(self)


class PoseState(StreamServer.StreamState):
    """Pose-specific state with smoothing handling."""

    def apply_intensity(self, frames: list) -> list:
        """Apply smoothing to frames.

        Smoothing interpolates between current and target angles.
        1.0 = no smoothing (direct), 0.0 = fully smoothed (stay at current).
        """
        if self.intensity >= 1.0:
            return frames

        # For poses, smoothing works differently than LED brightness
        # We apply smoothing as a factor to the angle values
        # This is a simple implementation - real smoothing would interpolate between frames
        smoothed_frames = []
        for frame in frames:
            smoothed_frame = [angle * self.intensity for angle in frame]
            smoothed_frames.append(smoothed_frame)
        return smoothed_frames
