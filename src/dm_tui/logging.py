"""Telemetry logging utilities."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, TextIO

from .dmlib.protocol import Feedback, FeedbackEngineering

CSV_HEADERS = [
    "timestamp",
    "esc_id",
    "mst_id",
    "status",
    "position_rad",
    "velocity_rad_s",
    "torque_nm",
    "temp_mos_c",
    "temp_rotor_c",
]


@dataclass(slots=True)
class TelemetryRow:
    """Row written to the telemetry CSV log."""

    timestamp: float
    esc_id: int
    mst_id: int
    status: int
    position_rad: float
    velocity_rad_s: float
    torque_nm: float
    temp_mos_c: float
    temp_rotor_c: float

    def as_sequence(self) -> list[float | int]:
        """Return the row as a list suitable for csv.writer."""

        return [
            self.timestamp,
            self.esc_id,
            self.mst_id,
            self.status,
            self.position_rad,
            self.velocity_rad_s,
            self.torque_nm,
            self.temp_mos_c,
            self.temp_rotor_c,
        ]


class TelemetryCsvWriter:
    """Context manager that appends telemetry rows to a CSV file."""

    __slots__ = ("_handle", "_writer", "path")

    def __init__(self, handle: TextIO, path: Path) -> None:
        self._handle = handle
        self._writer = csv.writer(handle)
        self.path = path

    def __enter__(self) -> "TelemetryCsvWriter":  # pragma: no cover - exercised in tests
        return self

    def __exit__(self, *_exc_info) -> None:  # pragma: no cover - exercised in tests
        self.close()

    def write_header(self) -> None:
        """Write the CSV header row."""

        self._writer.writerow(CSV_HEADERS)
        self._handle.flush()

    def write_row(self, row: TelemetryRow) -> None:
        """Append a single telemetry *row*."""

        self._writer.writerow(row.as_sequence())
        self._handle.flush()

    def write_rows(self, rows: Iterable[TelemetryRow]) -> None:
        """Append multiple telemetry *rows*."""

        for row in rows:
            self.write_row(row)

    def close(self) -> None:
        """Close the underlying file handle."""

        if not self._handle.closed:
            self._handle.flush()
            self._handle.close()


def open_csv(path: Path) -> TelemetryCsvWriter:
    """Open *path* for appending telemetry rows, creating headers if needed."""

    path.parent.mkdir(parents=True, exist_ok=True)
    need_header = not path.exists() or path.stat().st_size == 0
    handle = path.open("a", encoding="utf-8", newline="")
    writer = TelemetryCsvWriter(handle, path)
    if need_header:
        writer.write_header()
    return writer


def telemetry_row_from_engineering(
    engineering: FeedbackEngineering,
    *,
    mst_id: int,
    timestamp: float,
) -> TelemetryRow:
    """Create a :class:`TelemetryRow` from engineering feedback values."""

    return TelemetryRow(
        timestamp=timestamp,
        esc_id=engineering.esc_id,
        mst_id=mst_id,
        status=engineering.status,
        position_rad=engineering.position_rad,
        velocity_rad_s=engineering.velocity_rad_s,
        torque_nm=engineering.torque_nm,
        temp_mos_c=engineering.temp_mos_c,
        temp_rotor_c=engineering.temp_rotor_c,
    )


def telemetry_row_from_feedback(
    feedback: Feedback,
    *,
    mst_id: int,
    timestamp: float,
    p_max: float,
    v_max: float,
    t_max: float,
) -> TelemetryRow:
    """Create a :class:`TelemetryRow` from raw :class:`~dm_tui.dmlib.protocol.Feedback`."""

    engineering = feedback.to_engineering(p_max=p_max, v_max=v_max, t_max=t_max)
    return telemetry_row_from_engineering(engineering, mst_id=mst_id, timestamp=timestamp)


__all__ = [
    "CSV_HEADERS",
    "TelemetryCsvWriter",
    "TelemetryRow",
    "open_csv",
    "telemetry_row_from_engineering",
    "telemetry_row_from_feedback",
]
