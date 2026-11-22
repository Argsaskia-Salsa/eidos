import asyncio
import os
import time
from unittest.mock import AsyncMock, patch

import pytest

from reflexd.circuit_breaker import (
    CB_FAILURE_THRESHOLD,
    CB_HALF_OPEN_DELAY,
    CB_RETRY_DELAYS,
    CircuitBreaker,
    CircuitState,
    create_circuit_breaker,
    get_circuit_breaker,
)


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
        cb = CircuitBreaker(failure_threshold=2, retry_delays=[0.01])
        mock_func = AsyncMock(side_effect=ValueError("test error"))

        with pytest.raises(ValueError):
            await cb.call(mock_func)
        with pytest.raises(ValueError):
            await cb.call(mock_func)

        assert cb.state == CircuitState.OPEN

    @pytest.mark.asyncio
    async def test_open_state_raises_runtime_error(self):
        cb = CircuitBreaker(failure_threshold=1, retry_delays=[0.01])
        mock_func = AsyncMock(side_effect=ValueError("test error"))
        with pytest.raises(ValueError):
            await cb.call(mock_func)

        assert cb.state == CircuitState.OPEN
        with pytest.raises(RuntimeError, match="Circuit breaker is OPEN"):
            await cb.call(AsyncMock())

    @pytest.mark.asyncio
    async def test_transitions_to_half_open_after_recovery_timeout(self):
        cb = CircuitBreaker(failure_threshold=1, retry_delays=[0.01], half_open_delay=0.01)
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
    async def test_half_open_allows_exactly_one_call(self):
        cb = CircuitBreaker(
            failure_threshold=1, retry_delays=[0.01], half_open_delay=0.01
        )
        mock_func = AsyncMock(side_effect=ValueError("test error"))
        with pytest.raises(ValueError):
            await cb.call(mock_func)

        await asyncio.sleep(0.02)

        mock_func = AsyncMock(return_value="success")
        result = await cb.call(mock_func)
        assert result == "success"
        assert cb.state == CircuitState.CLOSED

    @pytest.mark.asyncio
    async def test_half_open_failure_reopens_circuit(self):
        cb = CircuitBreaker(failure_threshold=1, retry_delays=[0.01], half_open_delay=0.01)
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
        cb = CircuitBreaker(failure_threshold=3, retry_delays=[0.01])
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
        cb = CircuitBreaker(failure_threshold=10, retry_delays=[0.01])
        mock_func = AsyncMock(return_value="success")

        async def call_cb():
            return await cb.call(mock_func)

        results = await asyncio.gather(*[call_cb() for _ in range(20)])
        assert all(r == "success" for r in results)
        assert cb.state == CircuitState.CLOSED

    @pytest.mark.asyncio
    async def test_last_failure_time_updated_on_failure(self):
        cb = CircuitBreaker(failure_threshold=2, retry_delays=[0.01])
        mock_func = AsyncMock(side_effect=ValueError("test error"))
        before = time.time()
        with pytest.raises(ValueError):
            await cb.call(mock_func)
        after = time.time()

        assert cb._last_failure_time is not None
        assert before <= cb._last_failure_time <= after


class TestRetryBehavior:
    @pytest.mark.asyncio
    async def test_retry_with_exponential_backoff(self):
        delays = [0.05, 0.1, 0.2]
        cb = CircuitBreaker(failure_threshold=5, retry_delays=delays)
        call_count = 0

        async def fail_twice_then_succeed():
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                raise ValueError("temporary error")
            return "success"

        result = await cb.call(fail_twice_then_succeed)
        assert result == "success"
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_all_retries_exhausted_opens_circuit(self):
        delays = [0.01, 0.01, 0.01]
        cb = CircuitBreaker(failure_threshold=1, retry_delays=delays)
        mock_func = AsyncMock(side_effect=ValueError("persistent error"))

        with pytest.raises(ValueError):
            await cb.call(mock_func)

        assert cb.state == CircuitState.OPEN
        assert mock_func.call_count == 3

    @pytest.mark.asyncio
    async def test_retry_delays_are_respected(self):
        delays = [0.05, 0.1]
        cb = CircuitBreaker(failure_threshold=5, retry_delays=delays)
        call_times = []

        async def always_fail():
            call_times.append(time.monotonic())
            raise ValueError("error")

        with pytest.raises(ValueError):
            await cb.call(always_fail)

        assert len(call_times) == 2
        elapsed = call_times[1] - call_times[0]
        assert elapsed >= 0.04


class TestPerToolIsolation:
    @pytest.mark.asyncio
    async def test_separate_breakers_per_tool(self):
        breaker1 = create_circuit_breaker()
        breaker2 = create_circuit_breaker()

        mock_func = AsyncMock(side_effect=ValueError("error"))

        for _ in range(5):
            with pytest.raises(ValueError):
                await breaker1.call(mock_func)

        assert breaker1.state == CircuitState.OPEN
        assert breaker2.state == CircuitState.CLOSED

    @pytest.mark.asyncio
    async def test_failure_in_one_tool_does_not_affect_another(self):
        breaker1 = create_circuit_breaker()
        breaker2 = create_circuit_breaker()

        mock_func = AsyncMock(side_effect=ValueError("error"))

        for _ in range(5):
            with pytest.raises(ValueError):
                await breaker1.call(mock_func)

        result = await breaker2.call(AsyncMock(return_value="ok"))
        assert result == "ok"
        assert breaker2.state == CircuitState.CLOSED


class TestConfigurationFromEnv:
    def test_default_failure_threshold(self):
        with patch.dict(os.environ, {}, clear=True):
            from reflexd.circuit_breaker import CB_FAILURE_THRESHOLD
            assert CB_FAILURE_THRESHOLD == 5

    def test_custom_failure_threshold_from_env(self):
        with patch.dict(os.environ, {"CB_FAILURE_THRESHOLD": "3"}):
            import importlib
            import reflexd.circuit_breaker as cb_module
            importlib.reload(cb_module)
            assert cb_module.CB_FAILURE_THRESHOLD == 3

    def test_default_retry_delays(self):
        with patch.dict(os.environ, {}, clear=True):
            from reflexd.circuit_breaker import CB_RETRY_DELAYS
            assert CB_RETRY_DELAYS == [0.1, 0.3, 1.0]

    def test_custom_retry_delays_from_env(self):
        with patch.dict(os.environ, {"CB_RETRY_DELAYS": "0.2,0.5,1.5"}):
            import importlib
            import reflexd.circuit_breaker as cb_module
            importlib.reload(cb_module)
            assert cb_module.CB_RETRY_DELAYS == [0.2, 0.5, 1.5]

    def test_default_half_open_delay(self):
        with patch.dict(os.environ, {}, clear=True):
            from reflexd.circuit_breaker import CB_HALF_OPEN_DELAY
            assert CB_HALF_OPEN_DELAY == 30.0

    def test_custom_half_open_delay_from_env(self):
        with patch.dict(os.environ, {"CB_HALF_OPEN_DELAY": "60"}):
            import importlib
            import reflexd.circuit_breaker as cb_module
            importlib.reload(cb_module)
            assert cb_module.CB_HALF_OPEN_DELAY == 60.0

    def test_invalid_env_falls_back_to_defaults(self):
        with patch.dict(os.environ, {"CB_FAILURE_THRESHOLD": "invalid", "CB_RETRY_DELAYS": "not,valid", "CB_HALF_OPEN_DELAY": "bad"}):
            import importlib
            import reflexd.circuit_breaker as cb_module
            importlib.reload(cb_module)
            assert cb_module.CB_FAILURE_THRESHOLD == 5
            assert cb_module.CB_RETRY_DELAYS == [0.1, 0.3, 1.0]
            assert cb_module.CB_HALF_OPEN_DELAY == 30.0


class TestCreateCircuitBreakerFactory:
    def test_create_circuit_breaker_returns_new_instance(self):
        from reflexd.circuit_breaker import CircuitBreaker as CBClass
        cb1 = create_circuit_breaker()
        cb2 = create_circuit_breaker()
        assert cb1 is not cb2
        assert isinstance(cb1, CBClass)

    def test_create_circuit_breaker_uses_env_config(self):
        with patch.dict(os.environ, {"CB_FAILURE_THRESHOLD": "7"}):
            import importlib
            import reflexd.circuit_breaker as cb_module
            importlib.reload(cb_module)
            cb = cb_module.create_circuit_breaker()
            assert cb.failure_threshold == 7