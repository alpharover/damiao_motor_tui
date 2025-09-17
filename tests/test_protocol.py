import pytest

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


def test_frame_mit_round_trip_preserves_values():
    limits = dict(
        position_limit=5.0,
        velocity_limit=8.0,
        torque_limit=6.0,
        kp_limit=250.0,
        kd_limit=12.0,
    )
    arb_id, payload = protocol.frame_mit(
        0x05,
        position_rad=1.25,
        velocity_rad_s=-2.4,
        torque_nm=1.9,
        kp=120.0,
        kd=3.5,
        **limits,
    )
    assert arb_id == 0x305
    assert len(payload) == 8
    position, velocity, torque, kp, kd = protocol.decode_mit(payload, **limits)
    assert position == pytest.approx(1.25, rel=1e-2, abs=1e-3)
    assert velocity == pytest.approx(-2.4, rel=1e-2, abs=1e-3)
    assert torque == pytest.approx(1.9, rel=1e-2, abs=1e-3)
    assert kp == pytest.approx(120.0, rel=1e-2, abs=1e-3)
    assert kd == pytest.approx(3.5, rel=1e-2, abs=1e-3)


def test_frame_mit_clamps_out_of_range_inputs():
    limits = dict(
        position_limit=2.0,
        velocity_limit=4.0,
        torque_limit=3.0,
        kp_limit=150.0,
        kd_limit=6.0,
    )
    _, payload = protocol.frame_mit(
        0x01,
        position_rad=10.0,
        velocity_rad_s=-10.0,
        torque_nm=10.0,
        kp=400.0,
        kd=20.0,
        **limits,
    )
    position, velocity, torque, kp, kd = protocol.decode_mit(payload, **limits)
    assert position <= limits["position_limit"] + 1e-6
    assert position >= -limits["position_limit"] - 1e-6
    assert velocity == pytest.approx(-limits["velocity_limit"], abs=1e-3)
    assert torque == pytest.approx(limits["torque_limit"], abs=1e-3)
    assert kp == pytest.approx(limits["kp_limit"], abs=1e-3)
    assert kd == pytest.approx(limits["kd_limit"], abs=1e-3)
