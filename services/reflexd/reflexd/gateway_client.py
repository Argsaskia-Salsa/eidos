import asyncio
import logging
import os
import random
import time
import uuid

import aiohttp

from .circuit_breaker import CircuitBreaker, create_circuit_breaker
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
    def __init__(
        self, url: str, token: str, timeout: aiohttp.ClientTimeout | None = None
    ):
        self.url = url
        self.token = token
        self.timeout = timeout or _get_timeout()
        self.session: aiohttp.ClientSession | None = None
        self.session_id: str | None = None

        self.circuit_breakers: dict[str, CircuitBreaker] = {
            "move_head": create_circuit_breaker(),
            "set_avatar": create_circuit_breaker(),
            "set_all_leds": create_circuit_breaker(),
            "clear_leds": create_circuit_breaker(),
        }

    def _get_breaker(self, tool: str) -> CircuitBreaker:
        return self.circuit_breakers.get(tool, self.circuit_breakers["move_head"])

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
                logger.info(
                    "Connected to gateway at %s (attempt %d)", self.url, attempt
                )
                return
            except (aiohttp.ClientError, OSError, TimeoutError) as e:
                last_error = e
                if attempt < max_attempts:
                    delay = min(
                        base_delay * (2 ** (attempt - 1)) + random.uniform(0, 1),
                        max_delay,
                    )
                    logger.warning(
                        "Gateway connection failed (attempt %d/%d): %s. Retrying in %.2fs...",
                        attempt,
                        max_attempts,
                        e,
                        delay,
                    )
                    await asyncio.sleep(delay)
                else:
                    logger.error(
                        "Gateway connection failed after %d attempts: %s",
                        max_attempts,
                        e,
                    )
        raise RuntimeError(
            f"Failed to connect to gateway after {max_attempts} attempts"
        ) from last_error

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

        breaker = self._get_breaker(tool)

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
                    # Check for session expiry - reinitialize and retry once
                    if resp.status == 404 and "Session not found" in text:
                        logger.warning("Session expired, reinitializing...")
                        await self._reinitialize()
                        # Update session_id after reinit
                        new_session_id = self.session_id
                        headers["Mcp-Session-Id"] = new_session_id
                        # Retry once
                        async with session.post(self.url, json=request, headers=headers) as retry_resp:
                            if retry_resp.status != 200:
                                retry_text = await retry_resp.text()
                                raise RuntimeError(f"Tool call failed after reinit: {retry_resp.status} - {retry_text}")
                            retry_data = await retry_resp.json()
                            if "error" in retry_data:
                                raise RuntimeError(f"Tool error: {retry_data['error']}")
                            return retry_data.get("result", {})
                    raise RuntimeError(f"Tool call failed: {resp.status} - {text}")
                data = await resp.json()
                if "error" in data:
                    # Check for session expiry in error response
                    error_msg = str(data['error'])
                    if "Session not found" in error_msg:
                        logger.warning("Session expired (in error), reinitializing...")
                        await self._reinitialize()
                        new_session_id = self.session_id
                        headers["Mcp-Session-Id"] = new_session_id
                        async with session.post(self.url, json=request, headers=headers) as retry_resp:
                            if retry_resp.status != 200:
                                retry_text = await retry_resp.text()
                                raise RuntimeError(f"Tool call failed after reinit: {retry_resp.status} - {retry_text}")
                            retry_data = await retry_resp.json()
                            if "error" in retry_data:
                                raise RuntimeError(f"Tool error: {retry_data['error']}")
                            return retry_data.get("result", {})
                    raise RuntimeError(f"Tool error: {data['error']}")
                return data.get("result", {})

        return await breaker.call(_do_call)

    async def _reinitialize(self) -> None:
        """Reinitialize MCP session after expiry."""
        if not self.session:
            raise RuntimeError("No session to reinitialize")

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
                raise RuntimeError(f"Reinitialize failed: {resp.status}")
            new_session_id = resp.headers.get("Mcp-Session-Id")
            if not new_session_id:
                raise RuntimeError("No Mcp-Session-Id in reinitialize response")
            self.session_id = new_session_id
            logger.info("Reinitialized gateway session: %s", new_session_id)

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