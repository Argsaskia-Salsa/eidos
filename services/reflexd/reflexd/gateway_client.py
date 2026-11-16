import uuid

import aiohttp

from .safety import check_rate_limit, clamp_move_head


class GatewayClient:
    def __init__(self, url: str, token: str):
        self.url = url
        self.token = token
        self.session: aiohttp.ClientSession | None = None
        self.session_id: str | None = None

    async def connect(self) -> None:
        self.session = aiohttp.ClientSession()
        await self._initialize()

    async def _initialize(self) -> None:
        if not self.session:
            raise RuntimeError("Session not created")

        request = {
            "jsonrpc": "2.0",
            "id": str(uuid.uuid4()),
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "reflexd", "version": "0.1.0"},
            },
        }

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.token}",
        }

        async with self.session.post(self.url, json=request, headers=headers) as resp:
            if resp.status != 200:
                raise RuntimeError(f"Initialize failed: {resp.status}")
            self.session_id = resp.headers.get("Mcp-Session-Id")
            if not self.session_id:
                raise RuntimeError("No Mcp-Session-Id in response")

    async def call(self, tool: str, args: dict) -> dict:
        if not self.session or not self.session_id:
            raise RuntimeError("Not connected to gateway")

        if tool == "move_head":
            yaw = args.get("yaw", 0)
            pitch = args.get("pitch", 60)
            speed = args.get("speed", 100)
            yaw, pitch, speed = clamp_move_head(yaw, pitch, speed)
            if not check_rate_limit():
                raise RuntimeError("Rate limit exceeded for move_head (max 3/sec)")
            args = {"yaw": yaw, "pitch": pitch, "speed": speed}

        request = {
            "jsonrpc": "2.0",
            "id": str(uuid.uuid4()),
            "method": "tools/call",
            "params": {"name": tool, "arguments": args},
        }

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.token}",
            "Mcp-Session-Id": self.session_id,
        }

        async with self.session.post(self.url, json=request, headers=headers) as resp:
            if resp.status != 200:
                text = await resp.text()
                raise RuntimeError(f"Tool call failed: {resp.status} - {text}")
            data = await resp.json()
            if "error" in data:
                raise RuntimeError(f"Tool error: {data['error']}")
            return data.get("result", {})

    async def close(self) -> None:
        if self.session:
            await self.session.close()
            self.session = None
            self.session_id = None


_gateway_client: GatewayClient | None = None


def get_gateway_client() -> GatewayClient | None:
    return _gateway_client


def set_gateway_client(client: GatewayClient) -> None:
    global _gateway_client
    _gateway_client = client
