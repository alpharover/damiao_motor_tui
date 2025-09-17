"""High-level motor control helpers (scaffold)."""

from __future__ import annotations

from dataclasses import dataclass
from struct import unpack
from time import monotonic
from typing import Iterable

from .bus_manager import BusManager
from .bus_manager import BusManagerError
from .dmlib import protocol, params
from .dmlib.params import RID_CTRL_MODE, RID_ESC_ID, RID_MST_ID


@dataclass(slots=True)
class MotorTarget:
    esc_id: int
    velocity_rad_s: float = 0.0


@dataclass(slots=True)
class MitTarget:
    esc_id: int
    position_rad: float = 0.0
    velocity_rad_s: float = 0.0
    torque_nm: float = 0.0
    kp: float = 0.0
    kd: float = 0.0
    position_limit: float = protocol.MIT_DEFAULT_POSITION_LIMIT
    velocity_limit: float = protocol.MIT_DEFAULT_VELOCITY_LIMIT
    torque_limit: float = protocol.MIT_DEFAULT_TORQUE_LIMIT
    kp_limit: float = protocol.MIT_DEFAULT_KP_LIMIT
    kd_limit: float = protocol.MIT_DEFAULT_KD_LIMIT


def enable(bus: BusManager, esc_id: int) -> None:
    arb_id, data = protocol.frame_enable(esc_id)
    bus.send(arb_id, data)


def disable(bus: BusManager, esc_id: int) -> None:
    arb_id, data = protocol.frame_disable(esc_id)
    bus.send(arb_id, data)


def zero(bus: BusManager, esc_id: int) -> None:
    arb_id, data = protocol.frame_zero(esc_id)
    bus.send(arb_id, data)


def enable_all(bus: BusManager, esc_ids: Iterable[int]) -> None:
    for esc_id in esc_ids:
        enable(bus, esc_id)


def disable_all(bus: BusManager, esc_ids: Iterable[int]) -> None:
    for esc_id in esc_ids:
        disable(bus, esc_id)


def command_velocities(bus: BusManager, targets: Iterable[MotorTarget]) -> None:
    for target in targets:
        arb_id, data = protocol.frame_speed(target.esc_id, target.velocity_rad_s)
        bus.send(arb_id, data)


def command_velocity(bus: BusManager, esc_id: int, velocity_rad_s: float) -> None:
    command_velocities(bus, [MotorTarget(esc_id=esc_id, velocity_rad_s=velocity_rad_s)])


def command_mit(
    bus: BusManager,
    esc_id: int,
    *,
    position_rad: float,
    velocity_rad_s: float,
    torque_nm: float,
    kp: float,
    kd: float,
    position_limit: float = protocol.MIT_DEFAULT_POSITION_LIMIT,
    velocity_limit: float = protocol.MIT_DEFAULT_VELOCITY_LIMIT,
    torque_limit: float = protocol.MIT_DEFAULT_TORQUE_LIMIT,
    kp_limit: float = protocol.MIT_DEFAULT_KP_LIMIT,
    kd_limit: float = protocol.MIT_DEFAULT_KD_LIMIT,
) -> None:
    command_mit_targets(
        bus,
        [
            MitTarget(
                esc_id=esc_id,
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
        ],
    )


def command_mit_targets(bus: BusManager, targets: Iterable[MitTarget]) -> None:
    for target in targets:
        arb_id, data = protocol.frame_mit(
            target.esc_id,
            position_rad=target.position_rad,
            velocity_rad_s=target.velocity_rad_s,
            torque_nm=target.torque_nm,
            kp=target.kp,
            kd=target.kd,
            position_limit=target.position_limit,
            velocity_limit=target.velocity_limit,
            torque_limit=target.torque_limit,
            kp_limit=target.kp_limit,
            kd_limit=target.kd_limit,
        )
        bus.send(arb_id, data)


def read_param(bus: BusManager, esc_id: int, rid: int, *, timeout: float = 0.5) -> int:
    """Read *rid* from *esc_id*, returning the raw 32-bit value."""

    arb_id, payload = protocol.frame_param_read(esc_id, rid)
    bus.send(arb_id, payload)
    deadline = monotonic() + max(timeout, 0.0)
    while True:
        remaining = deadline - monotonic()
        if remaining <= 0:
            break
        message = bus.get_message(timeout=min(0.05, remaining))
        if message is None:
            continue
        if message.arbitration_id != protocol.MANAGEMENT_ARBITRATION_ID:
            continue
        try:
            response = protocol.parse_management_response(message.data)
        except ValueError:
            continue
        if response.command != params.MANAGEMENT_READ:
            continue
        if response.esc_id != esc_id:
            continue
        if response.rid != rid:
            continue
        return response.value
    raise BusManagerError(
        f"Timed out waiting for RID 0x{rid:02X} from ESC 0x{esc_id:02X}"
    )


def read_param_float(bus: BusManager, esc_id: int, rid: int, *, timeout: float = 0.5) -> float:
    """Read *rid* from *esc_id* and interpret the response as a little-endian float."""

    value = read_param(bus, esc_id, rid, timeout=timeout)
    data = value.to_bytes(4, "little", signed=False)
    return float(unpack("<f", data)[0])


def write_param(bus: BusManager, esc_id: int, rid: int, value: int) -> None:
    arb_id, data = protocol.frame_param_write(esc_id, rid, value)
    bus.send(arb_id, data)


def save_params(bus: BusManager, esc_id: int) -> None:
    arb_id, data = protocol.frame_param_save(esc_id)
    bus.send(arb_id, data)


def refresh_params(bus: BusManager, esc_id: int) -> None:
    arb_id, data = protocol.frame_param_refresh(esc_id)
    bus.send(arb_id, data)


def assign_motor_ids(
    bus: BusManager,
    *,
    current_esc: int,
    new_esc: int,
    new_mst: int,
    control_mode: int,
) -> None:
    disable(bus, current_esc)
    write_param(bus, current_esc, RID_ESC_ID, new_esc)
    write_param(bus, current_esc, RID_MST_ID, new_mst)
    write_param(bus, current_esc, RID_CTRL_MODE, control_mode)
    save_params(bus, current_esc)
