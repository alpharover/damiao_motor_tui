"""High-level motor control helpers (scaffold)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

from .bus_manager import BusManager
from .dmlib import protocol
from .dmlib.params import RID_CTRL_MODE, RID_ESC_ID, RID_MST_ID


@dataclass(slots=True)
class MotorTarget:
    esc_id: int
    velocity_rad_s: float = 0.0


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


def write_param(bus: BusManager, esc_id: int, rid: int, value: int) -> None:
    arb_id, data = protocol.frame_param_write(esc_id, rid, value)
    bus.send(arb_id, data)


def save_params(bus: BusManager, esc_id: int) -> None:
    arb_id, data = protocol.frame_param_save(esc_id)
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
