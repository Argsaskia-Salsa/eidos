"""
Pose Stream Client - HTTP control + MCP gateway integration.
Composes base StreamClient for HTTP, adds MCP methods for stackchan_follow_pose_stream.
"""

import uuid

import aiohttp

from ..base.client import StreamClient
from .config import CONFIG


class PoseStreamClient:
    """
    Pose Stream Client with HTTP control + MCP gateway methods.

    Usage:
        async with PoseStreamClient() as client:
            await client.stream_start()
            await client.stream_stop()
            status = await client.stream_status()
            # HTTP control:
            await client.set_pose("wave")
            await client.set_fps(30)
            await client.set_smoothing(0.5)
    """

    def __init__(
        self,
        http_url: str | None = None,
        token: str | None = None,
        gateway_mcp_url: str | None = None,
        ws_url: str | None = None,
        target: str | None = None,
        max_fps: float | None = None,
        timeout: aiohttp.ClientTimeout | None = None,
    ):
        # HTTP control client (composed)
        self.http_client = StreamClient(
            http_url or CONFIG.HTTP_SERVER_URL,
            element_name="pose",
            intensity_name="smoothing",
            timeout=timeout,
        )

        # MCP configuration
        self.token = token or CONFIG.STACKCHAN_TOKEN
        self.gateway_mcp_url = gateway_mcp_url or CONFIG.GATEWAY_MCP_URL
        self.ws_url = ws_url or CONFIG.WS_SERVER_URL
        self.target = target or CONFIG.TARGET
        self.max_fps = max_fps or CONFIG.MAX_FPS

        self._session: aiohttp.ClientSession | None = None
        self._session_id: str | None = None
        self._timeout = timeout or aiohttp.ClientTimeout(
            total=10.0, connect=5.0, sock_read=5.0
        )

    async def __aenter__(self):
        self._session = aiohttp.ClientSession(timeout=self._timeout)
        await self.http_client.__aenter__()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self._session:
            await self._session.close()
            self._session = None
        await self.http_client.__aexit__(exc_type, exc_val, exc_tb)

    def _require_session(self) -> aiohttp.ClientSession:
        if not self._session:
            raise RuntimeError("Client not initialized. Use async with.")
        return self._session

    def _require_token(self):
        if not self.token:
            raise RuntimeError("STACKCHAN_TOKEN not set. Set env var or pass token=.")

    # ===== MCP METHODS =====

    async def mcp_initialize(self) -> str:
        """Initialize MCP session, get Mcp-Session-Id."""
        self._require_token()
        session = self._require_session()

        async with session.post(
            self.gateway_mcp_url,
            json={
                "jsonrpc": "2.0",
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "pose_stream_client", "version": "1.0"},
                },
                "id": str(uuid.uuid4()),
            },
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
                "Authorization": f"Bearer {self.token}",
            },
        ) as resp:
            session_id = resp.headers.get("Mcp-Session-Id")
            if not session_id:
                raise RuntimeError("No Mcp-Session-Id in response")
            self._session_id = session_id
            return session_id

    async def mcp_call(self, tool: str, args: dict) -> dict:
        """Make MCP tools/call request."""
        self._require_token()
        if not self._session_id:
            await self.mcp_initialize()

        session = self._require_session()

        async with session.post(
            self.gateway_mcp_url,
            json={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {"name": tool, "arguments": args},
                "id": str(uuid.uuid4()),
            },
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
                "Authorization": f"Bearer {self.token}",
                "Mcp-Session-Id": self._session_id,
            },
            timeout=self._timeout,
        ) as resp:
            data = await resp.json()
            if "error" in data:
                raise RuntimeError(f"MCP error: {data['error']}")
            return data.get("result", {})

    async def stream_start(self) -> dict:
        """Start pose stream via gateway MCP."""
        return await self.mcp_call(
            "stackchan_follow_pose_stream",
            {
                "action": "start",
                "url": self.ws_url,
                "target": self.target,
                "max_fps": self.max_fps,
            },
        )

    async def stream_stop(self) -> dict:
        """Stop pose stream via gateway MCP."""
        return await self.mcp_call("stackchan_follow_pose_stream", {"action": "stop"})

    async def stream_status(self) -> dict:
        """Get pose stream status via gateway MCP."""
        return await self.mcp_call("stackchan_follow_pose_stream", {"action": "status"})

    # ===== HTTP CONTROL (delegated to composed client) =====

    async def status(self) -> dict:
        return await self.http_client.status()

    async def set_pose(self, name: str, **args) -> dict:
        return await self.http_client.set_element(name, **args)

    async def set_fps(self, fps: float) -> dict:
        return await self.http_client.set_fps(fps)

    async def set_smoothing(self, smoothing: float) -> dict:
        return await self.http_client.set_intensity(smoothing)

    async def list_poses(self) -> dict:
        return await self.http_client.list_elements()

    async def reload_poses(self) -> dict:
        return await self.http_client.reload_elements()

    # Aliases for compatibility
    set_element = set_pose
    set_intensity = set_smoothing
    list_elements = list_poses
    reload_elements = reload_poses
