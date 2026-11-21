from reflexd.safety import clamp_move_head


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
