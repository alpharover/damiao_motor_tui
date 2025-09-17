"""Textual entry point for dm-tui."""

from __future__ import annotations

import threading
from datetime import datetime
from pathlib import Path
from time import monotonic
from typing import Dict, Iterable

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual.timer import Timer
from textual.widgets import DataTable, Footer, Header, Static, TextLog

from .bus_manager import BusManager, BusManagerError
from .controllers import disable_all
from .dmlib import protocol
from .discovery import MotorInfo, passive_sniff
from .persistence import AppConfig, MotorRecord, load_config, save_config
from .osutils import read_bus_statistics


class BusStatusPanel(Static):
    """Panel summarising SocketCAN interface state."""

    DEFAULT_CSS = """
    BusStatusPanel {
        padding: 1 1;
        border: round $accent;
        height: 12;
    }
    """

    def update_stats(self, channel: str, stats: Dict[str, object]) -> None:
        bitrate = stats.get("bitrate")
        oper_state = stats.get("oper_state") or stats.get("state") or "--"
        tx_packets = stats.get("tx_packets", "--")
        tx_errors = stats.get("tx_errors", "0")
        rx_packets = stats.get("rx_packets", "--")
        rx_errors = stats.get("rx_errors", "0")
        queue_len = stats.get("tx_queue_len", "--")
        text = "\n".join(
            [
                f"[b]Channel[/b]  {channel}",
                f"[b]State[/b]    {oper_state}",
                f"[b]Bitrate[/b]  {bitrate if bitrate else '--'}",
                f"[b]TX[/b]       {tx_packets} (err {tx_errors})",
                f"[b]RX[/b]       {rx_packets} (err {rx_errors})",
                f"[b]Queue[/b]    {queue_len}",
            ]
        )
        self.update(text)

    def update_error(self, channel: str, message: str) -> None:
        self.update(f"[b]Channel[/b]  {channel}\n[red]{message}[/red]")


class MotorTable(DataTable):
    """Motor summary table."""

    DEFAULT_CSS = """
    MotorTable {
        border: round $accent;
        height: 1fr;
    }
    """

    def on_mount(self) -> None:
        self.add_columns("ESC", "MST", "Name", "Status", "Last Seen")
        self.cursor_type = "row"
        self.show_cursor = False
        self.zebra_stripes = True

    def update_rows(
        self,
        motors: Dict[int, MotorInfo],
        records: Dict[int, MotorRecord],
        now: float,
    ) -> None:
        self.clear()
        esc_ids = sorted(set(records.keys()) | set(motors.keys()))
        for esc_id in esc_ids:
            record = records.get(esc_id)
            info = motors.get(esc_id)
            mst_id = info.mst_id if info else (record.mst_id if record else 0)
            last_seen = "--"
            status = "Configured"
            if info:
                delta = max(0.0, now - info.last_seen)
                last_seen = f"{delta:0.1f}s ago"
                status = "Active" if delta < 2.0 else "Quiet"
            name = record.name or "--" if record else "--"
            self.add_row(
                f"0x{esc_id:02X}",
                f"0x{mst_id:03X}",
                name,
                status,
                last_seen,
            )


class ActivityLog(TextLog):
    """Scrolling log widget."""

    DEFAULT_CSS = """
    ActivityLog {
        border: round $accent;
        height: 1fr;
    }
    """

    def on_mount(self) -> None:
        self.border_title = "Activity Log"
        self.auto_scroll = True


class HintPanel(Static):
    """Key binding hint box."""

    DEFAULT_CSS = """
    HintPanel {
        border: round $accent;
        height: 8;
        padding: 1 1;
    }
    """

    def update_hints(self, bus: str) -> None:
        self.update(
            "\n".join(
                [
                    f"[b]Active Bus[/b]  {bus}",
                    "",
                    "[b]Key Bindings[/b]",
                    "Space  E-STOP",
                    "R      Re-scan",
                    "B      Cycle Bus",
                    "Ctrl+S Save Config",
                ]
            )
        )


class DmTuiApp(App[None]):
    """dm-tui Textual application shell."""

    CSS = """
    #content {
        height: 1fr;
    }

    #left-column,
    #right-column {
        height: 1fr;
    }

    #left-column {
        width: 2fr;
        gap: 1;
    }

    #right-column {
        width: 1fr;
        gap: 1;
    }
    """

    BINDINGS = [
        Binding("space", "estop", "E-STOP", show=True),
        Binding("r", "trigger_discovery", "Re-scan", show=True),
        Binding("b", "cycle_bus", "Cycle Bus", show=True),
        Binding("ctrl+s", "save_config", "Save config", show=False),
        Binding("ctrl+c", "quit", "Quit", show=False),
    ]

    active_bus = reactive("canB")

    def __init__(self, config_path: Path | None = None) -> None:
        super().__init__()
        self._config_path = config_path
        self._config: AppConfig = load_config(config_path)
        self.active_bus = self._config.active_bus
        self._motor_records: Dict[int, MotorRecord] = {
            record.esc_id: record for record in self._config.motors
        }
        self._motors: Dict[int, MotorInfo] = {}
        self._bus_manager: BusManager | None = None
        self._bus_stats_timer: Timer | None = None
        self._discovery_timer: Timer | None = None
        self._discovery_running = False
        self._bus_stats_running = False
        self._threads_lock = threading.Lock()
        self._mounted = False

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="content"):
            with Vertical(id="left-column"):
                yield BusStatusPanel(id="bus-status")
                yield MotorTable(id="motor-table")
            with Vertical(id="right-column"):
                yield ActivityLog(id="activity-log")
                yield HintPanel(id="hint-panel")
        yield Footer()

    def on_mount(self) -> None:
        self._mounted = True
        self._refresh_hint_panel()
        self._refresh_motor_table()
        self._open_bus(self.active_bus)
        self._bus_stats_timer = self.set_interval(3.0, self._schedule_bus_stats_refresh)
        self._discovery_timer = self.set_interval(4.0, self._schedule_discovery)

    def on_unmount(self) -> None:
        if self._bus_stats_timer:
            self._bus_stats_timer.stop()
        if self._discovery_timer:
            self._discovery_timer.stop()
        self._close_bus()
        self._mounted = False

    def watch_active_bus(self, active_bus: str) -> None:
        if not self._mounted:
            return
        self._config.active_bus = active_bus
        self._refresh_hint_panel()
        self._open_bus(active_bus)
        self._schedule_bus_stats_refresh()

    def action_estop(self) -> None:
        esc_ids = sorted(set(self._motor_records.keys()) | set(self._motors.keys()))
        if not esc_ids:
            self._log("No motors recorded for E-STOP.")
            return
        if not self._bus_manager:
            self._log("[red]E-STOP ignored; bus offline.[/red]")
            return
        try:
            disable_all(self._bus_manager, esc_ids)
        except BusManagerError as exc:  # pragma: no cover - depends on hardware
            self._log(f"[red]Failed to broadcast disable:[/red] {exc}")
        else:
            self._log("Issued disable frame to all recorded ESC IDs.")

    def action_trigger_discovery(self) -> None:
        self._log("Discovery requested.")
        self._schedule_discovery()

    def action_cycle_bus(self) -> None:
        buses = self._config.buses
        channels = [bus.channel for bus in buses]
        if not channels:
            self._log("[red]No buses configured.[/red]")
            return
        if self.active_bus not in channels:
            self.active_bus = channels[0]
            return
        index = channels.index(self.active_bus)
        self.active_bus = channels[(index + 1) % len(channels)]
        self._log(f"Switched active bus to {self.active_bus}.")

    def action_save_config(self) -> None:
        self._persist_config()
        self._log("Configuration saved.")

    def _schedule_discovery(self) -> None:
        if not self._mounted or self._bus_manager is None:
            return
        with self._threads_lock:
            if self._discovery_running:
                return
            self._discovery_running = True
        thread = threading.Thread(target=self._discovery_worker, daemon=True)
        thread.start()

    def _schedule_bus_stats_refresh(self) -> None:
        if not self._mounted:
            return
        with self._threads_lock:
            if self._bus_stats_running:
                return
            self._bus_stats_running = True
        thread = threading.Thread(target=self._bus_stats_worker, daemon=True)
        thread.start()

    def _discovery_worker(self) -> None:
        try:
            bus = self._bus_manager
            if bus is None:
                return
            motors = passive_sniff(bus, duration=0.5)
        except Exception as exc:  # pragma: no cover - hardware dependent
            self.call_from_thread(
                self._log,
                f"[red]Discovery error:[/red] {exc}",
            )
        else:
            if motors:
                self.call_from_thread(self._ingest_discovery, motors)
        finally:
            with self._threads_lock:
                self._discovery_running = False

    def _bus_stats_worker(self) -> None:
        try:
            stats = read_bus_statistics(self.active_bus)
        except Exception as exc:  # pragma: no cover - depends on OS tools
            self.call_from_thread(self._update_bus_error, str(exc))
        else:
            self.call_from_thread(self._update_bus_stats, stats)
        finally:
            with self._threads_lock:
                self._bus_stats_running = False

    def _ingest_discovery(self, motors: Iterable[MotorInfo]) -> None:
        now = monotonic()
        config_changed = False
        for motor in motors:
            existing = self._motors.get(motor.esc_id)
            if existing is None or motor.mst_id != existing.mst_id:
                self._motors[motor.esc_id] = MotorInfo(
                    esc_id=motor.esc_id,
                    mst_id=motor.mst_id,
                    last_seen=now,
                )
            else:
                self._motors[motor.esc_id] = MotorInfo(
                    esc_id=existing.esc_id,
                    mst_id=existing.mst_id,
                    last_seen=now,
                )
            record = self._motor_records.get(motor.esc_id)
            if record is None:
                record = MotorRecord(esc_id=motor.esc_id, mst_id=motor.mst_id)
                self._motor_records[motor.esc_id] = record
                self._config.motors.append(record)
                config_changed = True
                self._log(
                    f"Discovered ESC 0x{motor.esc_id:02X} (MST 0x{motor.mst_id:03X})."
                )
            elif record.mst_id != motor.mst_id:
                record.mst_id = motor.mst_id
                config_changed = True
        self._refresh_motor_table()
        if config_changed:
            self._persist_config()

    def _update_bus_stats(self, stats: Dict[str, object]) -> None:
        panel = self.query_one(BusStatusPanel)
        panel.update_stats(self.active_bus, stats)

    def _update_bus_error(self, message: str) -> None:
        panel = self.query_one(BusStatusPanel)
        panel.update_error(self.active_bus, message)

    def _refresh_motor_table(self) -> None:
        table = self.query_one(MotorTable)
        table.update_rows(self._motors, self._motor_records, monotonic())

    def _refresh_hint_panel(self) -> None:
        panel = self.query_one(HintPanel)
        panel.update_hints(self.active_bus)

    def _open_bus(self, channel: str) -> None:
        self._close_bus()
        try:
            manager = BusManager(channel=channel)
            manager.open()
        except BusManagerError as exc:
            self._bus_manager = None
            self._update_bus_error(str(exc))
            self._log(f"[red]Bus {channel} unavailable:[/red] {exc}")
            return
        self._bus_manager = manager
        mst_ids = [record.mst_id for record in self._motor_records.values() if record.mst_id]
        if mst_ids:
            try:
                manager.set_filters(protocol.build_filters(mst_ids))
            except BusManagerError as exc:  # pragma: no cover - depends on hardware
                self._log(f"[yellow]Warning:[/yellow] failed to apply filters: {exc}")
        self._log(f"Connected to {channel}.")
        self._update_bus_stats({"state": "Initializing"})

    def _close_bus(self) -> None:
        if self._bus_manager is not None:
            self._bus_manager.close()
            self._bus_manager = None

    def _persist_config(self) -> None:
        self._config.motors = list(self._motor_records.values())
        self._config.active_bus = self.active_bus
        save_config(self._config, self._config_path)

    def _log(self, message: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        text = f"[{timestamp}] {message}"
        try:
            log = self.query_one(ActivityLog)
        except LookupError:
            pass
        else:
            log.write_line(text)
        self.console.log(message)


def run(config_path: Path | None = None) -> None:
    """Convenience shim to launch the Textual app."""

    DmTuiApp(config_path=config_path).run()


if __name__ == "__main__":
    run()
