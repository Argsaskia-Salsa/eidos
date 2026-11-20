"""
Integration tests for Pose Stream - full cycle: server + WS + HTTP + MCP + hot-reload.
"""

import asyncio
import json

import aiohttp
import pytest
import websockets
from reflexd.streams.pose import (
    POSES,
    PoseRegistry,
    PoseStreamClient,
    PoseStreamServer,
    reload_all_poses,
)
from reflexd.streams.pose.config import CONFIG


async def create_test_server():
    """Create and start Pose stream server for testing."""
    server = PoseStreamServer()
    # Use test ports
    server.config.WS_PORT = 18774
    server.config.HTTP_PORT = 18775
    server.config.WS_HOST = "127.0.0.1"
    
    # Start server in background
    task = asyncio.create_task(server.start())
    
    # Wait for server to start
    await asyncio.sleep(0.5)
    
    return server, task


async def stop_test_server(server, task):
    """Stop test server and cleanup."""
    await server.stop()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


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


class TestPoseStreamIntegration:
    """Integration tests for Pose stream full cycle with running server."""
    
    @pytest.mark.asyncio
    async def test_server_status_endpoint(self):
        """Test /status endpoint returns correct state."""
        server, task = await create_test_server()
        try:
            async with aiohttp.ClientSession() as session:
                url = f"http://127.0.0.1:{server.config.HTTP_PORT}/status"
                async with session.get(url) as resp:
                    assert resp.status == 200
                    data = await resp.json()
                    assert data["pose"] == "wave"  # default pose
                    assert data["fps"] == CONFIG.DEFAULT_FPS
                    assert data["smoothing"] == CONFIG.DEFAULT_SMOOTHING
        finally:
            await stop_test_server(server, task)
    
    @pytest.mark.asyncio
    async def test_set_pose_via_http(self):
        """Test setting pose via HTTP POST /pose."""
        server, task = await create_test_server()
        try:
            async with aiohttp.ClientSession() as session:
                url = f"http://127.0.0.1:{server.config.HTTP_PORT}/pose"
                async with session.post(url, json={"pose": "nod", "args": {}}) as resp:
                    assert resp.status == 200
                    data = await resp.json()
                    assert data["ok"] is True
                    assert data["status"]["pose"] == "nod"
        finally:
            await stop_test_server(server, task)
    
    @pytest.mark.asyncio
    async def test_set_pose_with_args(self):
        """Test setting pose with arguments."""
        server, task = await create_test_server()
        try:
            async with aiohttp.ClientSession() as session:
                url = f"http://127.0.0.1:{server.config.HTTP_PORT}/pose"
                async with session.post(url, json={
                    "pose": "wave", 
                    "args": {"amplitude": 45.0, "speed": 2.0}
                }) as resp:
                    assert resp.status == 200
                    data = await resp.json()
                    assert data["ok"] is True
                    assert data["status"]["pose"] == "wave"
                    assert data["status"]["pose_args"]["amplitude"] == 45.0
                    assert data["status"]["pose_args"]["speed"] == 2.0
        finally:
            await stop_test_server(server, task)
    
    @pytest.mark.asyncio
    async def test_set_invalid_pose(self):
        """Test setting invalid pose returns 400."""
        server, task = await create_test_server()
        try:
            async with aiohttp.ClientSession() as session:
                url = f"http://127.0.0.1:{server.config.HTTP_PORT}/pose"
                async with session.post(url, json={"pose": "nonexistent", "args": {}}) as resp:
                    assert resp.status == 400
                    data = await resp.json()
                    assert data["ok"] is False
        finally:
            await stop_test_server(server, task)
    
    @pytest.mark.asyncio
    async def test_set_fps(self):
        """Test setting FPS via HTTP."""
        server, task = await create_test_server()
        try:
            async with aiohttp.ClientSession() as session:
                url = f"http://127.0.0.1:{server.config.HTTP_PORT}/fps"
                async with session.post(url, json={"fps": 30}) as resp:
                    assert resp.status == 200
                    data = await resp.json()
                    assert data["ok"] is True
                    assert data["status"]["fps"] == 30
        finally:
            await stop_test_server(server, task)
    
    @pytest.mark.asyncio
    async def test_set_smoothing(self):
        """Test setting smoothing via HTTP."""
        server, task = await create_test_server()
        try:
            async with aiohttp.ClientSession() as session:
                url = f"http://127.0.0.1:{server.config.HTTP_PORT}/smoothing"
                async with session.post(url, json={"smoothing": 0.3}) as resp:
                    assert resp.status == 200
                    data = await resp.json()
                    assert data["ok"] is True
                    assert data["status"]["smoothing"] == 0.3
        finally:
            await stop_test_server(server, task)
    
    @pytest.mark.asyncio
    async def test_list_poses(self):
        """Test listing poses via HTTP."""
        server, task = await create_test_server()
        try:
            async with aiohttp.ClientSession() as session:
                url = f"http://127.0.0.1:{server.config.HTTP_PORT}/poses"
                async with session.get(url) as resp:
                    assert resp.status == 200
                    data = await resp.json()
                    assert "poses" in data
                    assert data["current"] == "wave"
                    pose_names = [p["name"] for p in data["poses"]]
                    assert "wave" in pose_names
                    assert "nod" in pose_names
                    assert "track" in pose_names
                    assert "idle" in pose_names
                    assert "off" in pose_names
        finally:
            await stop_test_server(server, task)
    
    @pytest.mark.asyncio
    async def test_ws_frame_format(self):
        """Test WebSocket frame format matches stackchan_follow_pose_stream expectations."""
        server, task = await create_test_server()
        try:
            uri = f"ws://127.0.0.1:{server.config.WS_PORT}"
            async with websockets.connect(uri) as ws:
                frame_json = await ws.recv()
                frame = json.loads(frame_json)
                
                # Check required fields
                assert "ts" in frame
                assert frame["kind"] == "continuous"
                assert "angles" in frame
                assert isinstance(frame["angles"], list)
                assert len(frame["angles"]) == 1  # Single frame per message
                angles = frame["angles"][0]
                assert isinstance(angles, list)
                assert len(angles) == CONFIG.NUM_JOINTS  # yaw, pitch, roll
                for angle in angles:
                    assert isinstance(angle, float)
        finally:
            await stop_test_server(server, task)
    
    @pytest.mark.asyncio
    async def test_custom_pose_hot_reload(self):
        """Test hot-reload of custom poses without server restart."""
        server, task = await create_test_server()
        try:
            # Use the actual custom directory from the registry
            custom_dir = PoseRegistry.custom_dir
            custom_dir.mkdir(parents=True, exist_ok=True)
            
            # Write custom pose
            pose_content = '''
from reflexd.streams.pose.builtins import PoseSpec

def test_custom(num_joints=3, **kwargs):
    return [[10.0, 20.0, 30.0] for _ in range(10)]

POSE = PoseSpec(
    name="test_custom",
    func=test_custom,
    args={},
    description="Test custom pose"
)
'''
            (custom_dir / "test_custom.py").write_text(pose_content)
            
            try:
                async with aiohttp.ClientSession() as session:
                    # Reload poses
                    url = f"http://127.0.0.1:{server.config.HTTP_PORT}/poses/reload"
                    async with session.post(url, json={}) as resp:
                        assert resp.status == 200
                        data = await resp.json()
                        assert data["ok"] is True
                        assert "test_custom" in data["poses"]
                    
                    # Verify pose is available
                    url = f"http://127.0.0.1:{server.config.HTTP_PORT}/poses"
                    async with session.get(url) as resp:
                        data = await resp.json()
                        pose_names = [p["name"] for p in data["poses"]]
                        assert "test_custom" in pose_names
                    
                    # Set the custom pose
                    url = f"http://127.0.0.1:{server.config.HTTP_PORT}/pose"
                    async with session.post(url, json={"pose": "test_custom", "args": {}}) as resp:
                        assert resp.status == 200
                        data = await resp.json()
                        assert data["status"]["pose"] == "test_custom"
            finally:
                # Clean up
                (custom_dir / "test_custom.py").unlink(missing_ok=True)
                reload_all_poses()
        finally:
            await stop_test_server(server, task)


class TestPoseStreamClient:
    """Tests for PoseStreamClient (requires running server)."""
    
    @pytest.mark.asyncio
    async def test_client_status(self):
        """Test client status method."""
        server, task = await create_test_server()
        try:
            client = PoseStreamClient(
                http_url=f"http://127.0.0.1:{server.config.HTTP_PORT}"
            )
            async with client:
                status = await client.status()
                assert "pose" in status
                assert "fps" in status
                assert "smoothing" in status
        finally:
            await stop_test_server(server, task)
    
    @pytest.mark.asyncio
    async def test_client_set_pose(self):
        """Test client set_pose method."""
        server, task = await create_test_server()
        try:
            client = PoseStreamClient(
                http_url=f"http://127.0.0.1:{server.config.HTTP_PORT}"
            )
            async with client:
                result = await client.set_pose("nod")
                assert result["ok"] is True
                assert result["status"]["pose"] == "nod"
        finally:
            await stop_test_server(server, task)
    
    @pytest.mark.asyncio
    async def test_client_set_fps(self):
        """Test client set_fps method."""
        server, task = await create_test_server()
        try:
            client = PoseStreamClient(
                http_url=f"http://127.0.0.1:{server.config.HTTP_PORT}"
            )
            async with client:
                result = await client.set_fps(30)
                assert result["ok"] is True
                assert result["status"]["fps"] == 30
        finally:
            await stop_test_server(server, task)
    
    @pytest.mark.asyncio
    async def test_client_set_smoothing(self):
        """Test client set_smoothing method."""
        server, task = await create_test_server()
        try:
            client = PoseStreamClient(
                http_url=f"http://127.0.0.1:{server.config.HTTP_PORT}"
            )
            async with client:
                result = await client.set_smoothing(0.3)
                assert result["ok"] is True
                assert result["status"]["smoothing"] == 0.3
        finally:
            await stop_test_server(server, task)
    
    @pytest.mark.asyncio
    async def test_client_list_poses(self):
        """Test client list_poses method."""
        server, task = await create_test_server()
        try:
            client = PoseStreamClient(
                http_url=f"http://127.0.0.1:{server.config.HTTP_PORT}"
            )
            async with client:
                result = await client.list_poses()
                assert "poses" in result
                pose_names = [p["name"] for p in result["poses"]]
                assert "wave" in pose_names
        finally:
            await stop_test_server(server, task)
    
    @pytest.mark.asyncio
    async def test_client_reload_poses(self):
        """Test client reload_poses method."""
        server, task = await create_test_server()
        try:
            client = PoseStreamClient(
                http_url=f"http://127.0.0.1:{server.config.HTTP_PORT}"
            )
            async with client:
                result = await client.reload_poses()
                assert result["ok"] is True
        finally:
            await stop_test_server(server, task)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])