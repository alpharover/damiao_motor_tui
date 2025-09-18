"""Protocol helpers for Damiao DM-J4340-2EC motors."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from struct import pack, unpack
from typing import Iterable

from . import params

ENABLE_FRAME = bytes([0xFF] * 7 + [0xFC])
DISABLE_FRAME = bytes([0xFF] * 7 + [0xFD])
ZERO_FRAME = bytes([0xFF] * 7 + [0xFE])
MANAGEMENT_ARBITRATION_ID = 0x7FF

MIT_DEFAULT_POSITION_LIMIT = 12.0
MIT_DEFAULT_VELOCITY_LIMIT = 30.0
MIT_DEFAULT_TORQUE_LIMIT = 20.0
MIT_DEFAULT_KP_LIMIT = 400.0
MIT_DEFAULT_KD_LIMIT = 10.0


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


@dataclass(slots=True)
class ManagementResponse:
    """Parsed management response payload from the 0x7FF channel."""

    esc_id: int
    command: int
    rid: int
    value: int


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


def frame_mit(
    esc_id: int,
    *,
    position_rad: float,
    velocity_rad_s: float,
    torque_nm: float,
    kp: float,
    kd: float,
    position_limit: float = MIT_DEFAULT_POSITION_LIMIT,
    velocity_limit: float = MIT_DEFAULT_VELOCITY_LIMIT,
    torque_limit: float = MIT_DEFAULT_TORQUE_LIMIT,
    kp_limit: float = MIT_DEFAULT_KP_LIMIT,
    kd_limit: float = MIT_DEFAULT_KD_LIMIT,
) -> tuple[int, bytes]:
    payload = pack_mit_payload(
        position_rad=position_rad,
        velocity_rad_s=velocity_rad_s,
        torque_nm=torque_nm,
        kp=kp,
        kd=kd,
        position_limit=position_limit,
        velocity_limit=velocity_limit,
        torque_limit=torque_limit,
        kp_limit=kp_limit,
        kd_limit=kd_limit,
    )
    return 0x300 + esc_id, payload


def is_enable_payload(payload: bytes) -> bool:
    return payload == ENABLE_FRAME


def is_disable_payload(payload: bytes) -> bool:
    return payload == DISABLE_FRAME


def is_zero_payload(payload: bytes) -> bool:
    return payload == ZERO_FRAME


def unpack_speed_payload(payload: bytes) -> float:
    if len(payload) != 8:
        raise ValueError("Speed command payload must be 8 bytes")
    return unpack("<f", payload[:4])[0]


def unpack_position_speed_payload(payload: bytes) -> tuple[float, float]:
    if len(payload) != 8:
        raise ValueError("Position-speed payload must be 8 bytes")
    position, velocity = unpack("<ff", payload)
    return position, velocity


def _build_management_payload(esc_id: int, command: int, rid: int = 0, value: int = 0) -> bytes:
    payload = bytearray(8)
    payload[0] = esc_id & 0xFF
    payload[1] = (esc_id >> 8) & 0xFF
    payload[2] = command & 0xFF
    payload[3] = rid & 0xFF
    payload[4:8] = (value & 0xFFFFFFFF).to_bytes(4, "little")
    return bytes(payload)


def pack_mit_payload(
    *,
    position_rad: float,
    velocity_rad_s: float,
    torque_nm: float,
    kp: float,
    kd: float,
    position_limit: float = MIT_DEFAULT_POSITION_LIMIT,
    velocity_limit: float = MIT_DEFAULT_VELOCITY_LIMIT,
    torque_limit: float = MIT_DEFAULT_TORQUE_LIMIT,
    kp_limit: float = MIT_DEFAULT_KP_LIMIT,
    kd_limit: float = MIT_DEFAULT_KD_LIMIT,
) -> bytes:
    p_min, p_max = -abs(position_limit), abs(position_limit)
    v_min, v_max = -abs(velocity_limit), abs(velocity_limit)
    t_min, t_max = -abs(torque_limit), abs(torque_limit)
    kp_min, kp_max = 0.0, abs(kp_limit)
    kd_min, kd_max = 0.0, abs(kd_limit)

    p_int = _float_to_uint(position_rad, p_min, p_max, bits=16)
    v_int = _float_to_uint(velocity_rad_s, v_min, v_max, bits=12)
    kp_int = _float_to_uint(kp, kp_min, kp_max, bits=12)
    kd_int = _float_to_uint(kd, kd_min, kd_max, bits=12)
    t_int = _float_to_uint(torque_nm, t_min, t_max, bits=12)

    payload = bytearray(8)
    payload[0] = (p_int >> 8) & 0xFF
    payload[1] = p_int & 0xFF
    payload[2] = (v_int >> 4) & 0xFF
    payload[3] = ((v_int & 0x0F) << 4) | ((kp_int >> 8) & 0x0F)
    payload[4] = kp_int & 0xFF
    payload[5] = (kd_int >> 4) & 0xFF
    payload[6] = ((kd_int & 0x0F) << 4) | ((t_int >> 8) & 0x0F)
    payload[7] = t_int & 0xFF
    return bytes(payload)


def decode_mit(
    payload: bytes,
    *,
    position_limit: float = MIT_DEFAULT_POSITION_LIMIT,
    velocity_limit: float = MIT_DEFAULT_VELOCITY_LIMIT,
    torque_limit: float = MIT_DEFAULT_TORQUE_LIMIT,
    kp_limit: float = MIT_DEFAULT_KP_LIMIT,
    kd_limit: float = MIT_DEFAULT_KD_LIMIT,
) -> tuple[float, float, float, float, float]:
    if len(payload) != 8:
        raise ValueError("MIT command payload must be 8 bytes")

    p_int = (payload[0] << 8) | payload[1]
    v_int = (payload[2] << 4) | (payload[3] >> 4)
    kp_int = ((payload[3] & 0x0F) << 8) | payload[4]
    kd_int = (payload[5] << 4) | (payload[6] >> 4)
    t_int = ((payload[6] & 0x0F) << 8) | payload[7]

    p_min, p_max = -abs(position_limit), abs(position_limit)
    v_min, v_max = -abs(velocity_limit), abs(velocity_limit)
    t_min, t_max = -abs(torque_limit), abs(torque_limit)
    kp_min, kp_max = 0.0, abs(kp_limit)
    kd_min, kd_max = 0.0, abs(kd_limit)

    position = _uint_to_float(p_int, p_min, p_max, bits=16)
    velocity = _uint_to_float(v_int, v_min, v_max, bits=12)
    kp = _uint_to_float(kp_int, kp_min, kp_max, bits=12)
    kd = _uint_to_float(kd_int, kd_min, kd_max, bits=12)
    torque = _uint_to_float(t_int, t_min, t_max, bits=12)
    return position, velocity, torque, kp, kd


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

    filters: list[dict[str, int]] = []
    seen: set[int] = set()
    for mst_id in mst_ids:
        if mst_id in seen:
            continue
        filters.append({"can_id": mst_id, "can_mask": 0x7FF, "extended": 0})
        seen.add(mst_id)

    management_filter = {
        "can_id": MANAGEMENT_ARBITRATION_ID,
        "can_mask": 0x7FF,
        "extended": 0,
    }
    if MANAGEMENT_ARBITRATION_ID not in seen:
        filters.append(management_filter)
    else:
        # Ensure the management arbitration ID appears exactly once even if provided explicitly.
        filters = [
            filter_entry
            for filter_entry in filters
            if not (
                filter_entry.get("can_id") == MANAGEMENT_ARBITRATION_ID
                and filter_entry.get("can_mask") == 0x7FF
                and filter_entry.get("extended") == 0
            )
        ]
        filters.append(management_filter)

    return filters


def frame_param_read(esc_id: int, rid: int) -> tuple[int, bytes]:
    payload = _build_management_payload(esc_id, params.MANAGEMENT_READ, rid)
    return MANAGEMENT_ARBITRATION_ID, payload


def frame_param_write(esc_id: int, rid: int, value: int) -> tuple[int, bytes]:
    payload = _build_management_payload(esc_id, params.MANAGEMENT_WRITE, rid, value)
    return MANAGEMENT_ARBITRATION_ID, payload


def frame_param_save(esc_id: int) -> tuple[int, bytes]:
    payload = _build_management_payload(esc_id, params.MANAGEMENT_SAVE)
    return MANAGEMENT_ARBITRATION_ID, payload


def frame_param_refresh(esc_id: int) -> tuple[int, bytes]:
    payload = _build_management_payload(esc_id, params.MANAGEMENT_REFRESH)
    return MANAGEMENT_ARBITRATION_ID, payload


def parse_management_response(data: bytes) -> ManagementResponse:
    if len(data) != 8:
        raise ValueError("Management response must be 8 bytes")
    esc_id = data[0] | (data[1] << 8)
    command = data[2]
    rid = data[3]
    value = int.from_bytes(data[4:8], "little")
    return ManagementResponse(esc_id=esc_id, command=command, rid=rid, value=value)


def _to_signed(value: int, *, bits: int) -> int:
    sign_bit = 1 << (bits - 1)
    return (value ^ sign_bit) - sign_bit


def _float_to_uint(value: float, minimum: float, maximum: float, *, bits: int) -> int:
    if maximum <= minimum:
        raise ValueError("maximum must be greater than minimum")
    if not isfinite(value):
        raise ValueError("value must be finite")
    span = maximum - minimum
    scale = (1 << bits) - 1
    clamped = max(min(value, maximum), minimum)
    normalized = (clamped - minimum) / span
    return int(round(normalized * scale)) & scale


def _uint_to_float(value: int, minimum: float, maximum: float, *, bits: int) -> float:
    if maximum <= minimum:
        raise ValueError("maximum must be greater than minimum")
    scale = (1 << bits) - 1
    value &= scale
    span = maximum - minimum
    return (value / scale) * span + minimum


__all__ = [
    "Feedback",
    "FeedbackEngineering",
    "ManagementResponse",
    "frame_enable",
    "frame_disable",
    "frame_zero",
    "frame_speed",
    "frame_position_speed",
    "frame_mit",
    "is_enable_payload",
    "is_disable_payload",
    "is_zero_payload",
    "unpack_speed_payload",
    "unpack_position_speed_payload",
    "pack_mit_payload",
    "decode_mit",
    "decode_feedback",
    "build_filters",
    "frame_param_read",
    "frame_param_write",
    "frame_param_save",
    "frame_param_refresh",
    "parse_management_response",
    "MANAGEMENT_ARBITRATION_ID",
    "MIT_DEFAULT_POSITION_LIMIT",
    "MIT_DEFAULT_VELOCITY_LIMIT",
    "MIT_DEFAULT_TORQUE_LIMIT",
    "MIT_DEFAULT_KP_LIMIT",
    "MIT_DEFAULT_KD_LIMIT",
]
