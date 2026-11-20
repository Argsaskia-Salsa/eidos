"""Unit tests for PoseRegistry."""


from reflexd.streams.pose import (
    NUM_JOINTS,
    POSE_SPECS,
    POSES,
    PoseRegistry,
    reload_all_poses,
)
from reflexd.streams.pose.config import CONFIG


class TestPoseRegistry:
    """Tests for PoseRegistry (no server needed)."""

    def test_builtin_poses_loaded(self):
        """Test all builtin poses are loaded."""
        assert "wave" in POSES
        assert "nod" in POSES
        assert "track" in POSES
        assert "idle" in POSES
        assert "off" in POSES

    def test_pose_functions_return_frames(self):
        """Test pose functions return valid frames."""
        for name, func in POSES.items():
            frames = func(CONFIG.NUM_JOINTS)
            assert isinstance(frames, list)
            assert len(frames) > 0
            for frame in frames:
                assert isinstance(frame, list)
                assert len(frame) == CONFIG.NUM_JOINTS
                for angle in frame:
                    assert isinstance(angle, float)

    def test_reload_all_poses(self):
        """Test reload_all_poses works."""
        poses_before = list(POSES.keys())
        poses_after = reload_all_poses()
        assert set(poses_before) == set(poses_after)

    def test_registry_specs(self):
        """Test registry specs contain correct metadata."""
        specs = POSE_SPECS
        assert "wave" in specs
        assert specs["wave"].name == "wave"
        assert "amplitude" in specs["wave"].args
        assert "speed" in specs["wave"].args
        assert specs["wave"].description == "Head waves side to side"

        assert "nod" in specs
        assert "amplitude" in specs["nod"].args
        assert "speed" in specs["nod"].args

        assert "track" in specs
        assert "smooth" in specs["track"].args

        assert "idle" in specs
        assert "jitter" in specs["idle"].args

        assert "off" in specs
        assert specs["off"].args == {}

    def test_registry_get(self):
        """Test registry get method."""
        wave_func = PoseRegistry.get("wave")
        assert wave_func is not None
        assert callable(wave_func)

        nonexistent = PoseRegistry.get("nonexistent")
        assert nonexistent is None

    def test_registry_builtin_names(self):
        """Test registry builtin_names method."""
        builtin_names = PoseRegistry.builtin_names()
        assert "wave" in builtin_names
        assert "nod" in builtin_names
        assert "track" in builtin_names
        assert "idle" in builtin_names
        assert "off" in builtin_names

    def test_registry_custom_names(self):
        """Test registry custom_names method."""
        custom_names = PoseRegistry.custom_names()
        assert isinstance(custom_names, list)

    def test_num_joints_config(self):
        """Test NUM_JOINTS matches config."""
        assert NUM_JOINTS == CONFIG.NUM_JOINTS
        assert NUM_JOINTS == 3