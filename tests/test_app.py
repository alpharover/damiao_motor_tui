from rich.console import Console
from textual.message_pump import active_app

from time import monotonic

from dm_tui.app import DmTuiApp, MotorTable
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
