"""Tests for telemetry logging helpers."""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from dm_tui import logging as telemetry_logging
from dm_tui.app import (
    DEFAULT_P_MAX,
    DEFAULT_T_MAX,
    DEFAULT_V_MAX,
    DmTuiApp,
)
from dm_tui.dmlib.protocol import Feedback


def _read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
        assert reader.fieldnames is not None
        return reader.fieldnames, rows


def test_open_csv_writes_header(tmp_path: Path) -> None:
    path = tmp_path / "telemetry.csv"
    row = telemetry_logging.TelemetryRow(
        timestamp=1.23,
        esc_id=1,
        mst_id=0x011,
        status=2,
        position_rad=3.21,
        velocity_rad_s=4.56,
        torque_nm=7.89,
        temp_mos_c=32.0,
        temp_rotor_c=30.0,
    )

    with telemetry_logging.open_csv(path) as writer:
        writer.write_row(row)

    headers, rows = _read_csv(path)
    assert headers == telemetry_logging.CSV_HEADERS
    assert len(rows) == 1
    parsed = rows[0]
    assert float(parsed["timestamp"]) == pytest.approx(row.timestamp)
    assert int(parsed["esc_id"]) == row.esc_id
    assert int(parsed["mst_id"]) == row.mst_id
    assert float(parsed["position_rad"]) == pytest.approx(row.position_rad)
    assert float(parsed["velocity_rad_s"]) == pytest.approx(row.velocity_rad_s)
    assert float(parsed["torque_nm"]) == pytest.approx(row.torque_nm)


def test_open_csv_appends_rows_without_duplicate_header(tmp_path: Path) -> None:
    path = tmp_path / "telemetry.csv"
    row1 = telemetry_logging.TelemetryRow(
        timestamp=0.0,
        esc_id=1,
        mst_id=0x011,
        status=0,
        position_rad=0.0,
        velocity_rad_s=0.0,
        torque_nm=0.0,
        temp_mos_c=20.0,
        temp_rotor_c=21.0,
    )
    row2 = telemetry_logging.TelemetryRow(
        timestamp=1.0,
        esc_id=2,
        mst_id=0x012,
        status=1,
        position_rad=1.0,
        velocity_rad_s=2.0,
        torque_nm=3.0,
        temp_mos_c=25.0,
        temp_rotor_c=26.0,
    )

    with telemetry_logging.open_csv(path) as writer:
        writer.write_row(row1)

    with telemetry_logging.open_csv(path) as writer:
        writer.write_row(row2)

    headers, rows = _read_csv(path)
    assert headers == telemetry_logging.CSV_HEADERS
    assert [int(row["esc_id"]) for row in rows] == [1, 2]


def test_dm_tui_app_writes_telemetry_log(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    app = DmTuiApp(config_path=config_path)
    feedback = Feedback(
        esc_id=3,
        status=1,
        position_raw=1000,
        velocity_raw=500,
        torque_raw=-250,
        temp_mos=40,
        temp_rotor=38,
    )
    timestamp = 12.345
    mst_id = 0x120

    app._ingest_feedback(feedback.esc_id, feedback, mst_id, timestamp)
    app._close_telemetry_log()

    log_path = tmp_path / "telemetry.csv"
    headers, rows = _read_csv(log_path)
    assert headers == telemetry_logging.CSV_HEADERS
    assert len(rows) == 1
    row = rows[0]
    assert int(row["esc_id"]) == feedback.esc_id
    assert int(row["mst_id"]) == mst_id
    assert float(row["timestamp"]) == pytest.approx(timestamp)

    engineering = feedback.to_engineering(
        p_max=DEFAULT_P_MAX,
        v_max=DEFAULT_V_MAX,
        t_max=DEFAULT_T_MAX,
    )
    assert float(row["position_rad"]) == pytest.approx(engineering.position_rad)
    assert float(row["velocity_rad_s"]) == pytest.approx(engineering.velocity_rad_s)
    assert float(row["torque_nm"]) == pytest.approx(engineering.torque_nm)

