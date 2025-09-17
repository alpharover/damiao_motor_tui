"""Protocol helpers for Damiao DM-J4340-2EC motors."""

from __future__ import annotations

from dataclasses import dataclass
from struct import pack
from typing import Iterable

ENABLE_FRAME = bytes([0xFF] * 7 + [0xFC])
DISABLE_FRAME = bytes([0xFF] * 7 + [0xFD])
ZERO_FRAME = bytes([0xFF] * 7 + [0xFE])


@dataclass(slots=True)
class Feedback:
    """Decoded feedback frame values."""

    esc_id: int
    status: int
    position_raw: int
    velocity_raw: int
    torque_raw: int
    temp_mos: int
    temp_rotor: int

    def to_engineering(self, *, p_max: float, v_max: float, t_max: float) -> "FeedbackEngineering":
        return FeedbackEngineering(
            esc_id=self.esc_id,
            status=self.status,
            position_rad=self.position_raw / 32767.0 * p_max,
            velocity_rad_s=self.velocity_raw / 2047.0 * v_max,
            torque_nm=self.torque_raw / 2047.0 * t_max,
            temp_mos_c=self.temp_mos,
            temp_rotor_c=self.temp_rotor,
        )


@dataclass(slots=True)
class FeedbackEngineering:
    """Feedback converted into engineering units."""

    esc_id: int
    status: int
    position_rad: float
    velocity_rad_s: float
    torque_nm: float
    temp_mos_c: float
    temp_rotor_c: float


def frame_enable(esc_id: int) -> tuple[int, bytes]:
    return esc_id, ENABLE_FRAME


def frame_disable(esc_id: int) -> tuple[int, bytes]:
    return esc_id, DISABLE_FRAME


def frame_zero(esc_id: int) -> tuple[int, bytes]:
    return esc_id, ZERO_FRAME


def frame_speed(esc_id: int, velocity_rad_s: float) -> tuple[int, bytes]:
    payload = pack("<f", velocity_rad_s) + bytes(4)
    return 0x200 + esc_id, payload


def frame_position_speed(esc_id: int, position_rad: float, velocity_rad_s: float) -> tuple[int, bytes]:
    payload = pack("<ff", position_rad, velocity_rad_s)
    return 0x100 + esc_id, payload


def decode_feedback(data: bytes) -> Feedback:
    if len(data) != 8:
        raise ValueError("Feedback frame must be 8 bytes")
    status_field = data[0]
    esc_id = status_field & 0x0F
    status = status_field >> 4
    pos_raw = _to_signed(data[1] << 8 | data[2], bits=16)
    vel_raw = _to_signed(((data[3] << 4) | (data[4] >> 4)) & 0xFFF, bits=12)
    torque_raw = _to_signed(((data[4] & 0x0F) << 8) | data[5], bits=12)
    temp_mos = data[6]
    temp_rotor = data[7]
    return Feedback(
        esc_id=esc_id,
        status=status,
        position_raw=pos_raw,
        velocity_raw=vel_raw,
        torque_raw=torque_raw,
        temp_mos=temp_mos,
        temp_rotor=temp_rotor,
    )


def build_filters(mst_ids: Iterable[int]) -> list[dict[str, int]]:
    """Create kernel filter definitions for a collection of MST IDs."""
    return [{"can_id": mst_id, "can_mask": 0x7FF, "extended": 0} for mst_id in mst_ids]


def _to_signed(value: int, *, bits: int) -> int:
    sign_bit = 1 << (bits - 1)
    return (value ^ sign_bit) - sign_bit


__all__ = [
    "Feedback",
    "FeedbackEngineering",
    "frame_enable",
    "frame_disable",
    "frame_zero",
    "frame_speed",
    "frame_position_speed",
    "decode_feedback",
    "build_filters",
]
