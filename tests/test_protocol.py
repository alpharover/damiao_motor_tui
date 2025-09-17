from dm_tui.dmlib import protocol


def _encode_feedback(status: int, esc_id: int, pos: int, vel: int, torque: int, mos: int, rotor: int) -> bytes:
    status_byte = ((status & 0x0F) << 4) | (esc_id & 0x0F)
    pos &= 0xFFFF
    vel &= 0xFFF
    torque &= 0xFFF
    return bytes(
        [
            status_byte,
            (pos >> 8) & 0xFF,
            pos & 0xFF,
            (vel >> 4) & 0xFF,
            ((vel & 0x0F) << 4) | ((torque >> 8) & 0x0F),
            torque & 0xFF,
            mos & 0xFF,
            rotor & 0xFF,
        ]
    )


def test_decode_feedback_round_trip():
    frame = _encode_feedback(status=1, esc_id=2, pos=0x1234, vel=0x07F, torque=0x801, mos=45, rotor=50)
    feedback = protocol.decode_feedback(frame)
    assert feedback.esc_id == 2
    assert feedback.status == 1
    assert feedback.position_raw == 0x1234
    assert feedback.velocity_raw == 0x07F
    assert feedback.torque_raw == -0x7FF
    engineering = feedback.to_engineering(p_max=12.0, v_max=30.0, t_max=20.0)
    assert engineering.esc_id == 2
    assert engineering.temp_mos_c == 45
    assert abs(engineering.velocity_rad_s - (feedback.velocity_raw / 2047.0 * 30.0)) < 1e-6


def test_frame_builders_have_correct_length():
    _, enable_data = protocol.frame_enable(1)
    assert len(enable_data) == 8
    _, speed_data = protocol.frame_speed(1, 3.14)
    assert len(speed_data) == 8
    _, pos_speed_data = protocol.frame_position_speed(1, 1.0, 2.0)
    assert len(pos_speed_data) == 8
