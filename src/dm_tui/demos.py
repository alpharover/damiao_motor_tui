"""Demo choreography scaffolding."""

from __future__ import annotations

import math
import threading
from dataclasses import dataclass
from time import monotonic
from typing import Callable, Iterable, List, Sequence

from .bus_manager import BusManager
from .controllers import MotorTarget, command_velocities
from .dmlib import protocol

_UPDATE_INTERVAL = 0.05


@dataclass(slots=True)
class DemoHandle:
    """Handle returned from demo helpers."""

    name: str
    tasks: Sequence[object]
    updater: threading.Thread | None
    _stopper: Callable[[], None]

    def stop(self) -> None:
        """Stop any periodic tasks associated with the demo."""
        self._stopper()


def _compute_velocity(mode: str, amplitude: float, base_phase: float, index: int, count: int) -> float:
    if count <= 1:
        return amplitude * math.sin(base_phase)
    if mode == "sine":
        phase = base_phase + (2 * math.pi * index / count)
    elif mode == "antiphase":
        phase = base_phase + (math.pi if index % 2 else 0)
    elif mode == "figure8":
        phase = base_phase + (index * math.pi / 2)
    else:
        phase = base_phase
    return amplitude * math.sin(phase)


def sine_orchestra(
    bus: BusManager,
    esc_ids: Iterable[int],
    *,
    amplitude_rps: float,
    frequency_hz: float,
    mode: str = "sine",
) -> DemoHandle:
    """Schedule a sine-based orchestra demo across the supplied motors."""

    esc_list: List[int] = [esc for esc in esc_ids]
    if not esc_list:
        raise ValueError("sine_orchestra requires at least one ESC ID")

    period_hz = max(20.0, frequency_hz * 16.0)
    tasks = []
    count = len(esc_list)
    base_phase = 0.0
    for index, esc in enumerate(esc_list):
        velocity = _compute_velocity(mode, amplitude_rps, base_phase, index, count)
        arb_id, payload = protocol.frame_speed(esc, velocity)
        task = bus.send_periodic(arb_id, payload, hz=period_hz)
        tasks.append(task)

    stop_event = threading.Event()
    start_time = monotonic()

    def _update_loop() -> None:
        while not stop_event.wait(_UPDATE_INTERVAL):
            now = monotonic()
            phase_base = 2 * math.pi * frequency_hz * (now - start_time)
            for index, esc in enumerate(esc_list):
                velocity = _compute_velocity(mode, amplitude_rps, phase_base, index, count)
                _, payload = protocol.frame_speed(esc, velocity)
                try:
                    tasks[index].update(data=payload)
                except Exception:
                    # Ensure the loop keeps running even if a single update fails.
                    continue

    updater = threading.Thread(
        target=_update_loop,
        name="dm-tui-sine-orchestra",
        daemon=True,
    )
    updater.start()

    def _stopper() -> None:
        stop_event.set()
        if updater.is_alive():
            updater.join(timeout=_UPDATE_INTERVAL * 4)
        for task in tasks:
            try:
                task.stop()
            except Exception:
                continue

    return DemoHandle(
        name="sine_orchestra",
        tasks=tuple(tasks),
        updater=updater,
        _stopper=_stopper,
    )


def brake_to_zero(bus: BusManager, esc_ids: Iterable[int]) -> None:
    """Broadcast zero velocity commands to the provided ESC IDs."""

    targets = [MotorTarget(esc_id=esc_id, velocity_rad_s=0.0) for esc_id in esc_ids]
    if targets:
        command_velocities(bus, targets)
