"""
Pose Registry - extends StreamRegistry for pose patterns.
"""

from collections.abc import Callable
from pathlib import Path

from ..base.registry import SpecProtocol, StreamRegistry
from .builtins import BUILTIN_POSES
from .config import CONFIG


class PoseRegistry(StreamRegistry):
    """Registry for pose patterns with builtins and hot-reloadable customs."""

    custom_dir = Path(__file__).parent / "patterns" / "custom"
    module_prefix = "reflexd.streams.pose.patterns.custom"
    spec_attr = "POSE"

    # Type hints for pose-specific usage
    ELEMENTS: dict[str, Callable[..., list[list[float]]]] = {}
    ELEMENT_SPECS: dict[str, SpecProtocol] = {}

    # Register built-in poses
    _BUILTIN_SPECS: dict[str, SpecProtocol] = BUILTIN_POSES


# Initialize registry on import
PoseRegistry.reload_all()

# Public exports for backward compatibility
POSES = PoseRegistry.ELEMENTS
POSE_SPECS = PoseRegistry.ELEMENT_SPECS
reload_all_poses = PoseRegistry.reload_all
NUM_JOINTS = CONFIG.NUM_JOINTS
