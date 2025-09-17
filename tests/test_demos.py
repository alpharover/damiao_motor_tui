import math
import struct
import time

import pytest

from dm_tui.demos import brake_to_zero, sine_orchestra


class FakePeriodicTask:
    def __init__(self, arb_id: int, data: bytes, hz: float) -> None:
        self.arb_id = arb_id
        self.data = data
        self.hz = hz
        self.update_calls: list[bytes] = []
        self.stopped = False

    def update(self, *, data: bytes | None = None, period: float | None = None) -> None:
        if data is not None:
            self.data = data
            self.update_calls.append(data)
        if period is not None and period > 0:
            self.hz = 1.0 / period

    def stop(self) -> None:
        self.stopped = True


class FakeBus:
    def __init__(self) -> None:
        self.periodic_tasks: list[FakePeriodicTask] = []
        self.sent_frames: list[tuple[int, bytes, bool]] = []

    def send_periodic(self, arbitration_id: int, data: bytes, *, hz: float, extended_id: bool = False, duration=None):
        task = FakePeriodicTask(arbitration_id, data, hz)
        self.periodic_tasks.append(task)
        return task

    def send(self, arbitration_id: int, data: bytes, *, extended_id: bool = False) -> None:
        self.sent_frames.append((arbitration_id, data, extended_id))


def _extract_velocity(payload: bytes) -> float:
    return struct.unpack("<f", payload[:4])[0]


def test_sine_orchestra_schedules_periodic_updates() -> None:
    bus = FakeBus()
    handle = sine_orchestra(bus, [0x01, 0x02], amplitude_rps=5.0, frequency_hz=0.5)
    try:
        assert len(bus.periodic_tasks) == 2
        task_a, task_b = bus.periodic_tasks
        time.sleep(0.15)
        assert task_a.update_calls
        assert task_b.update_calls
        vel_a = _extract_velocity(task_a.update_calls[-1])
        vel_b = _extract_velocity(task_b.update_calls[-1])
        assert not math.isclose(vel_a, vel_b)
    finally:
        handle.stop()
    assert all(task.stopped for task in bus.periodic_tasks)
    update_counts = [len(task.update_calls) for task in bus.periodic_tasks]
    time.sleep(0.2)
    assert [len(task.update_calls) for task in bus.periodic_tasks] == update_counts
    assert handle.updater is not None and not handle.updater.is_alive()


def test_sine_orchestra_requires_non_empty_ids() -> None:
    bus = FakeBus()
    with pytest.raises(ValueError):
        sine_orchestra(bus, [], amplitude_rps=1.0, frequency_hz=0.5)


def test_brake_to_zero_sends_zero_velocity_frames() -> None:
    bus = FakeBus()
    brake_to_zero(bus, [0x03, 0x04])
    assert {frame[0] for frame in bus.sent_frames} == {0x203, 0x204}
    assert all(math.isclose(_extract_velocity(data), 0.0) for _, data, _ in bus.sent_frames)
