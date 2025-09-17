from rich.console import Console
from textual.message_pump import active_app

from dm_tui.app import MotorTable
from dm_tui.discovery import MotorInfo


class _DummyApp:
    """Minimal stand-in providing the console attribute required by Textual widgets."""

    def __init__(self) -> None:
        self.console = Console()


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
