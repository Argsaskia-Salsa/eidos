import asyncio
import os
from unittest.mock import patch

import pytest

from reflexd.safety import (
    _get_rate_limit_config,
    _head_times,
    check_rate_limit,
    clamp_move_head,
)


class TestClampMoveHead:
    def test_clamp_move_head_bounds_yaw(self):
        yaw, _pitch, _speed = clamp_move_head(180, 45, 100)
        assert yaw == 90

        yaw, _pitch, _speed = clamp_move_head(-180, 45, 100)
        assert yaw == -90

        yaw, _pitch, _speed = clamp_move_head(45, 45, 100)
        assert yaw == 45

    def test_clamp_move_head_bounds_pitch(self):
        _yaw, pitch, _speed = clamp_move_head(0, 180, 100)
        assert pitch == 85

        _yaw, pitch, _speed = clamp_move_head(0, -10, 100)
        assert pitch == 5

        _yaw, pitch, _speed = clamp_move_head(0, 45, 100)
        assert pitch == 45

    def test_clamp_move_head_bounds_speed(self):
        _yaw, _pitch, speed = clamp_move_head(0, 45, 500)
        assert speed == 200

        _yaw, _pitch, speed = clamp_move_head(0, 45, 5)
        assert speed == 10

        _yaw, _pitch, speed = clamp_move_head(0, 45, 150)
        assert speed == 150


class TestGetRateLimitConfig:
    def test_get_rate_limit_config_default(self):
        with patch.dict(os.environ, {}, clear=True):
            limit, window = _get_rate_limit_config()
            assert limit == 3
            assert window == 1.0

    def test_get_rate_limit_config_from_env(self):
        with patch.dict(os.environ, {"MOVE_HEAD_RATE_LIMIT": "5", "MOVE_HEAD_RATE_WINDOW": "2.0"}):
            limit, window = _get_rate_limit_config()
            assert limit == 5
            assert window == 2.0

    def test_get_rate_limit_config_invalid_limit_falls_back(self):
        with patch.dict(os.environ, {"MOVE_HEAD_RATE_LIMIT": "invalid", "MOVE_HEAD_RATE_WINDOW": "1.0"}):
            limit, window = _get_rate_limit_config()
            assert limit == 3
            assert window == 1.0

    def test_get_rate_limit_config_invalid_window_falls_back(self):
        with patch.dict(os.environ, {"MOVE_HEAD_RATE_LIMIT": "3", "MOVE_HEAD_RATE_WINDOW": "invalid"}):
            limit, window = _get_rate_limit_config()
            assert limit == 3
            assert window == 1.0


class TestCheckRateLimit:
    def setup_method(self):
        _head_times.clear()

    @pytest.mark.asyncio
    async def test_check_rate_limit_allows_under_limit(self):
        assert await check_rate_limit() is True
        assert await check_rate_limit() is True
        assert await check_rate_limit() is True

    @pytest.mark.asyncio
    async def test_check_rate_limit_blocks_over_limit(self):
        await check_rate_limit()
        await check_rate_limit()
        await check_rate_limit()
        assert await check_rate_limit() is False

    @pytest.mark.asyncio
    async def test_check_rate_limit_resets_after_window(self):
        await check_rate_limit()
        await check_rate_limit()
        await check_rate_limit()
        assert await check_rate_limit() is False

        await asyncio.sleep(1.1)
        assert await check_rate_limit() is True

    @pytest.mark.asyncio
    async def test_check_rate_limit_thread_safe(self):
        async def call_rate_limit():
            return await check_rate_limit()

        results = await asyncio.gather(*[call_rate_limit() for _ in range(10)])
        true_count = sum(1 for r in results if r)
        assert true_count == 3
