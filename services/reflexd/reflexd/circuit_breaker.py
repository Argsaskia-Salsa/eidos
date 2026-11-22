import asyncio
import logging
import os
import time
from enum import Enum

logger = logging.getLogger(__name__)


class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


def _parse_env_delays(env_var: str, default: str) -> list[float]:
    value = os.getenv(env_var, default)
    try:
        return [float(x.strip()) for x in value.split(",") if x.strip()]
    except ValueError:
        logger.warning("Invalid %s=%r, using default: %s", env_var, value, default)
        return [float(x.strip()) for x in default.split(",")]


def _parse_env_int(env_var: str, default: int) -> int:
    value = os.getenv(env_var, str(default))
    try:
        return int(value)
    except ValueError:
        logger.warning("Invalid %s=%r, using default: %d", env_var, value, default)
        return default


def _parse_env_float(env_var: str, default: float) -> float:
    value = os.getenv(env_var, str(default))
    try:
        return float(value)
    except ValueError:
        logger.warning("Invalid %s=%r, using default: %s", env_var, value, default)
        return default


CB_FAILURE_THRESHOLD = _parse_env_int("CB_FAILURE_THRESHOLD", 5)
CB_RETRY_DELAYS = _parse_env_delays("CB_RETRY_DELAYS", "0.1,0.3,1.0")
CB_HALF_OPEN_DELAY = _parse_env_float("CB_HALF_OPEN_DELAY", 30.0)


class CircuitBreaker:
    def __init__(
        self,
        failure_threshold: int | None = None,
        retry_delays: list[float] | None = None,
        half_open_delay: float | None = None,
    ):
        self.failure_threshold = (
            failure_threshold if failure_threshold is not None else CB_FAILURE_THRESHOLD
        )
        self.retry_delays = retry_delays if retry_delays is not None else CB_RETRY_DELAYS
        self.half_open_delay = half_open_delay if half_open_delay is not None else CB_HALF_OPEN_DELAY

        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._last_failure_time: float | None = None
        self._half_open_probe_allowed = False
        self._lock = asyncio.Lock()

    @property
    def state(self) -> CircuitState:
        return self._state

    async def call(self, func, *args, **kwargs):
        allow_probe = False
        async with self._lock:
            if self._state == CircuitState.OPEN:
                if (
                    self._last_failure_time
                    and time.time() - self._last_failure_time >= self.half_open_delay
                ):
                    logger.info("Circuit breaker transitioning to HALF_OPEN")
                    self._state = CircuitState.HALF_OPEN
                    self._half_open_probe_allowed = True
                    allow_probe = True
                else:
                    raise RuntimeError("Circuit breaker is OPEN")

            if self._state == CircuitState.HALF_OPEN:
                if self._half_open_probe_allowed:
                    self._half_open_probe_allowed = False
                    allow_probe = True
                else:
                    raise RuntimeError("Circuit breaker is OPEN")

        last_exception: Exception | None = None
        for attempt, delay in enumerate(self.retry_delays):
            try:
                result = await func(*args, **kwargs)
                await self._on_success(allow_probe)
                return result
            except Exception as e:  # noqa: BLE001
                last_exception = e
                if attempt < len(self.retry_delays) - 1:
                    logger.warning(
                        "Call failed (attempt %d/%d), retrying in %.2fs: %s",
                        attempt + 1,
                        len(self.retry_delays),
                        delay,
                        e,
                    )
                    await asyncio.sleep(delay)
                else:
                    logger.warning(
                        "Call failed after %d attempts: %s",
                        len(self.retry_delays),
                        e,
                    )

        await self._on_failure(allow_probe)
        assert last_exception is not None
        raise last_exception

    async def _on_success(self, was_probe: bool = False) -> None:
        async with self._lock:
            if self._state == CircuitState.HALF_OPEN and was_probe:
                logger.info("Circuit breaker transitioning to CLOSED after successful probe")
                self._state = CircuitState.CLOSED
                self._failure_count = 0
            elif self._state == CircuitState.CLOSED:
                self._failure_count = 0

    async def _on_failure(self, was_probe: bool = False) -> None:
        async with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.time()

            if self._state == CircuitState.HALF_OPEN and was_probe:
                logger.warning(
                    "Circuit breaker transitioning to OPEN after HALF_OPEN probe failure"
                )
                self._state = CircuitState.OPEN
            elif (
                self._state == CircuitState.CLOSED
                and self._failure_count >= self.failure_threshold
            ):
                logger.warning(
                    "Circuit breaker transitioning to OPEN after %d failures",
                    self._failure_count,
                )
                self._state = CircuitState.OPEN


_circuit_breaker: CircuitBreaker | None = None


def get_circuit_breaker() -> CircuitBreaker:
    global _circuit_breaker
    if _circuit_breaker is None:
        _circuit_breaker = CircuitBreaker()
    return _circuit_breaker


def create_circuit_breaker() -> CircuitBreaker:
    return CircuitBreaker()