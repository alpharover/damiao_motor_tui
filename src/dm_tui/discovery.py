"""Motor discovery workflows (placeholder implementation)."""

from __future__ import annotations

from dataclasses import dataclass
from time import monotonic
from typing import Iterable, List

from .bus_manager import BusManager
from .dmlib import protocol


@dataclass(slots=True)
class MotorInfo:
    esc_id: int
    mst_id: int
    last_seen: float


def passive_sniff(bus: BusManager, duration: float = 1.0) -> list[MotorInfo]:
    """Collect motors that are already chatting on the bus.

    The current scaffold listens for feedback frames using the protocol decoder
    but does not yet implement the full filtering/validation logic. It returns
    unique motors observed during *duration* seconds.
    """

    seen: dict[tuple[int, int], MotorInfo] = {}
    deadline = monotonic() + duration

    while monotonic() < deadline:
        message = bus.get_message(timeout=0.05)
        if message is None:
            continue
        try:
            feedback = protocol.decode_feedback(message.data)
        except ValueError:
            continue
        key = (feedback.esc_id, message.arbitration_id)
        seen[key] = MotorInfo(
            esc_id=feedback.esc_id,
            mst_id=message.arbitration_id,
            last_seen=monotonic(),
        )
    return list(seen.values())


def build_filters_for_mst_ids(mst_ids: Iterable[int]) -> list[dict[str, int]]:
    """Convenience wrapper around protocol filters for discovery."""

    return protocol.build_filters(mst_ids)
