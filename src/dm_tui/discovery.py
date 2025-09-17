"""Motor discovery workflows (placeholder implementation)."""

from __future__ import annotations

from dataclasses import dataclass
from time import monotonic, sleep
from typing import Iterable, List, Sequence

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


def active_probe(
    bus: BusManager,
    *,
    esc_candidates: Sequence[int] = tuple(range(1, 9)),
    probe_duration: float = 0.4,
) -> list[MotorInfo]:
    """Attempt to wake silent motors by issuing safe disable/zero commands.

    This implementation sends disable + zero-velocity frames and listens for
    feedback to identify responding motors. Commands remain neutral (0 rad/s)
    to avoid unexpected motion.
    """

    discovered: dict[tuple[int, int], MotorInfo] = {}
    end_time = monotonic() + probe_duration * len(esc_candidates)
    for esc_id in esc_candidates:
        bus.send(*protocol.frame_disable(esc_id))
        bus.send(*protocol.frame_speed(esc_id, 0.0))
        sleep(0.01)
        deadline = monotonic() + probe_duration
        while monotonic() < deadline:
            message = bus.get_message(timeout=0.05)
            if message is None:
                continue
            try:
                feedback = protocol.decode_feedback(message.data)
            except ValueError:
                continue
            if feedback.esc_id != esc_id:
                continue
            key = (feedback.esc_id, message.arbitration_id)
            discovered[key] = MotorInfo(
                esc_id=feedback.esc_id,
                mst_id=message.arbitration_id,
                last_seen=monotonic(),
            )
            break
    # Drain remaining buffered frames within allotted window.
    while monotonic() < end_time:
        message = bus.get_message(timeout=0.01)
        if message is None:
            break
        try:
            feedback = protocol.decode_feedback(message.data)
        except ValueError:
            continue
        key = (feedback.esc_id, message.arbitration_id)
        discovered[key] = MotorInfo(
            esc_id=feedback.esc_id,
            mst_id=message.arbitration_id,
            last_seen=monotonic(),
        )
    return list(discovered.values())
