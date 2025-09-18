from time import monotonic

import pytest
from rich.console import Console
from textual.message_pump import active_app

from dm_tui.app import (
    DmTuiApp,
    MetadataUpdate,
    MitCommand,
    MitModal,
    MotorControlPanel,
    MotorTable,
)
from dm_tui.discovery import MotorInfo
from dm_tui.dmlib import params
from dm_tui.dmlib.protocol import Feedback
from textual.widgets import Button


class _DummyApp:
    """Minimal stand-in providing the console attribute required by Textual widgets."""

    def __init__(self) -> None:
        self.console = Console()


class _StubInput:
    def __init__(self, value: str = "") -> None:
        self.value = value


class _StubButtonEvent:
    def __init__(self, button_id: str) -> None:
        self.button = type("_Btn", (), {"id": button_id})()


def test_motor_table_update_rows_handles_missing_records() -> None:
    """`MotorTable.update_rows` should tolerate absent records and default the name."""

    table = MotorTable()
    token = active_app.set(_DummyApp())
    try:
        table.add_columns("ESC", "MST", "Name", "Status", "Last Seen")
        table._row_keys = []
        table.update_rows({1: MotorInfo(1, 0x101, 0.0)}, {}, now=1.0)
        row = table.get_row("1")
    finally:
        active_app.reset(token)

    assert row[2] == "--"


def test_mit_modal_parses_user_values() -> None:
    token = active_app.set(_DummyApp())
    try:
        modal = MitModal(
            1,
            defaults=MitCommand(0.0, 0.0, 0.0, 0.0, 0.0),
            position_limit=2.0,
            velocity_limit=3.0,
            torque_limit=1.5,
            kp_limit=100.0,
            kd_limit=5.0,
        )
        modal._position_input = _StubInput("1.0")
        modal._velocity_input = _StubInput("-0.5")
        modal._torque_input = _StubInput("")
        modal._kp_input = _StubInput("50")
        modal._kd_input = _StubInput("2.5")
        captured: dict[str, MitCommand | None] = {}
        modal.dismiss = lambda value: captured.setdefault("result", value)
        modal.on_button_pressed(_StubButtonEvent("apply"))
    finally:
        active_app.reset(token)

    result = captured.get("result")
    assert isinstance(result, MitCommand)
    assert result.position_rad == 1.0
    assert result.velocity_rad_s == -0.5
    assert result.torque_nm == 0.0  # blank falls back to default
    assert result.kp == 50.0
    assert result.kd == 2.5


def test_ingest_feedback_fetches_rid_limits(monkeypatch, tmp_path) -> None:
    app = DmTuiApp(config_path=tmp_path / "config.yaml")
    app._bus_manager = object()
    app._ensure_telemetry_log = lambda: None

    refreshed: list[int] = []

    def fake_refresh(_bus, esc_id: int) -> None:
        refreshed.append(esc_id)

    values = {
        params.RID_P_MAX: 4.5,
        params.RID_V_MAX: 7.0,
        params.RID_T_MAX: 3.5,
    }
    read_calls: list[tuple[int, int]] = []

    def fake_read(_bus, esc_id: int, rid: int, timeout: float = 0.3) -> float:
        read_calls.append((esc_id, rid))
        return values[rid]

    monkeypatch.setattr("dm_tui.app.refresh_params", fake_refresh)
    monkeypatch.setattr("dm_tui.app.read_param_float", fake_read)

    feedback = Feedback(
        esc_id=0x01,
        status=0,
        position_raw=32767,
        velocity_raw=2047,
        torque_raw=2047,
        temp_mos=30,
        temp_rotor=32,
    )

    app._ingest_feedback(0x01, feedback, mst_id=0x101, timestamp=monotonic())

    record = app._motor_records[0x01]
    assert record.metadata["p_max"] == 4.5
    assert record.metadata["v_max"] == 7.0
    assert record.metadata["t_max"] == 3.5
    assert 0x01 in app._limits_loaded
    assert refreshed == [0x01]
    assert read_calls == [
        (0x01, params.RID_P_MAX),
        (0x01, params.RID_V_MAX),
        (0x01, params.RID_T_MAX),
    ]
    telemetry = app._telemetry[0x01]
    assert telemetry.position_rad == pytest.approx(4.5, rel=1e-3)
    assert telemetry.velocity_rad_s == pytest.approx(7.0, rel=1e-3)
    assert telemetry.torque_nm == pytest.approx(3.5, rel=1e-3)


def test_motor_control_panel_updates_and_disables() -> None:
    panel = MotorControlPanel()
    panel.update_controls(None, bus_online=False)
    assert all(button.disabled for button in panel._buttons.values())

    panel.update_controls(0x02, bus_online=True)
    assert not panel._buttons["control-enable"].disabled


def test_motor_control_panel_button_invokes_action() -> None:
    panel = MotorControlPanel()

    class _StubApp:
        def __init__(self) -> None:
            self.called: list[str] = []
            self.console = Console()
            self.raise_runtime = True

        def call_from_thread(self, func):
            if self.raise_runtime:
                self.raise_runtime = False
                raise RuntimeError("same thread")
            func()

        def action_enable_selected(self) -> None:
            self.called.append("enable")

    stub = _StubApp()
    token = active_app.set(stub)
    try:
        panel._app = stub  # type: ignore[attr-defined]
        panel.update_controls(0x01, bus_online=True)

        event = Button.Pressed(panel._buttons["control-enable"])
        panel.on_button_pressed(event)
    finally:
        active_app.reset(token)

    assert stub.called == ["enable"]


def test_metadata_update_refreshes_table(monkeypatch, tmp_path) -> None:
    """Metadata edits should push updates to the motor table immediately."""

    app = DmTuiApp(config_path=tmp_path / "config.yaml")
    app._mounted = True
    app._motors[0x01] = MotorInfo(0x01, 0x101, monotonic())

    call_order: list[str] = []

    def fake_group_refresh() -> None:
        call_order.append("group")

    def fake_detail_refresh() -> None:
        call_order.append("detail")

    def fake_persist() -> None:
        call_order.append("persist")

    def fake_table_refresh() -> None:
        call_order.append("refresh")

    app._refresh_group_panel = fake_group_refresh  # type: ignore[attr-defined]
    app._refresh_detail_panel = fake_detail_refresh  # type: ignore[attr-defined]
    app._persist_config = fake_persist  # type: ignore[attr-defined]
    app._refresh_motor_table = fake_table_refresh  # type: ignore[attr-defined]
    app._log = lambda _message: None

    app._apply_metadata_update(
        0x01,
        MetadataUpdate(name="Left", group="G1", p_max=None, v_max=None, t_max=None),
    )

    assert "refresh" in call_order
    assert call_order.index("refresh") > call_order.index("persist")


def test_watchdog_disables_stale_motor(monkeypatch, tmp_path) -> None:
    """Watchdog should disable stale motors and annotate state."""

    app = DmTuiApp(config_path=tmp_path / "config.yaml")
    bus = object()
    app._bus_manager = bus
    app._watchdog_threshold = 0.1
    app._watchdog_cooldown = 1.0
    now = monotonic()
    app._motors[0x01] = MotorInfo(0x01, 0x101, now - 1.0)

    calls: list[tuple[object, int]] = []

    def fake_disable(manager, esc_id):
        calls.append((manager, esc_id))

    monkeypatch.setattr("dm_tui.app.disable", fake_disable)

    messages: list[str] = []
    app._log = lambda message: messages.append(message)

    app._watchdog_check()

    assert calls == [(bus, 0x01)]
    assert any("Watchdog" in message for message in messages)
    assert 0x01 in app._watchdog_tripped


def test_watchdog_respects_cooldown(monkeypatch, tmp_path) -> None:
    """Watchdog should avoid spamming disable commands within the cooldown window."""

    app = DmTuiApp(config_path=tmp_path / "config.yaml")
    bus = object()
    app._bus_manager = bus
    app._watchdog_threshold = 0.1
    app._watchdog_cooldown = 5.0
    now = monotonic()
    app._motors[0x02] = MotorInfo(0x02, 0x102, now - 10.0)
    app._watchdog_last_disable[0x02] = now

    calls: list[tuple[object, int]] = []

    def fake_disable(manager, esc_id):
        calls.append((manager, esc_id))

    monkeypatch.setattr("dm_tui.app.disable", fake_disable)

    app._watchdog_check()

    assert calls == []
    assert 0x02 in app._watchdog_tripped


def test_get_commands_includes_motor_controls(tmp_path) -> None:
    app = DmTuiApp(config_path=tmp_path / "config.yaml")
    prompts = [command.prompt for command in app.get_commands()]
    assert "Enable Selected" in prompts
    assert "Disable Selected" in prompts
    assert "Zero Selected" in prompts
    assert "Set Velocity" in prompts
