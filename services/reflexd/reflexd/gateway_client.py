import asyncio
import logging
import os
import random
import time
import uuid

import aiohttp

from .circuit_breaker import get_circuit_breaker
from .safety import clamp_move_head

DEFAULT_TIMEOUT = aiohttp.ClientTimeout(total=10, connect=5)

logger = logging.getLogger(__name__)


def _get_timeout() -> aiohttp.ClientTimeout:
    timeout_str = os.getenv("GATEWAY_TIMEOUT")
    if timeout_str:
        try:
            total = float(timeout_str)
            return aiohttp.ClientTimeout(total=total, connect=min(5.0, total / 2))
        except ValueError:
            pass
    return DEFAULT_TIMEOUT


class RateLimiter:
    def __init__(self, max_per_second: float = 3.0):
        self.max_per_second = max_per_second
        self.min_interval = 1.0 / max_per_second
        self._last_call = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            wait_time = self._last_call + self.min_interval - now
            if wait_time > 0:
                await asyncio.sleep(wait_time)
            self._last_call = time.monotonic()


def _get_rate_limit_config() -> tuple[float, float]:
    try:
        limit = float(os.getenv("RATE_LIMIT_COMBINED", "3"))
    except ValueError:
        limit = 3.0
    try:
        window = float(os.getenv("RATE_LIMIT_WINDOW", "1.0"))
    except ValueError:
        window = 1.0
    return limit, window


_rate_limiter = RateLimiter(_get_rate_limit_config()[0])


class GatewayClient:
    def __init__(self, url: str, token: str, timeout: aiohttp.ClientTimeout | None = None):
        self.url = url
        self.token = token
        self.timeout = timeout or _get_timeout()
        self.session: aiohttp.ClientSession | None = None
        self.session_id: str | None = None
        self._circuit_breaker = get_circuit_breaker()

    async def connect(self) -> None:
        self.session = aiohttp.ClientSession(timeout=self.timeout)
        await self._initialize()

    async def connect_with_retry(
        self,
        max_attempts: int = 5,
        base_delay: float = 1.0,
        max_delay: float = 30.0,
    ) -> None:
        last_error: Exception | None = None
        for attempt in range(1, max_attempts + 1):
            try:
                await self.connect()
                logger.info("Connected to gateway at %s (attempt %d)", self.url, attempt)
                return
            except (aiohttp.ClientError, OSError, TimeoutError) as e:
                last_error = e
                if attempt < max_attempts:
                    delay = min(base_delay * (2 ** (attempt - 1)) + random.uniform(0, 1), max_delay)
                    logger.warning(
                        "Gateway connection failed (attempt %d/%d): %s. Retrying in %.2fs...",
                        attempt,
                        max_attempts,
                        e,
                        delay,
                    )
                    await asyncio.sleep(delay)
                else:
                    logger.error("Gateway connection failed after %d attempts: %s", max_attempts, e)
        raise RuntimeError(f"Failed to connect to gateway after {max_attempts} attempts") from last_error

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
            "Accept": "application/json, text/event-stream",
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

        session = self.session
        session_id = self.session_id
        assert session is not None
        assert session_id is not None

        if tool == "move_head":
            yaw = args.get("yaw", 0)
            pitch = args.get("pitch", 12)
            speed = args.get("speed", 100)
            yaw, pitch, speed = clamp_move_head(yaw, pitch, speed)
            args = {"yaw": yaw, "pitch": pitch, "speed": speed}

        if tool in ("move_head", "set_all_leds"):
            await _rate_limiter.acquire()

        async def _do_call() -> dict:
            request = {
                "jsonrpc": "2.0",
                "id": str(uuid.uuid4()),
                "method": "tools/call",
                "params": {"name": tool, "arguments": args},
            }

            headers = {
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
                "Authorization": f"Bearer {self.token}",
                "Mcp-Session-Id": session_id,
            }

            async with session.post(self.url, json=request, headers=headers) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    raise RuntimeError(f"Tool call failed: {resp.status} - {text}")
                data = await resp.json()
                if "error" in data:
                    raise RuntimeError(f"Tool error: {data['error']}")
                return data.get("result", {})

        return await self._circuit_breaker.call(_do_call)

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
