"""Textual entry point for dm-tui."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from time import monotonic
from typing import Dict, Iterable, Optional

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.events import Mount
from textual.message import Message
from textual.reactive import reactive
from textual.screen import ModalScreen
from textual.timer import Timer
from textual.widgets import Button, DataTable, Footer, Header, Input, Label, Static, TextLog

from .bus_manager import BusManager, BusManagerError
from .controllers import command_velocity, disable, disable_all, enable, zero
from .dmlib import protocol
from .dmlib.protocol import Feedback
from .discovery import MotorInfo, active_probe, passive_sniff
from .persistence import AppConfig, MotorRecord, ensure_bus, load_config, save_config
from .osutils import read_bus_statistics

DEFAULT_P_MAX = 12.0
DEFAULT_V_MAX = 30.0
DEFAULT_T_MAX = 20.0


@dataclass(slots=True)
class TelemetryRecord:
    feedback: Feedback
    timestamp: float
    position_rad: float
    velocity_rad_s: float
    torque_nm: float


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

    def on_mount(self) -> None:  # noqa: D401
        self.add_columns("ESC", "MST", "Name", "Status", "Last Seen")
        self.cursor_type = "row"
        self.show_cursor = True
        self.zebra_stripes = True
        self._row_keys: list[str] = []

    def update_rows(
        self,
        motors: Dict[int, MotorInfo],
        records: Dict[int, MotorRecord],
        now: float,
    ) -> None:
        self.clear()
        self._row_keys.clear()
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
            row_key = str(esc_id)
            self.add_row(
                f"0x{esc_id:02X}",
                f"0x{mst_id:03X}",
                name,
                status,
                last_seen,
                key=row_key,
            )
            self._row_keys.append(row_key)

    def focus_esc(self, esc_id: int) -> None:
        key = str(esc_id)
        try:
            row_index = self.get_row_index(key)
        except KeyError:
            return
        self.cursor_coordinate = (row_index, 0)

    def available_esc_ids(self) -> list[int]:
        return [int(key) for key in self._row_keys]


class ActivityLog(TextLog):
    """Scrolling log widget."""

    DEFAULT_CSS = """
    ActivityLog {
        border: round $accent;
        height: 1fr;
    }
    """

    def on_mount(self) -> None:  # noqa: D401
        self.border_title = "Activity Log"
        self.auto_scroll = True


class HintPanel(Static):
    """Key binding hint box."""

    DEFAULT_CSS = """
    HintPanel {
        border: round $accent;
        height: 10;
        padding: 1 1;
    }
    """

    def update_hints(self, bus: str, selected: int | None) -> None:
        self.update(
            "\n".join(
                [
                    f"[b]Active Bus[/b]  {bus}",
                    f"[b]Selected[/b]  0x{selected:02X}" if selected is not None else "[b]Selected[/b]  --",
                    "",
                    "[b]Key Bindings[/b]",
                    "Space  E-STOP",
                    "R      Re-scan",
                    "B      Cycle Bus",
                    "E/D/Z Enable/Disable/Zero",
                    "V      Set Velocity",
                    "Ctrl+S Save Config",
                ]
            )
        )


class MotorDetailPanel(Static):
    """Detailed view for the currently highlighted motor."""

    DEFAULT_CSS = """
    MotorDetailPanel {
        border: round $accent;
        height: 14;
        padding: 1 1;
    }
    """

    def show_idle(self) -> None:
        self.update("Select a motor row to view details.")

    def show_details(
        self,
        *,
        esc_id: int,
        record: MotorRecord | None,
        info: MotorInfo | None,
        telemetry: TelemetryRecord | None,
        now: float,
    ) -> None:
        if telemetry:
            delta = now - telemetry.timestamp
            temps = f"MOS {telemetry.feedback.temp_mos}°C | Rotor {telemetry.feedback.temp_rotor}°C"
            velocity = f"{telemetry.velocity_rad_s:0.2f} rad/s"
            torque = f"{telemetry.torque_nm:0.2f} Nm"
        else:
            delta = None
            temps = "--"
            velocity = "--"
            torque = "--"
        mst_id = info.mst_id if info else (record.mst_id if record else 0)
        last_seen = "--"
        if info:
            last_seen = f"{max(0.0, now - info.last_seen):0.1f}s ago"
        name = record.name if record and record.name else "--"
        lines = [
            f"[b]ESC[/b] 0x{esc_id:02X} | [b]MST[/b] 0x{mst_id:03X}",
            f"[b]Name[/b] {name}",
            f"[b]Last Seen[/b] {last_seen}",
            f"[b]Temps[/b] {temps}",
            f"[b]Velocity[/b] {velocity}",
            f"[b]Torque[/b] {torque}",
        ]
        if delta is not None:
            status = "Fresh" if delta < 1.0 else "Stale"
            lines.append(f"[b]Telemetry[/b] {status} ({delta:0.1f}s old)")
        self.update("\n".join(lines))


class TelemetryPanel(Static):
    """Compact overview showing recent telemetry across motors."""

    DEFAULT_CSS = """
    TelemetryPanel {
        border: round $accent;
        height: 11;
        padding: 1 1;
    }
    """

    def update_rows(self, telemetry: Dict[int, TelemetryRecord], now: float) -> None:
        if not telemetry:
            self.update("Telemetry will appear once feedback frames arrive.")
            return
        lines = ["[b]Live Telemetry[/b]"]
        for esc_id in sorted(telemetry)[:6]:
            record = telemetry[esc_id]
            age = now - record.timestamp
            lines.append(
                "  ".join(
                    [
                        f"0x{esc_id:02X}",
                        f"θ={record.position_rad:0.2f} rad",
                        f"ω={record.velocity_rad_s:0.2f} rad/s",
                        f"τ={record.torque_nm:0.2f} Nm",
                        f"age={age:0.1f}s",
                    ]
                )
            )
        self.update("\n".join(lines))


class VelocityModal(ModalScreen[Optional[float]]):
    """Modal dialog requesting a velocity setpoint."""

    def __init__(self, esc_id: int, default: float | None = None) -> None:
        super().__init__()
        self._esc_id = esc_id
        self._default = default
        self._error: Label | None = None
        self._input: Input | None = None

    def compose(self) -> ComposeResult:
        yield Static(f"Set velocity for ESC 0x{self._esc_id:02X}", id="vel-title")
        default_text = "" if self._default is None else f"{self._default:0.2f}"
        self._input = Input(default_text, placeholder="rad/s", id="vel-input")
        yield self._input
        self._error = Label("", id="vel-error")
        yield self._error
        with Horizontal(id="vel-buttons"):
            yield Button("Cancel", id="cancel")
            yield Button("Apply", id="apply", variant="primary")

    def on_mount(self, event: Mount) -> None:  # noqa: D401
        if self._input:
            self.set_focus(self._input)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.dismiss(None)
            return
        if self._input is None:
            self.dismiss(None)
            return
        try:
            value = float(self._input.value.strip())
        except ValueError:
            if self._error:
                self._error.update("Enter a numeric rad/s value.")
            return
        self.dismiss(value)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        try:
            apply_button = self.query_one("#apply", Button)
        except LookupError:
            self.dismiss(None)
            return
        self.on_button_pressed(Button.Pressed(apply_button))


class DmTuiApp(App[None]):
    """dm-tui Textual application shell."""

    CSS = """
    #content {
        height: 1fr;
        gap: 1;
    }

    #left-column,
    #right-column {
        height: 1fr;
        gap: 1;
    }

    #left-column {
        width: 2fr;
    }

    #right-column {
        width: 1fr;
    }
    """

    BINDINGS = [
        Binding("space", "estop", "E-STOP", show=True),
        Binding("r", "trigger_discovery", "Re-scan", show=True),
        Binding("b", "cycle_bus", "Cycle Bus", show=True),
        Binding("e", "enable_selected", "Enable", show=True),
        Binding("d", "disable_selected", "Disable", show=True),
        Binding("z", "zero_selected", "Zero", show=True),
        Binding("v", "set_velocity", "Velocity", show=True),
        Binding("ctrl+s", "save_config", "Save config", show=False),
        Binding("ctrl+c", "quit", "Quit", show=False),
    ]

    active_bus = reactive("canB")
    selected_esc = reactive(None)

    def __init__(self, config_path: Path | None = None) -> None:
        super().__init__()
        self._config_path = config_path
        self._config: AppConfig = load_config(config_path)
        self.active_bus = self._config.active_bus
        ensure_bus(self._config, self.active_bus, make_active=True)
        self._motor_records: Dict[int, MotorRecord] = {
            record.esc_id: record for record in self._config.motors
        }
        self._motors: Dict[int, MotorInfo] = {}
        self._telemetry: Dict[int, TelemetryRecord] = {}
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
                yield TelemetryPanel(id="telemetry-panel")
            with Vertical(id="right-column"):
                yield MotorDetailPanel(id="motor-detail")
                yield ActivityLog(id="activity-log")
                yield HintPanel(id="hint-panel")
        yield Footer()

    def on_mount(self) -> None:
        self._mounted = True
        self._refresh_hint_panel()
        self._refresh_motor_table()
        self._refresh_detail_panel()
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

    def watch_selected_esc(self, selected_esc: Optional[int]) -> None:
        if not self._mounted:
            return
        self._refresh_hint_panel()
        self._refresh_detail_panel()

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
        except BusManagerError as exc:  # pragma: no cover - hardware dependent
            self._log(f"[red]Failed to broadcast disable:[/red] {exc}")
        else:
            self._log("Issued disable frame to all recorded ESC IDs.")

    def action_trigger_discovery(self) -> None:
        self._log("Discovery requested.")
        self._schedule_discovery(force_active=True)

    def action_cycle_bus(self) -> None:
        channels = [bus.channel for bus in self._config.buses]
        if not channels:
            self._log("[red]No buses configured.[/red]")
            return
        if self.active_bus not in channels:
            self.active_bus = channels[0]
            return
        index = channels.index(self.active_bus)
        self.active_bus = channels[(index + 1) % len(channels)]
        self._log(f"Switched active bus to {self.active_bus}.")

    def action_enable_selected(self) -> None:
        esc_id = self._require_selected_motor()
        if esc_id is None or not self._bus_manager:
            return
        try:
            enable(self._bus_manager, esc_id)
        except BusManagerError as exc:  # pragma: no cover
            self._log(f"[red]Enable failed:[/red] {exc}")
        else:
            self._log(f"Enabled ESC 0x{esc_id:02X}.")

    def action_disable_selected(self) -> None:
        esc_id = self._require_selected_motor()
        if esc_id is None or not self._bus_manager:
            return
        try:
            disable(self._bus_manager, esc_id)
        except BusManagerError as exc:  # pragma: no cover
            self._log(f"[red]Disable failed:[/red] {exc}")
        else:
            self._log(f"Disabled ESC 0x{esc_id:02X}.")

    def action_zero_selected(self) -> None:
        esc_id = self._require_selected_motor()
        if esc_id is None or not self._bus_manager:
            return
        try:
            zero(self._bus_manager, esc_id)
        except BusManagerError as exc:  # pragma: no cover
            self._log(f"[red]Zero command failed:[/red] {exc}")
        else:
            self._log(f"Zero command sent to ESC 0x{esc_id:02X}.")

    def action_set_velocity(self) -> None:
        esc_id = self._require_selected_motor()
        if esc_id is None or not self._bus_manager:
            return
        telemetry = self._telemetry.get(esc_id)
        default = telemetry.velocity_rad_s if telemetry else None
        modal = VelocityModal(esc_id, default)
        self.push_screen(modal, callback=lambda value: self._apply_velocity(esc_id, value))

    def action_save_config(self) -> None:
        self._persist_config()
        self._log("Configuration saved.")

    def _apply_velocity(self, esc_id: int, value: Optional[float]) -> None:
        if value is None:
            return
        if not self._bus_manager:
            self._log("[red]Velocity ignored; bus offline.[/red]")
            return
        try:
            command_velocity(self._bus_manager, esc_id, value)
        except BusManagerError as exc:  # pragma: no cover
            self._log(f"[red]Velocity command failed:[/red] {exc}")
        else:
            self._log(f"Velocity {value:0.2f} rad/s sent to ESC 0x{esc_id:02X}.")

    def _require_selected_motor(self) -> Optional[int]:
        if self.selected_esc is None:
            self._log("Select a motor row first.")
            return None
        return self.selected_esc

    def _schedule_discovery(self, *, force_active: bool = False) -> None:
        if not self._mounted or self._bus_manager is None:
            return
        with self._threads_lock:
            if self._discovery_running:
                return
            self._discovery_running = True
        thread = threading.Thread(target=self._discovery_worker, args=(force_active,), daemon=True)
        thread.start()

    def _schedule_bus_stats_refresh(self) -> None:
        if not self._mounted:
            return
        with self._threads_lock:
            if self._bus_stats_running:
                return
            self._bus_stats_running = True
        threading.Thread(target=self._bus_stats_worker, daemon=True).start()

    def _discovery_worker(self, force_active: bool) -> None:
        try:
            bus = self._bus_manager
            if bus is None:
                return
            motors = passive_sniff(bus, duration=0.6)
            if (not motors or force_active) and bus is not None:
                active = active_probe(bus)
                motors.extend(active)
        except Exception as exc:  # pragma: no cover - hardware dependent
            self.call_from_thread(self._log, f"[red]Discovery error:[/red] {exc}")
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
            self._motors[motor.esc_id] = MotorInfo(
                esc_id=motor.esc_id,
                mst_id=motor.mst_id,
                last_seen=now,
            )
            record = self._motor_records.get(motor.esc_id)
            if record is None:
                record = MotorRecord(esc_id=motor.esc_id, mst_id=motor.mst_id)
                self._motor_records[motor.esc_id] = record
                self._config.motors.append(record)
                config_changed = True
                self._log(f"Discovered ESC 0x{motor.esc_id:02X} (MST 0x{motor.mst_id:03X}).")
            elif record.mst_id != motor.mst_id:
                record.mst_id = motor.mst_id
                config_changed = True
        self._refresh_motor_table()
        if config_changed:
            self._persist_config()

    def _ingest_feedback(self, esc_id: int, feedback: Feedback, mst_id: int, timestamp: float) -> None:
        limits = self._resolve_limits(esc_id)
        engineering = feedback.to_engineering(
            p_max=limits[0],
            v_max=limits[1],
            t_max=limits[2],
        )
        self._telemetry[esc_id] = TelemetryRecord(
            feedback=feedback,
            timestamp=timestamp,
            position_rad=engineering.position_rad,
            velocity_rad_s=engineering.velocity_rad_s,
            torque_nm=engineering.torque_nm,
        )
        self._motors[esc_id] = MotorInfo(esc_id=esc_id, mst_id=mst_id, last_seen=timestamp)
        record = self._motor_records.get(esc_id)
        config_changed = False
        if record is None:
            record = MotorRecord(esc_id=esc_id, mst_id=mst_id)
            self._motor_records[esc_id] = record
            self._config.motors.append(record)
            config_changed = True
            self._log(f"Telemetry discovered ESC 0x{esc_id:02X} (MST 0x{mst_id:03X}).")
        elif record.mst_id != mst_id:
            record.mst_id = mst_id
            config_changed = True
        if self.selected_esc is None:
            self.selected_esc = esc_id
        self._refresh_motor_table()
        self._refresh_telemetry_panel()
        self._refresh_detail_panel()
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
        available = table.available_esc_ids()
        if not available:
            self.selected_esc = None
            return
        if self.selected_esc not in available:
            self.selected_esc = available[0]
        table.focus_esc(self.selected_esc or available[0])

    def _refresh_detail_panel(self) -> None:
        panel = self.query_one(MotorDetailPanel)
        esc_id = self.selected_esc
        if esc_id is None:
            panel.show_idle()
            return
        panel.show_details(
            esc_id=esc_id,
            record=self._motor_records.get(esc_id),
            info=self._motors.get(esc_id),
            telemetry=self._telemetry.get(esc_id),
            now=monotonic(),
        )

    def _refresh_telemetry_panel(self) -> None:
        panel = self.query_one(TelemetryPanel)
        panel.update_rows(self._telemetry, monotonic())

    def _refresh_hint_panel(self) -> None:
        panel = self.query_one(HintPanel)
        panel.update_hints(self.active_bus, self.selected_esc)

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
        self._telemetry.clear()
        manager.register_listener(self._handle_bus_message)
        mst_ids = [record.mst_id for record in self._motor_records.values() if record.mst_id]
        if mst_ids:
            try:
                manager.set_filters(protocol.build_filters(mst_ids))
            except BusManagerError as exc:  # pragma: no cover
                self._log(f"[yellow]Warning:[/yellow] failed to apply filters: {exc}")
        self._log(f"Connected to {channel}.")
        self._update_bus_stats({"state": "Initializing"})

    def _close_bus(self) -> None:
        if self._bus_manager is not None:
            try:
                self._bus_manager.unregister_listener(self._handle_bus_message)
            except Exception:
                pass
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

    def _handle_bus_message(self, message) -> None:  # pragma: no cover - runtime path
        try:
            feedback = protocol.decode_feedback(message.data)
        except ValueError:
            return
        timestamp = monotonic()
        self.call_from_thread(
            self._ingest_feedback,
            feedback.esc_id,
            feedback,
            message.arbitration_id,
            timestamp,
        )

    def _resolve_limits(self, esc_id: int) -> tuple[float, float, float]:
        record = self._motor_records.get(esc_id)
        metadata = record.metadata if record else {}
        p_max = float(metadata.get("p_max", metadata.get("P_MAX", DEFAULT_P_MAX)))
        v_max = float(metadata.get("v_max", metadata.get("V_MAX", DEFAULT_V_MAX)))
        t_max = float(metadata.get("t_max", metadata.get("T_MAX", DEFAULT_T_MAX)))
        return p_max, v_max, t_max

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.sender.id != "motor-table":
            return
        try:
            esc_id = int(event.row_key)
        except (TypeError, ValueError):
            return
        self.selected_esc = esc_id


def run(config_path: Path | None = None) -> None:
    """Convenience shim to launch the Textual app."""

    DmTuiApp(config_path=config_path).run()


if __name__ == "__main__":
    run()
