import asyncio
import time

import pytest

from reflexd.safety import check_rate_limit, clamp_move_head


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


class TestCheckRateLimit:
    def setup_method(self):
        from reflexd.safety import _head_times

        _head_times.clear()

    def test_check_rate_limit_allows_under_limit(self):
        assert check_rate_limit() is True
        assert check_rate_limit() is True
        assert check_rate_limit() is True

    def test_check_rate_limit_blocks_over_limit(self):
        check_rate_limit()
        check_rate_limit()
        check_rate_limit()
        assert check_rate_limit() is False

    def test_check_rate_limit_resets_after_window(self):
        check_rate_limit()
        check_rate_limit()
        check_rate_limit()
        assert check_rate_limit() is False

        time.sleep(1.1)
        assert check_rate_limit() is True

    @pytest.mark.asyncio
    async def test_check_rate_limit_thread_safe(self):
        async def call_rate_limit():
            return check_rate_limit()

        results = await asyncio.gather(*[call_rate_limit() for _ in range(10)])
        true_count = sum(1 for r in results if r)
        assert true_count == 3
