"""
Base StreamClient - async HTTP client for stream control endpoints.
Pure HTTP, no MCP. Subclasses compose this for transport-specific methods.
"""

import aiohttp
from typing import Any


class StreamClient:
    """
    Generic HTTP client for stream server control.
    
    Usage:
        client = StreamClient("http://localhost:8773", element_name="pattern", intensity_name="brightness")
        await client.status()
        await client.set_element("rainbow")
        await client.set_fps(30)
        await client.set_intensity(0.5)
        await client.list_elements()
        await client.reload_elements()
    """
    
    def __init__(
        self, 
        base_url: str, 
        element_name: str = "pattern",
        intensity_name: str = "brightness",
        timeout: aiohttp.ClientTimeout | None = None
    ):
        self.base_url = base_url.rstrip("/")
        self.element_name = element_name
        self.intensity_name = intensity_name
        self.timeout = timeout or aiohttp.ClientTimeout(total=10.0, connect=5.0, sock_read=5.0)
        self._session: aiohttp.ClientSession | None = None
    
    async def __aenter__(self):
        self._session = aiohttp.ClientSession(timeout=self.timeout)
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self._session:
            await self._session.close()
            self._session = None
    
    def _require_session(self) -> aiohttp.ClientSession:
        if not self._session:
            raise RuntimeError("Client not initialized. Use async with or manually create session.")
        return self._session
    
    async def _get(self, path: str) -> dict:
        session = self._require_session()
        async with session.get(f"{self.base_url}{path}") as resp:
            return await resp.json()
    
    async def _post(self, path: str, data: dict) -> dict:
        session = self._require_session()
        async with session.post(f"{self.base_url}{path}", json=data) as resp:
            return await resp.json()
    
    # ===== CONTROL METHODS =====
    
    async def status(self) -> dict:
        """Get server status."""
        return await self._get("/status")
    
    async def set_element(self, name: str, **args) -> dict:
        """Set the active element (pattern/pose/mode)."""
        return await self._post(f"/{self.element_name}", {self.element_name: name, "args": args})
    
    async def set_fps(self, fps: float) -> dict:
        """Set frames per second."""
        return await self._post("/fps", {"fps": fps})
    
    async def set_intensity(self, intensity: float) -> dict:
        """Set intensity (brightness/smoothing/sensitivity)."""
        return await self._post(f"/{self.intensity_name}", {self.intensity_name: intensity})
    
    async def list_elements(self) -> dict:
        """List all available elements."""
        return await self._get(f"/{self.element_name}s")
    
    async def reload_elements(self) -> dict:
        """Hot-reload custom elements."""
        return await self._post(f"/{self.element_name}s/reload", {})