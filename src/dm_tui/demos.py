"""Demo choreography scaffolding."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .bus_manager import BusManager


@dataclass(slots=True)
class DemoHandle:
    name: str

    def stop(self) -> None:
        """Placeholder stop method for future periodic tasks."""
        return None


def sine_orchestra(
    bus: BusManager,
    esc_ids: Iterable[int],
    *,
    amplitude_rps: float,
    frequency_hz: float,
) -> DemoHandle:
    """Stub entry point for sine orchestra demo (not yet implemented)."""

    raise NotImplementedError("sine_orchestra demo logic pending implementation")


def brake_to_zero(bus: BusManager, esc_ids: Iterable[int]) -> None:
    """Stub brake routine (implemented by high-level controllers later)."""

    raise NotImplementedError("brake_to_zero routine pending implementation")
