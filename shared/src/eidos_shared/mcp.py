from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential


@dataclass
class MCPClient:
    base_url: str
    timeout: float = 30.0
    max_retries: int = 3

    def __post_init__(self) -> None:
        self._client = httpx.AsyncClient(timeout=self.timeout)

    @retry(wait=wait_exponential(multiplier=1, min=1, max=10), stop=stop_after_attempt(3))
    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        resp = await self._client.post(
            f"{self.base_url}/tools/{tool_name}",
            json={"arguments": arguments}
        )
        resp.raise_for_status()
        return resp.json()  # type: ignore[no-any-return]

    async def health_check(self) -> bool:
        try:
            resp = await self._client.get(f"{self.base_url}/health")
            return resp.status_code == 200
        except Exception:
            return False

    async def close(self) -> None:
        await self._client.aclose()


def create_mcp_server(name: str, tools: dict[str, Callable]) -> Any:
    """Creates MCP server with registered tools.

    Uses fastmcp library. Implementation depends on fastmcp API.
    """
    try:
        from fastmcp import FastMCP
    except ImportError:
        raise RuntimeError(
            "fastmcp is required for create_mcp_server. "
            "Install with: pip install fastmcp"
        )

    mcp = FastMCP(name)

    for tool_name, tool_func in tools.items():
        mcp.tool(tool_func)

    return mcp
