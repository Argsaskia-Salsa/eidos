"""
Pose Stream Package - unified API for pose streaming.
"""

from .cli import main
from .client import PoseStreamClient
from .registry import NUM_JOINTS, POSE_SPECS, POSES, PoseRegistry, reload_all_poses
from .server import PoseStreamServer

__all__ = [
    "NUM_JOINTS",
    "POSES",
    "POSE_SPECS",
    "PoseRegistry",
    "PoseStreamClient",
    "PoseStreamServer",
    "main",  # CLI entrypoint
    "reload_all_poses",
]
