from rich.console import Console
from textual.message_pump import active_app
from textual.widgets import Button

from dm_tui.app import MitCommand, MitModal, MotorTable
from dm_tui.discovery import MotorInfo


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
