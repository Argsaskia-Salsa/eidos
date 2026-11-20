"""Unit tests for PoseStreamServer state machine."""

import pytest
from reflexd.streams.pose.config import CONFIG
from reflexd.streams.pose.server import PoseStreamServer


class TestPoseStreamServerState:
    """Tests for PoseStreamServer state management."""

    def test_server_state_default_element(self):
        """Test default element is first available."""
        server = PoseStreamServer()
        assert server.state.element_name in ["wave", "nod", "track", "idle", "off"]

    def test_server_state_set_element(self):
        """Test setting element and args."""
        server = PoseStreamServer()
        server.state.set_element("wave", amplitude=45.0, speed=2.0)
        assert server.state.element_name == "wave"
        assert server.state.element_args == {"amplitude": 45.0, "speed": 2.0}

    def test_server_state_set_invalid_element(self):
        """Test setting invalid element raises ValueError."""
        server = PoseStreamServer()
        with pytest.raises(ValueError, match="Unknown pose: nonexistent"):
            server.state.set_element("nonexistent")

    def test_server_state_set_fps(self):
        """Test setting FPS clamps to valid range."""
        server = PoseStreamServer()
        server.state.set_fps(100)
        assert server.state.fps == 60

        server.state.set_fps(0)
        assert server.state.fps == 1

        server.state.set_fps(30)
        assert server.state.fps == 30

    def test_server_state_set_smoothing(self):
        """Test setting smoothing clamps to valid range."""
        server = PoseStreamServer()
        server.state.set_intensity(1.5)
        assert server.state.intensity == 1.0

        server.state.set_intensity(-0.5)
        assert server.state.intensity == 0.0

        server.state.set_intensity(0.5)
        assert server.state.intensity == 0.5

    def test_server_state_caching(self):
        """Test frame caching works correctly."""
        server = PoseStreamServer()
        
        # Get frames first time - should generate
        frames1 = server.state.get_element()
        assert len(frames1) > 0
        assert server.state._cache_key is not None

        # Get frames second time - should use cache
        frames2 = server.state.get_element()
        assert frames1 is frames2  # Same object from cache

        # Change args - should invalidate cache
        server.state.set_element("wave", amplitude=45.0)
        frames3 = server.state.get_element()
        assert frames3 is not frames1  # New object

    def test_server_state_status(self):
        """Test status returns correct structure."""
        server = PoseStreamServer()
        status = server.state.status()
        
        assert "pose" in status
        assert "pose_args" in status
        assert "fps" in status
        assert "smoothing" in status
        assert "frames_in_element" in status
        assert "frame_index" in status
        assert "connected_clients" in status
        assert "uptime_seconds" in status

    def test_pose_state_apply_intensity(self):
        """Test smoothing application on frames."""
        server = PoseStreamServer()
        
        # Test frames with various angles
        frames = [[30.0, 20.0, 10.0], [15.0, 10.0, 5.0]]
        
        # At 1.0 smoothing, no change
        server.state.intensity = 1.0
        result = server.state.apply_intensity(frames)
        assert result == frames
        
        # At 0.5 smoothing, angles halved
        server.state.intensity = 0.5
        result = server.state.apply_intensity(frames)
        expected = [[15.0, 10.0, 5.0], [7.5, 5.0, 2.5]]
        assert result == expected

    def test_pose_state_default_element_fallback(self):
        """Test fallback to default element when element not found."""
        server = PoseStreamServer()
        
        # Set to valid element
        server.state.set_element("wave")
        frames = server.state.get_element()
        assert len(frames) > 0
        
        # Manually set to invalid element name in state (bypassing validation)
        server.state.element_name = "invalid"
        server.state._cache_key = None
        
        # Should fallback to default element
        frames = server.state.get_element()
        assert len(frames) > 0


class TestPoseStreamServerConfig:
    """Tests for PoseServerConfig."""

    def test_config_values(self):
        """Test config has correct values."""
        server = PoseStreamServer()
        config = server.get_config()
        
        assert config.WS_HOST == CONFIG.WS_HOST
        assert config.WS_PORT == CONFIG.WS_PORT
        assert config.HTTP_PORT == CONFIG.HTTP_PORT
        assert config.DEFAULT_FPS == CONFIG.DEFAULT_FPS
        assert config.DEFAULT_INTENSITY == CONFIG.DEFAULT_SMOOTHING