import asyncio
import time
from unittest.mock import AsyncMock

import pytest

from reflexd.circuit_breaker import CircuitBreaker, CircuitState, get_circuit_breaker


class TestCircuitBreaker:
    @pytest.fixture(autouse=True)
    def reset_circuit_breaker(self):
        import reflexd.circuit_breaker as cb

        cb._circuit_breaker = None
        yield
        cb._circuit_breaker = None

    def test_initial_state_closed(self):
        cb = CircuitBreaker()
        assert cb.state == CircuitState.CLOSED

    @pytest.mark.asyncio
    async def test_call_succeeds_in_closed_state(self):
        cb = CircuitBreaker()
        mock_func = AsyncMock(return_value="success")
        result = await cb.call(mock_func)
        assert result == "success"
        assert cb.state == CircuitState.CLOSED

    @pytest.mark.asyncio
    async def test_failure_count_increments_on_exception(self):
        cb = CircuitBreaker(failure_threshold=3)
        mock_func = AsyncMock(side_effect=ValueError("test error"))
        with pytest.raises(ValueError):
            await cb.call(mock_func)
        assert cb._failure_count == 1

    @pytest.mark.asyncio
    async def test_opens_after_failure_threshold(self):
        cb = CircuitBreaker(failure_threshold=2)
        mock_func = AsyncMock(side_effect=ValueError("test error"))

        with pytest.raises(ValueError):
            await cb.call(mock_func)
        with pytest.raises(ValueError):
            await cb.call(mock_func)

        assert cb.state == CircuitState.OPEN

    @pytest.mark.asyncio
    async def test_open_state_raises_runtime_error(self):
        cb = CircuitBreaker(failure_threshold=1)
        mock_func = AsyncMock(side_effect=ValueError("test error"))
        with pytest.raises(ValueError):
            await cb.call(mock_func)

        assert cb.state == CircuitState.OPEN
        with pytest.raises(RuntimeError, match="Circuit breaker is OPEN"):
            await cb.call(AsyncMock())

    @pytest.mark.asyncio
    async def test_transitions_to_half_open_after_recovery_timeout(self):
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout=0.01)
        mock_func = AsyncMock(side_effect=ValueError("test error"))
        with pytest.raises(ValueError):
            await cb.call(mock_func)

        assert cb.state == CircuitState.OPEN
        await asyncio.sleep(0.02)

        mock_func = AsyncMock(return_value="success")
        result = await cb.call(mock_func)
        assert result == "success"
        assert cb.state == CircuitState.CLOSED

    @pytest.mark.asyncio
    async def test_half_open_limits_calls(self):
        cb = CircuitBreaker(
            failure_threshold=1, recovery_timeout=0.01, half_open_max_calls=2
        )
        mock_func = AsyncMock(side_effect=ValueError("test error"))
        with pytest.raises(ValueError):
            await cb.call(mock_func)

        await asyncio.sleep(0.02)

        # In HALF_OPEN, make concurrent calls that don't complete immediately
        # This tests the limit is checked before the call
        async def slow_func():
            await asyncio.sleep(0.05)
            return "success"

        # Start first two calls concurrently
        task1 = asyncio.create_task(cb.call(slow_func))
        task2 = asyncio.create_task(cb.call(slow_func))

        # Give them time to acquire lock and increment _half_open_calls
        await asyncio.sleep(0.01)

        # Third call should be rejected immediately
        with pytest.raises(
            RuntimeError, match="Circuit breaker HALF_OPEN limit reached"
        ):
            await cb.call(slow_func)

        # Wait for the first two to complete
        await task1
        await task2

        assert cb.state == CircuitState.CLOSED

    @pytest.mark.asyncio
    async def test_half_open_success_closes_circuit(self):
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout=0.01)
        mock_func = AsyncMock(side_effect=ValueError("test error"))
        with pytest.raises(ValueError):
            await cb.call(mock_func)

        await asyncio.sleep(0.02)

        mock_func = AsyncMock(return_value="success")
        await cb.call(mock_func)

        assert cb.state == CircuitState.CLOSED
        assert cb._failure_count == 0

    @pytest.mark.asyncio
    async def test_half_open_failure_reopens_circuit(self):
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout=0.01)
        mock_func = AsyncMock(side_effect=ValueError("test error"))
        with pytest.raises(ValueError):
            await cb.call(mock_func)

        await asyncio.sleep(0.02)

        mock_func = AsyncMock(side_effect=ValueError("test error"))
        with pytest.raises(ValueError):
            await cb.call(mock_func)

        assert cb.state == CircuitState.OPEN

    @pytest.mark.asyncio
    async def test_closed_state_resets_failure_count_on_success(self):
        cb = CircuitBreaker(failure_threshold=3)
        mock_func = AsyncMock(side_effect=ValueError("test error"))
        with pytest.raises(ValueError):
            await cb.call(mock_func)
        assert cb._failure_count == 1

        mock_func = AsyncMock(return_value="success")
        await cb.call(mock_func)
        assert cb._failure_count == 0

    @pytest.mark.asyncio
    async def test_get_circuit_breaker_returns_singleton(self):
        cb1 = get_circuit_breaker()
        cb2 = get_circuit_breaker()
        assert cb1 is cb2

    @pytest.mark.asyncio
    async def test_concurrent_calls_are_thread_safe(self):
        cb = CircuitBreaker(failure_threshold=10)
        mock_func = AsyncMock(return_value="success")

        async def call_cb():
            return await cb.call(mock_func)

        results = await asyncio.gather(*[call_cb() for _ in range(20)])
        assert all(r == "success" for r in results)
        assert cb.state == CircuitState.CLOSED

    @pytest.mark.asyncio
    async def test_last_failure_time_updated_on_failure(self):
        cb = CircuitBreaker(failure_threshold=2)
        mock_func = AsyncMock(side_effect=ValueError("test error"))
        before = time.time()
        with pytest.raises(ValueError):
            await cb.call(mock_func)
        after = time.time()

        assert cb._last_failure_time is not None
        assert before <= cb._last_failure_time <= after
