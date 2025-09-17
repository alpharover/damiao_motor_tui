"""High-level motor control helpers (scaffold)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .bus_manager import BusManager
from .dmlib import protocol


@dataclass(slots=True)
class MotorTarget:
    esc_id: int
    velocity_rad_s: float = 0.0


def enable_all(bus: BusManager, esc_ids: Iterable[int]) -> None:
    for esc_id in esc_ids:
        arb_id, data = protocol.frame_enable(esc_id)
        bus.send(arb_id, data)


def disable_all(bus: BusManager, esc_ids: Iterable[int]) -> None:
    for esc_id in esc_ids:
        arb_id, data = protocol.frame_disable(esc_id)
        bus.send(arb_id, data)


def command_velocities(bus: BusManager, targets: Iterable[MotorTarget]) -> None:
    for target in targets:
        arb_id, data = protocol.frame_speed(target.esc_id, target.velocity_rad_s)
        bus.send(arb_id, data)
