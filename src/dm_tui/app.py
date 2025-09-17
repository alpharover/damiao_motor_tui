"""Textual entry point for dm-tui."""

from __future__ import annotations

import math
import os
import threading
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from time import monotonic
from typing import TYPE_CHECKING, Deque, Dict, Iterable, Mapping, Optional

from textual.app import App, ComposeResult, ScreenStackError
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.events import Mount
from textual.reactive import reactive
from textual.screen import ModalScreen
from textual.timer import Timer
from textual.command import Command, DiscoveryHit
from textual.widgets import Button, DataTable, Footer, Header, Input, Label, Log, Sparkline, Static
from textual.css.query import NoMatches

from .bus_manager import BusManager, BusManagerError
from .controllers import (
    MotorTarget,
    assign_motor_ids,
    command_mit,
    command_velocity,
    command_velocities,
    enable_all,
    disable,
    disable_all,
    enable,
    read_param_float,
    refresh_params,
    zero,
)
from .dmlib import params, protocol
from .dmlib.protocol import Feedback
from .discovery import MotorInfo, active_probe, passive_sniff
from .demos import DemoHandle, brake_to_zero, sine_orchestra
from .persistence import (
    AppConfig,
    GroupRecord,
    MotorRecord,
    DEFAULT_CONFIG_DIR,
    ensure_bus,
    load_config,
    save_config,
)
from .osutils import read_bus_statistics

if TYPE_CHECKING:
    from .logging import TelemetryCsvWriter

DEFAULT_P_MAX = 12.0
DEFAULT_V_MAX = 30.0
DEFAULT_T_MAX = 20.0
DEFAULT_KP_MAX = protocol.MIT_DEFAULT_KP_LIMIT
DEFAULT_KD_MAX = protocol.MIT_DEFAULT_KD_LIMIT

LIMIT_METADATA_KEYS = ("p_max", "v_max", "t_max")


def _parse_env_float(name: str, default: float) -> float:
    """Return a float from *name* env var, falling back to *default* on errors."""

    value = os.environ.get(name)
    if value is None or not value.strip():
        return default
    try:
        return float(value)
    except ValueError:
        return default


def _has_limit_metadata(metadata: Mapping[str, object]) -> bool:
    return all(key in metadata or key.upper() in metadata for key in LIMIT_METADATA_KEYS)


def _coerce_positive(value: object, default: float) -> float:
    try:
        candidate = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(candidate) or candidate <= 0:
        return default
    return candidate


WATCHDOG_THRESHOLD_SECONDS = _parse_env_float("DM_TUI_WATCHDOG_THRESHOLD", 3.0)
WATCHDOG_COOLDOWN_SECONDS = _parse_env_float("DM_TUI_WATCHDOG_COOLDOWN", 5.0)
WATCHDOG_INTERVAL_SECONDS = _parse_env_float("DM_TUI_WATCHDOG_INTERVAL", 1.0)

def _parse_optional_float(value: str) -> float | None:
    value = value.strip()
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


@dataclass(slots=True)
class TelemetryRecord:
    feedback: Feedback
    timestamp: float
    position_rad: float
    velocity_rad_s: float
    torque_nm: float


@dataclass(slots=True)
class IdAssignmentResult:
    esc_id: int
    mst_id: int
    control_mode: int


@dataclass(slots=True)
class MetadataUpdate:
    name: str | None
    group: str | None
    p_max: float | None
    v_max: float | None
    t_max: float | None


@dataclass(slots=True)
class GroupDefinition:
    name: str
    esc_ids: list[int]


@dataclass(slots=True)
class MitCommand:
    position_rad: float
    velocity_rad_s: float
    torque_nm: float
    kp: float
    kd: float


@dataclass(slots=True)
class DemoDefinition:
    key: str
    title: str
    description: str
    amplitude_rps: float
    frequency_hz: float
    mode: str


@dataclass(slots=True)
class ActiveDemo:
    definition: DemoDefinition
    esc_ids: list[int]
    handle: DemoHandle | None


DEMO_DEFINITIONS: list[DemoDefinition] = [
    DemoDefinition(
        key="sine",
        title="Sine Orchestra",
        description="Phase-offset sine wave across the selected group.",
        amplitude_rps=30 * 2 * math.pi / 60,
        frequency_hz=0.5,
        mode="sine",
    ),
    DemoDefinition(
        key="handshake",
        title="Handshake Duet",
        description="Alternate motors in antiphase with shared amplitude.",
        amplitude_rps=40 * 2 * math.pi / 60,
        frequency_hz=0.4,
        mode="antiphase",
    ),
    DemoDefinition(
        key="figure8",
        title="Figure Eight",
        description="Lissajous-inspired offsets for up to 8 motors.",
        amplitude_rps=35 * 2 * math.pi / 60,
        frequency_hz=0.35,
        mode="figure8",
    ),
]


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
        *,
        telemetry: Dict[int, "TelemetryRecord"] | None = None,
        watchdog_tripped: set[int] | None = None,
        last_disable: Dict[int, float] | None = None,
    ) -> None:
        self.clear()
        self._row_keys.clear()
        esc_ids = sorted(set(records.keys()) | set(motors.keys()) | set(telemetry.keys() if telemetry else []))
        for esc_id in esc_ids:
            record = records.get(esc_id)
            info = motors.get(esc_id)
            telemetry_record = telemetry.get(esc_id) if telemetry else None
            mst_id = info.mst_id if info else (record.mst_id if record else 0)
            last_seen_value: float | None = None
            if info:
                last_seen_value = info.last_seen
            if telemetry_record is not None:
                last_seen_value = (
                    telemetry_record.timestamp
                    if last_seen_value is None
                    else max(last_seen_value, telemetry_record.timestamp)
                )
            status = "Configured"
            delta = None
            if last_seen_value is not None:
                delta = max(0.0, now - last_seen_value)
                last_seen = f"{delta:0.1f}s ago"
                status = "Active" if delta < 2.0 else "Quiet"
            else:
                last_seen = "--"
            triggered = bool(watchdog_tripped and esc_id in watchdog_tripped)
            if triggered:
                status = "[red]Watchdog[/red]"
                if last_disable and esc_id in last_disable:
                    since_disable = max(0.0, now - last_disable[esc_id])
                    status = f"[red]Watchdog[/red] ({since_disable:0.1f}s ago)"
                last_seen = f"[red]{last_seen}[/red]"
            name = record.name if record and record.name else "--"
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


class ActivityLog(Log):
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
        height: 12;
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
                    "T      MIT Command",
                    "A      ID Wizard",
                    "M      Edit Metadata",
                    "Ctrl+M Edit Groups",
                    "Ctrl+G Group Actions",
                    "Ctrl+D Launch Demo",
                    "Ctrl+Shift+D Stop Demo",
                    "Ctrl+S Save Config",
                    ":      Command Palette",
                ]
            )
        )


class MotorControlPanel(Static):
    """Button panel exposing per-motor controls."""

    DEFAULT_CSS = """
    MotorControlPanel {
        border: round $accent;
        height: 8;
        padding: 1 1;
    }

    #motor-control-buttons {
        height: auto;
        padding-top: 1;
    }

    #motor-control-buttons Button {
        margin-right: 1;
    }
    """

    def __init__(self, *children, **kwargs) -> None:
        super().__init__(*children, **kwargs)
        self._info = Static("Select a motor to enable controls.", id="motor-control-info")
        specs = [
            ("control-enable", "Enable", "action_enable_selected"),
            ("control-disable", "Disable", "action_disable_selected"),
            ("control-zero", "Zero", "action_zero_selected"),
            ("control-velocity", "Velocity…", "action_set_velocity"),
            ("control-mit", "MIT…", "action_set_mit"),
        ]
        buttons = []
        self._buttons: dict[str, Button] = {}
        self._action_lookup: dict[str, str] = {}
        for button_id, label, action in specs:
            button = Button(label, id=button_id)
            button.disabled = True
            self._buttons[button_id] = button
            self._action_lookup[button_id] = action
            buttons.append(button)
        self._buttons_row = Horizontal(*buttons, id="motor-control-buttons")
        self._current_esc: int | None = None

    def compose(self) -> ComposeResult:
        yield self._info
        yield self._buttons_row

    def update_controls(self, esc_id: int | None, *, bus_online: bool) -> None:
        self._current_esc = esc_id
        if esc_id is None:
            self._info.update("Select a motor to enable controls.")
        else:
            status = "online" if bus_online else "offline"
            self._info.update(f"ESC 0x{esc_id:02X} ({status})")
        disabled = esc_id is None or not bus_online
        for button in self._buttons.values():
            try:
                button.disabled = disabled
            except Exception:  # fallback for tests when widget not attached
                button._disabled = disabled  # type: ignore[attr-defined]

    def on_button_pressed(self, event: Button.Pressed) -> None:
        action = self._action_lookup.get(event.button.id)
        if action is None:
            return
        self._dispatch_action(action)

    def _dispatch_action(self, action: str) -> None:
        app = getattr(self, "app", None)
        if app is None:
            return
        handler = getattr(app, action, None)
        if handler is None:
            return
        caller = getattr(app, "call_from_thread", None)
        if callable(caller):
            try:
                caller(handler)
                return
            except RuntimeError:
                pass
        handler()


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
        watchdog_active: bool = False,
        watchdog_last: float | None = None,
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
        group = record.group if record and record.group else "--"
        lines = [
            f"[b]ESC[/b] 0x{esc_id:02X} | [b]MST[/b] 0x{mst_id:03X}",
            f"[b]Name[/b] {name}",
            f"[b]Group[/b] {group}",
            f"[b]Last Seen[/b] {last_seen}",
            f"[b]Temps[/b] {temps}",
            f"[b]Velocity[/b] {velocity}",
            f"[b]Torque[/b] {torque}",
        ]
        if delta is not None:
            status = "Fresh" if delta < 1.0 else "Stale"
            lines.append(f"[b]Telemetry[/b] {status} ({delta:0.1f}s old)")
        if watchdog_active:
            if watchdog_last is not None:
                lines.append(
                    f"[red]Watchdog auto-disable[/red] {max(0.0, now - watchdog_last):0.1f}s ago."
                )
            else:
                lines.append("[red]Watchdog auto-disable active.[/red]")
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


class GroupPanel(Static):
    """Display configured motor groups."""

    DEFAULT_CSS = """
    GroupPanel {
        border: round $accent;
        height: 8;
        padding: 1 1;
    }
    """

    def update_groups(self, groups: Dict[str, GroupRecord]) -> None:
        if not groups:
            self.update("No groups configured. Press M to tag motors or Ctrl+G to define one.")
            return
        lines = ["[b]Groups[/b]"]
        for name, record in sorted(groups.items()):
            escs = ", ".join(f"0x{esc:02X}" for esc in sorted(record.esc_ids)) or "(empty)"
            lines.append(f"{name}: {escs}")
        lines.append("")
        lines.append("Ctrl+G to run actions · Ctrl+D to launch demos")
        self.update("\n".join(lines))


class VelocitySparkline(Static):
    """Sparkline showing recent velocity history for the selected motor."""

    DEFAULT_CSS = """
    VelocitySparkline {
        border: round $accent;
        height: 6;
        padding: 1 1;
    }
    VelocitySparkline > #velocity-sparkline {
        height: 2;
        margin-top: 1;
        margin-bottom: 1;
    }
    """

    def __init__(self, *, id: str | None = None) -> None:
        super().__init__(id=id)
        self._spark = Sparkline(id="velocity-sparkline")
        self._caption = Label("Select a motor to view velocity history.", id="velocity-caption")

    def compose(self) -> ComposeResult:
        yield Label("Velocity History", id="velocity-title")
        yield self._spark
        yield self._caption

    def update_series(self, esc_id: Optional[int], values: Iterable[float]) -> None:
        series = list(values)
        self._spark.data = series if series else None
        if esc_id is None:
            self._caption.update("Select a motor to view velocity history.")
        elif not series:
            self._caption.update(f"ESC 0x{esc_id:02X}: no velocity samples yet.")
        else:
            self._caption.update(f"ESC 0x{esc_id:02X}: last {len(series)} samples")


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
        apply_button = self.query_one("#apply", Button)
        self.on_button_pressed(Button.Pressed(apply_button))


class MitModal(ModalScreen[Optional[MitCommand]]):
    """Collect MIT-mode setpoint parameters for a single motor."""

    def __init__(
        self,
        esc_id: int,
        *,
        defaults: MitCommand | None = None,
        position_limit: float,
        velocity_limit: float,
        torque_limit: float,
        kp_limit: float,
        kd_limit: float,
    ) -> None:
        super().__init__()
        self._esc_id = esc_id
        self._defaults = defaults or MitCommand(0.0, 0.0, 0.0, 0.0, 0.0)
        self._position_limit = abs(position_limit)
        self._velocity_limit = abs(velocity_limit)
        self._torque_limit = abs(torque_limit)
        self._kp_limit = abs(kp_limit)
        self._kd_limit = abs(kd_limit)
        self._error: Label | None = None
        self._position_input: Input | None = None
        self._velocity_input: Input | None = None
        self._torque_input: Input | None = None
        self._kp_input: Input | None = None
        self._kd_input: Input | None = None

    def compose(self) -> ComposeResult:
        yield Static(f"MIT command for ESC 0x{self._esc_id:02X}", id="mit-title")
        self._position_input = Input(
            self._format_default(self._defaults.position_rad),
            placeholder=f"Position ±{self._position_limit:0.2f} rad",
            id="mit-position",
        )
        yield self._position_input
        self._velocity_input = Input(
            self._format_default(self._defaults.velocity_rad_s),
            placeholder=f"Velocity ±{self._velocity_limit:0.2f} rad/s",
            id="mit-velocity",
        )
        yield self._velocity_input
        self._torque_input = Input(
            self._format_default(self._defaults.torque_nm),
            placeholder=f"Torque ±{self._torque_limit:0.2f} Nm",
            id="mit-torque",
        )
        yield self._torque_input
        self._kp_input = Input(
            self._format_default(self._defaults.kp),
            placeholder=f"Kp 0–{self._kp_limit:0.2f}",
            id="mit-kp",
        )
        yield self._kp_input
        self._kd_input = Input(
            self._format_default(self._defaults.kd),
            placeholder=f"Kd 0–{self._kd_limit:0.2f}",
            id="mit-kd",
        )
        yield self._kd_input
        self._error = Label("", id="mit-error")
        yield self._error
        with Horizontal(id="mit-buttons"):
            yield Button("Cancel", id="cancel")
            yield Button("Apply", id="apply", variant="primary")

    def _format_default(self, value: float) -> str:
        return "" if abs(value) < 1e-9 else f"{value:0.3f}"

    def on_mount(self, event: Mount) -> None:  # noqa: D401
        if self._position_input:
            self.set_focus(self._position_input)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.dismiss(None)
            return
        try:
            command = MitCommand(
                position_rad=self._parse_value(
                    self._position_input,
                    name="Position",
                    minimum=-self._position_limit,
                    maximum=self._position_limit,
                    default=self._defaults.position_rad,
                ),
                velocity_rad_s=self._parse_value(
                    self._velocity_input,
                    name="Velocity",
                    minimum=-self._velocity_limit,
                    maximum=self._velocity_limit,
                    default=self._defaults.velocity_rad_s,
                ),
                torque_nm=self._parse_value(
                    self._torque_input,
                    name="Torque",
                    minimum=-self._torque_limit,
                    maximum=self._torque_limit,
                    default=self._defaults.torque_nm,
                ),
                kp=self._parse_value(
                    self._kp_input,
                    name="Kp",
                    minimum=0.0,
                    maximum=self._kp_limit,
                    default=self._defaults.kp,
                ),
                kd=self._parse_value(
                    self._kd_input,
                    name="Kd",
                    minimum=0.0,
                    maximum=self._kd_limit,
                    default=self._defaults.kd,
                ),
            )
        except ValueError as exc:
            if self._error:
                self._error.update(str(exc))
            return
        self.dismiss(command)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        apply_button = self.query_one("#apply", Button)
        self.on_button_pressed(Button.Pressed(apply_button))

    def _parse_value(
        self,
        widget: Input | None,
        *,
        name: str,
        minimum: float,
        maximum: float,
        default: float,
    ) -> float:
        if widget is None:
            return default
        text = widget.value.strip()
        if not text:
            return default
        try:
            value = float(text)
        except ValueError as exc:  # pragma: no cover - defensive
            raise ValueError(f"Enter a numeric {name} value.") from exc
        if value < minimum or value > maximum:
            raise ValueError(
                f"{name} must be between {minimum:0.2f} and {maximum:0.2f}."
            )
        return value


class GroupVelocityModal(ModalScreen[Optional[float]]):
    """Collect a velocity setpoint for a group."""

    def __init__(self, group: str) -> None:
        super().__init__()
        self._group = group
        self._input = Input(placeholder="rad/s (applied to each motor)")
        self._error = Label("")

    def compose(self) -> ComposeResult:
        yield Static(f"Set velocity for group '{self._group}'", id="gvel-title")
        yield self._input
        yield self._error
        with Horizontal(id="gvel-buttons"):
            yield Button("Cancel", id="cancel")
            yield Button("Apply", id="apply", variant="primary")

    def on_mount(self, event: Mount) -> None:  # noqa: D401
        self.set_focus(self._input)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.dismiss(None)
            return
        try:
            value = float(self._input.value.strip())
        except ValueError:
            self._error.update("Enter a numeric rad/s value.")
            return
        self.dismiss(value)


class IdWizardModal(ModalScreen[Optional[IdAssignmentResult]]):
    """Collect new ESC/MST IDs and control mode."""

    def __init__(self, current_esc: int, current_mst: int, default_mode: int = 3) -> None:
        super().__init__()
        self._current_esc = current_esc
        self._current_mst = current_mst
        self._default_mode = default_mode
        self._esc_input: Input | None = None
        self._mst_input: Input | None = None
        self._mode_input: Input | None = None
        self._error: Label | None = None

    def compose(self) -> ComposeResult:
        yield Static(
            "Assign new IDs. Writes ESC_ID, MST_ID, CTRL_MODE and persists via SAVE.",
            id="id-title",
        )
        self._esc_input = Input(str(self._current_esc), placeholder="New ESC ID (decimal or 0x)", id="id-esc")
        yield Label("ESC ID")
        yield self._esc_input
        default_mst = self._current_mst if self._current_mst else (self._current_esc + 0x10)
        self._mst_input = Input(str(default_mst), placeholder="New MST ID", id="id-mst")
        yield Label("MST ID")
        yield self._mst_input
        self._mode_input = Input(str(self._default_mode), placeholder="CTRL_MODE (e.g. 3)", id="id-mode")
        yield Label("Control Mode")
        yield self._mode_input
        self._error = Label("", id="id-error")
        yield self._error
        with Horizontal(id="id-buttons"):
            yield Button("Cancel", id="cancel")
            yield Button("Apply", id="apply", variant="primary")

    def on_mount(self, event: Mount) -> None:  # noqa: D401
        if self._esc_input:
            self.set_focus(self._esc_input)

    def _parse_value(self, inp: Input | None, label: str) -> Optional[int]:
        if inp is None:
            return None
        raw = inp.value.strip()
        if not raw:
            if self._error:
                self._error.update(f"{label} is required.")
            return None
        try:
            base = 16 if raw.lower().startswith("0x") else 10
            return int(raw, base)
        except ValueError:
            if self._error:
                self._error.update(f"{label} must be numeric.")
            return None

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.dismiss(None)
            return
        esc_id = self._parse_value(self._esc_input, "ESC ID")
        mst_id = self._parse_value(self._mst_input, "MST ID")
        mode = self._parse_value(self._mode_input, "Control mode")
        if esc_id is None or mst_id is None or mode is None:
            return
        if not (1 <= esc_id <= 0x7F and 1 <= mst_id <= 0x7FF):
            if self._error:
                self._error.update("ESC ID (1-127) and MST ID (1-2047) expected.")
            return
        self.dismiss(IdAssignmentResult(esc_id=esc_id, mst_id=mst_id, control_mode=mode))


class MetadataModal(ModalScreen[Optional[MetadataUpdate]]):
    """Capture friendly metadata for a motor."""

    def __init__(self, esc_id: int, record: MotorRecord | None) -> None:
        super().__init__()
        metadata = record.metadata if record else {}
        self._esc_id = esc_id
        self._name_input = Input(record.name or "" if record else "", placeholder="Display name")
        self._group_input = Input(record.group or "" if record else "", placeholder="Group name")
        self._p_input = Input(str(metadata.get("p_max", "")), placeholder="P_MAX (rad)")
        self._v_input = Input(str(metadata.get("v_max", "")), placeholder="V_MAX (rad/s)")
        self._t_input = Input(str(metadata.get("t_max", "")), placeholder="T_MAX (Nm)")

    def compose(self) -> ComposeResult:
        yield Static(f"Metadata for ESC 0x{self._esc_id:02X}", id="meta-title")
        yield Label("Name")
        yield self._name_input
        yield Label("Group")
        yield self._group_input
        yield Label("P_MAX")
        yield self._p_input
        yield Label("V_MAX")
        yield self._v_input
        yield Label("T_MAX")
        yield self._t_input
        with Horizontal(id="meta-buttons"):
            yield Button("Cancel", id="cancel")
            yield Button("Save", id="save", variant="primary")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.dismiss(None)
            return
        update = MetadataUpdate(
            name=self._name_input.value.strip() or None,
            group=self._group_input.value.strip() or None,
            p_max=_parse_optional_float(self._p_input.value),
            v_max=_parse_optional_float(self._v_input.value),
            t_max=_parse_optional_float(self._t_input.value),
        )
        self.dismiss(update)


class GroupModal(ModalScreen[Optional[GroupDefinition]]):
    """Create or update a group definition."""

    def __init__(self, existing: Dict[str, GroupRecord]) -> None:
        super().__init__()
        default_name = next(iter(existing.keys()), "demo")
        self._name_input = Input(default_name, placeholder="Group name")
        default_escs = " ".join(f"0x{esc:02X}" for esc in existing.get(default_name, GroupRecord(default_name, [])).esc_ids)
        self._esc_input = Input(default_escs, placeholder="ESC IDs (comma/space, allow 0x)")
        self._error = Label("")

    def compose(self) -> ComposeResult:
        yield Static("Define a group for synchronized commands.", id="group-title")
        yield Label("Group Name")
        yield self._name_input
        yield Label("ESC IDs")
        yield self._esc_input
        yield self._error
        with Horizontal(id="group-buttons"):
            yield Button("Cancel", id="cancel")
            yield Button("Save", id="save", variant="primary")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.dismiss(None)
            return
        name = self._name_input.value.strip()
        if not name:
            self._error.update("[red]Name required.[/red]")
            return
        esc_field = self._esc_input.value.replace(",", " ")
        esc_tokens = [tok for tok in esc_field.split() if tok]
        esc_ids: list[int] = []
        for tok in esc_tokens:
            try:
                base = 16 if tok.lower().startswith("0x") else 10
                esc_ids.append(int(tok, base))
            except ValueError:
                self._error.update(f"[red]Invalid ESC ID token: {tok}[/red]")
                return
        self.dismiss(GroupDefinition(name=name, esc_ids=esc_ids))


class GroupActionModal(ModalScreen[Optional[tuple[str, str]]]):
    """Prompt for a group action."""

    def __init__(self, groups: Dict[str, GroupRecord]) -> None:
        super().__init__()
        self._groups = groups
        default = next(iter(groups.keys()), "")
        self._group_input = Input(default, placeholder="Group name")
        self._error = Label("")

    def compose(self) -> ComposeResult:
        yield Static("Select group action (enable/disable/velocity).", id="gact-title")
        yield Label("Group Name")
        yield self._group_input
        yield self._error
        with Horizontal(id="gact-buttons"):
            yield Button("Enable", id="enable", variant="primary")
            yield Button("Disable", id="disable")
            yield Button("Velocity", id="velocity")
            yield Button("Cancel", id="cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.dismiss(None)
            return
        group = self._group_input.value.strip()
        if not group:
            self._error.update("[red]Provide a group name.[/red]")
            return
        if self._groups and group not in self._groups:
            self._error.update(f"[red]Unknown group '{group}'.[/red]")
            return
        self.dismiss((event.button.id, group))


class DemoModal(ModalScreen[Optional[tuple[str, str]]]):
    """Prompt for demo and group selection."""

    def __init__(self, demos: Iterable[DemoDefinition], groups: Dict[str, GroupRecord]) -> None:
        super().__init__()
        self._demos = {demo.key: demo for demo in demos}
        self._groups = groups
        default_group = next(iter(groups.keys()), "ALL")
        self._demo_input = Input(next(iter(self._demos.keys()), "sine"), placeholder="Demo key (sine/handshake/figure8)")
        self._group_input = Input(default_group, placeholder="Group name or ALL")
        self._error = Label("")

    def compose(self) -> ComposeResult:
        demos = "\n".join(f" - {demo.key}: {demo.title}" for demo in self._demos.values())
        yield Static(f"Available demos:\n{demos}", id="demo-list")
        yield Label("Demo Key")
        yield self._demo_input
        yield Label("Group (or 'ALL')")
        yield self._group_input
        yield self._error
        with Horizontal(id="demo-buttons"):
            yield Button("Cancel", id="cancel")
            yield Button("Launch", id="launch", variant="primary")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.dismiss(None)
            return
        demo_key = self._demo_input.value.strip()
        if demo_key not in self._demos:
            self._error.update(f"[red]Unknown demo '{demo_key}'.[/red]")
            return
        group_name = self._group_input.value.strip() or "ALL"
        if group_name != "ALL" and group_name not in self._groups:
            self._error.update(f"[red]Unknown group '{group_name}'.[/red]")
            return
        self.dismiss((demo_key, group_name))


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
        Binding("t", "set_mit", "MIT Cmd", show=True),
        Binding("a", "assign_ids", "Assign IDs", show=True),
        Binding("m", "edit_metadata", "Edit Metadata", show=True),
        Binding("ctrl+m", "manage_groups", "Edit Groups", show=True),
        Binding("ctrl+g", "prompt_group_action", "Group Actions", show=True),
        Binding("ctrl+d", "launch_demo", "Launch Demo", show=True),
        Binding("ctrl+shift+d", "stop_demo", "Stop Demo", show=True),
        Binding(":", "open_command_palette", "Command Palette", show=False),
        Binding("ctrl+s", "save_config", "Save config", show=False),
        Binding("ctrl+c", "quit", "Quit", show=False),
    ]

    active_bus = reactive("canB")
    selected_esc = reactive(None)

    def __init__(self, config_path: Path | None = None) -> None:
        super().__init__()
        self._config_path = config_path
        self._threads_lock = threading.Lock()
        self._mounted = False
        self._config: AppConfig = load_config(config_path)
        ensure_bus(self._config, self._config.active_bus, make_active=True)
        self._motor_records: Dict[int, MotorRecord] = {
            record.esc_id: record for record in self._config.motors
        }
        self._motors: Dict[int, MotorInfo] = {}
        self._telemetry: Dict[int, TelemetryRecord] = {}
        self._telemetry_history: Dict[int, Deque[float]] = {}
        self._torque_history: Dict[int, Deque[float]] = {}
        self._temp_history: Dict[int, Deque[int]] = {}
        config_dir = config_path.expanduser().parent if config_path is not None else DEFAULT_CONFIG_DIR
        self._telemetry_log_path = config_dir / "telemetry.csv"
        self._telemetry_log_writer: "TelemetryCsvWriter | None" = None
        self._telemetry_log_error = False
        self._groups: Dict[str, GroupRecord] = {
            group.name: GroupRecord(name=group.name, esc_ids=list(group.esc_ids))
            for group in self._config.groups
        }
        self._bus_manager: BusManager | None = None
        self._bus_stats_timer: Timer | None = None
        self._discovery_timer: Timer | None = None
        self._watchdog_timer: Timer | None = None
        self._active_demo: ActiveDemo | None = None
        self._discovery_running = False
        self._bus_stats_running = False
        self._watchdog_threshold = WATCHDOG_THRESHOLD_SECONDS
        self._watchdog_cooldown = max(WATCHDOG_COOLDOWN_SECONDS, self._watchdog_threshold)
        self._watchdog_interval = max(WATCHDOG_INTERVAL_SECONDS, 0.2)
        self._watchdog_last_disable: Dict[int, float] = {}
        self._watchdog_tripped: set[int] = set()
        self._limits_loaded: set[int] = {
            esc_id
            for esc_id, record in self._motor_records.items()
            if _has_limit_metadata(record.metadata)
        }
        self._limit_errors: set[int] = set()
        self.active_bus = self._config.active_bus

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="content"):
            with Vertical(id="left-column"):
                yield BusStatusPanel(id="bus-status")
                yield MotorTable(id="motor-table")
                yield TelemetryPanel(id="telemetry-panel")
                yield VelocitySparkline(id="velocity-history")
            with Vertical(id="right-column"):
                yield MotorDetailPanel(id="motor-detail")
                yield MotorControlPanel(id="motor-control")
                yield GroupPanel(id="group-panel")
                yield ActivityLog(id="activity-log")
                yield HintPanel(id="hint-panel")
        yield Footer()

    def on_mount(self) -> None:
        self._mounted = True
        self._refresh_hint_panel()
        self._refresh_motor_table()
        self._refresh_detail_panel()
        self._refresh_group_panel()
        self._refresh_control_panel()
        self._open_bus(self.active_bus)
        self._bus_stats_timer = self.set_interval(3.0, self._schedule_bus_stats_refresh)
        self._discovery_timer = self.set_interval(4.0, self._schedule_discovery)
        self._watchdog_timer = self.set_interval(self._watchdog_interval, self._watchdog_check)

    def on_unmount(self) -> None:
        if self._bus_stats_timer:
            self._bus_stats_timer.stop()
        if self._discovery_timer:
            self._discovery_timer.stop()
        if self._watchdog_timer:
            self._watchdog_timer.stop()
        self._stop_demo(disable=False)
        self._close_bus()
        self._close_telemetry_log()
        self._mounted = False

    def watch_active_bus(self, active_bus: str) -> None:
        if not self._mounted:
            return
        self._config.active_bus = active_bus
        self._refresh_hint_panel()
        self._open_bus(active_bus)
        self._schedule_bus_stats_refresh()
        self._refresh_control_panel()

    def watch_selected_esc(self, selected_esc: Optional[int]) -> None:
        if not self._mounted:
            return
        self._refresh_hint_panel()
        self._refresh_detail_panel()
        self._refresh_control_panel()

    # --- Actions -----------------------------------------------------------

    def action_estop(self) -> None:
        self._stop_demo(disable=False)
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
        self._stop_demo(disable=False)
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
        self._stop_demo(disable=False)
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
        self._stop_demo(disable=False)
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

    def action_set_mit(self) -> None:
        esc_id = self._require_selected_motor()
        if esc_id is None or not self._bus_manager:
            return
        telemetry = self._telemetry.get(esc_id)
        defaults = MitCommand(
            position_rad=telemetry.position_rad if telemetry else 0.0,
            velocity_rad_s=telemetry.velocity_rad_s if telemetry else 0.0,
            torque_nm=0.0,
            kp=0.0,
            kd=0.0,
        )
        limits = self._resolve_mit_limits(esc_id)
        modal = MitModal(
            esc_id,
            defaults=defaults,
            position_limit=limits[0],
            velocity_limit=limits[1],
            torque_limit=limits[2],
            kp_limit=limits[3],
            kd_limit=limits[4],
        )
        self.push_screen(
            modal,
            callback=lambda command, esc=esc_id, lim=limits: self._apply_mit(esc, command, lim),
        )

    def action_assign_ids(self) -> None:
        esc_id = self._require_selected_motor()
        if esc_id is None:
            return
        if not self._bus_manager:
            self._log("[red]Cannot assign IDs; bus offline.[/red]")
            return
        info = self._motors.get(esc_id)
        record = self._motor_records.get(esc_id)
        current_mst = info.mst_id if info else (record.mst_id if record else esc_id + 0x10)
        default_mode = 3
        if record and record.metadata.get("ctrl_mode"):
            try:
                default_mode = int(record.metadata["ctrl_mode"])
            except (TypeError, ValueError):
                default_mode = 3
        modal = IdWizardModal(esc_id, current_mst, default_mode)
        self.push_screen(modal, callback=lambda result: self._apply_id_assignment(esc_id, result))

    def action_edit_metadata(self) -> None:
        esc_id = self._require_selected_motor()
        if esc_id is None:
            return
        modal = MetadataModal(esc_id, self._motor_records.get(esc_id))
        self.push_screen(modal, callback=lambda update: self._apply_metadata_update(esc_id, update))

    def action_prompt_group_action(self) -> None:
        if not self._groups:
            self._log("Define a group first (press M to tag motors).")
            return
        modal = GroupActionModal(self._groups)
        self.push_screen(modal, callback=self._handle_group_action)

    def action_launch_demo(self) -> None:
        if not self._groups and not (self._motor_records or self._motors):
            self._log("No motors available. Discover or configure groups first.")
            return
        modal = DemoModal(DEMO_DEFINITIONS, self._groups)
        self.push_screen(modal, callback=self._handle_demo_selection)

    def action_stop_demo(self) -> None:
        if self._active_demo is None:
            self._log("No demo running.")
            return
        self._stop_demo(disable=True)

    def action_save_config(self) -> None:
        self._persist_config()
        self._log("Configuration saved.")

    def action_open_command_palette(self) -> None:
        super().action_command_palette()

    # --- Internal helpers --------------------------------------------------

    def _require_selected_motor(self) -> Optional[int]:
        if self.selected_esc is None:
            self._log("Select a motor row first.")
            return None
        return self.selected_esc

    def _apply_velocity(self, esc_id: int, value: Optional[float]) -> None:
        if value is None:
            return
        if not self._bus_manager:
            self._log("[red]Velocity ignored; bus offline.[/red]")
            return
        self._stop_demo(disable=False)
        try:
            command_velocity(self._bus_manager, esc_id, value)
        except BusManagerError as exc:  # pragma: no cover
            self._log(f"[red]Velocity command failed:[/red] {exc}")
        else:
            self._log(f"Velocity {value:0.2f} rad/s sent to ESC 0x{esc_id:02X}.")

    def _apply_mit(
        self,
        esc_id: int,
        command: Optional[MitCommand],
        limits: tuple[float, float, float, float, float],
    ) -> None:
        if command is None:
            return
        if not self._bus_manager:
            self._log("[red]MIT command ignored; bus offline.[/red]")
            return
        position_limit, velocity_limit, torque_limit, kp_limit, kd_limit = limits
        sanitized, adjustments = self._sanitize_mit_command(command, limits)
        self._stop_demo(disable=False)
        try:
            command_mit(
                self._bus_manager,
                esc_id,
                position_rad=sanitized.position_rad,
                velocity_rad_s=sanitized.velocity_rad_s,
                torque_nm=sanitized.torque_nm,
                kp=sanitized.kp,
                kd=sanitized.kd,
                position_limit=position_limit,
                velocity_limit=velocity_limit,
                torque_limit=torque_limit,
                kp_limit=kp_limit,
                kd_limit=kd_limit,
            )
        except BusManagerError as exc:  # pragma: no cover
            self._log(f"[red]MIT command failed:[/red] {exc}")
        else:
            if adjustments:
                self._log(f"[yellow]MIT command clamped ({', '.join(adjustments)}).[/yellow]")
            self._log(f"MIT command sent to ESC 0x{esc_id:02X}.")

    def _sanitize_mit_command(
        self,
        command: MitCommand,
        limits: tuple[float, float, float, float, float],
    ) -> tuple[MitCommand, list[str]]:
        position_limit, velocity_limit, torque_limit, kp_limit, kd_limit = limits
        adjustments: list[str] = []

        def clamp(value: float, minimum: float, maximum: float, label: str) -> float:
            clamped = max(min(value, maximum), minimum)
            if abs(clamped - value) > 1e-6:
                adjustments.append(label)
            return clamped

        sanitized = MitCommand(
            position_rad=clamp(command.position_rad, -abs(position_limit), abs(position_limit), "position"),
            velocity_rad_s=clamp(command.velocity_rad_s, -abs(velocity_limit), abs(velocity_limit), "velocity"),
            torque_nm=clamp(command.torque_nm, -abs(torque_limit), abs(torque_limit), "torque"),
            kp=clamp(command.kp, 0.0, abs(kp_limit), "kp"),
            kd=clamp(command.kd, 0.0, abs(kd_limit), "kd"),
        )
        return sanitized, adjustments

    def _handle_group_action(self, result: Optional[tuple[str, str]]) -> None:
        if result is None:
            return
        action, group = result
        if action == "velocity":
            modal = GroupVelocityModal(group)
            self.push_screen(modal, callback=lambda value: self._execute_group_action(action, group, velocity=value))
            return
        self._execute_group_action(action, group)

    def _execute_group_action(self, action: str, group_name: str, *, velocity: float | None = None) -> None:
        group = self._groups.get(group_name)
        if group is None:
            self._log(f"[red]Group '{group_name}' not found.[/red]")
            return
        esc_ids = [esc for esc in group.esc_ids if esc is not None]
        if not esc_ids:
            self._log(f"Group '{group_name}' is empty.")
            return
        if not self._bus_manager:
            self._log("[red]Bus offline; group command ignored.[/red]")
            return
        self._stop_demo(disable=False)
        try:
            if action == "enable":
                enable_all(self._bus_manager, esc_ids)
                self._log(f"Enabled group '{group_name}' ({len(esc_ids)} motors).")
            elif action == "disable":
                disable_all(self._bus_manager, esc_ids)
                self._log(f"Disabled group '{group_name}'.")
            elif action == "velocity":
                if velocity is None:
                    self._log("[red]Velocity value required.[/red]")
                    return
                targets = [MotorTarget(esc_id=esc, velocity_rad_s=velocity) for esc in esc_ids]
                command_velocities(self._bus_manager, targets)
                self._log(f"Velocity {velocity:0.2f} rad/s sent to group '{group_name}'.")
            else:
                self._log(f"[red]Unknown group action '{action}'.[/red]")
        except BusManagerError as exc:  # pragma: no cover
            self._log(f"[red]Group action failed:[/red] {exc}")

    def _apply_id_assignment(self, current_esc: int, result: Optional[IdAssignmentResult]) -> None:
        if result is None:
            return
        if not self._bus_manager:
            self._log("[red]Cannot write IDs; bus offline.[/red]")
            return
        self._stop_demo(disable=False)
        try:
            assign_motor_ids(
                self._bus_manager,
                current_esc=current_esc,
                new_esc=result.esc_id,
                new_mst=result.mst_id,
                control_mode=result.control_mode,
            )
        except BusManagerError as exc:  # pragma: no cover
            self._log(f"[red]ID assignment failed:[/red] {exc}")
            return
        self._log(
            f"Assigned ESC 0x{result.esc_id:02X}, MST 0x{result.mst_id:03X}, CTRL_MODE {result.control_mode}."
        )
        record = self._motor_records.pop(current_esc, None)
        if record is None:
            record = MotorRecord(esc_id=result.esc_id, mst_id=result.mst_id)
        else:
            record.esc_id = result.esc_id
            record.mst_id = result.mst_id
        record.metadata.setdefault("ctrl_mode", result.control_mode)
        self._motor_records[result.esc_id] = record
        info = self._motors.pop(current_esc, None)
        if info:
            self._motors[result.esc_id] = MotorInfo(
                esc_id=result.esc_id,
                mst_id=result.mst_id,
                last_seen=monotonic(),
            )
        tele = self._telemetry.pop(current_esc, None)
        if tele:
            self._telemetry[result.esc_id] = tele
        history = self._telemetry_history.pop(current_esc, None)
        if history:
            self._telemetry_history[result.esc_id] = history
        torque_history = self._torque_history.pop(current_esc, None)
        if torque_history:
            self._torque_history[result.esc_id] = torque_history
        temp_history = self._temp_history.pop(current_esc, None)
        if temp_history:
            self._temp_history[result.esc_id] = temp_history
        for group in self._groups.values():
            group.esc_ids = [result.esc_id if esc == current_esc else esc for esc in group.esc_ids]
        self._cleanup_empty_groups()
        self.selected_esc = result.esc_id
        self._refresh_motor_table()
        self._refresh_telemetry_panel()
        self._refresh_detail_panel()
        self._refresh_group_panel()
        self._reapply_filters()
        self._persist_config()

    def _apply_metadata_update(self, esc_id: int, update: Optional[MetadataUpdate]) -> None:
        if update is None:
            return
        record = self._motor_records.get(esc_id)
        if record is None:
            mst_id = self._motors.get(esc_id).mst_id if esc_id in self._motors else esc_id + 0x10
            record = MotorRecord(esc_id=esc_id, mst_id=mst_id)
            self._motor_records[esc_id] = record
        record.name = update.name
        record.group = update.group
        for key, value in (("p_max", update.p_max), ("v_max", update.v_max), ("t_max", update.t_max)):
            if value is None:
                record.metadata.pop(key, None)
            else:
                record.metadata[key] = value
        for group in self._groups.values():
            if esc_id in group.esc_ids and group.name != (update.group or group.name):
                group.esc_ids = [e for e in group.esc_ids if e != esc_id]
        if update.group:
            group = self._groups.setdefault(update.group, GroupRecord(name=update.group, esc_ids=[]))
            if esc_id not in group.esc_ids:
                group.esc_ids.append(esc_id)
        self._cleanup_empty_groups()
        self._refresh_group_panel()
        self._refresh_detail_panel()
        self._persist_config()
        self._log(f"Updated metadata for ESC 0x{esc_id:02X}.")

    def _apply_group_definition(self, definition: Optional[GroupDefinition]) -> None:
        if definition is None:
            return
        unique_ids = sorted({esc for esc in definition.esc_ids if esc > 0})
        self._groups[definition.name] = GroupRecord(name=definition.name, esc_ids=unique_ids)
        self._cleanup_empty_groups()
        self._refresh_group_panel()
        self._persist_config()
        self._log(f"Group '{definition.name}' updated ({len(unique_ids)} motors).")

    def _handle_demo_selection(self, selection: Optional[tuple[str, str]]) -> None:
        if selection is None:
            return
        demo_key, group_name = selection
        definition = next((demo for demo in DEMO_DEFINITIONS if demo.key == demo_key), None)
        if definition is None:
            self._log(f"[red]Demo '{demo_key}' not found.[/red]")
            return
        if group_name == "ALL":
            esc_ids = sorted(set(self._motor_records.keys()) | set(self._motors.keys()))
            if not esc_ids:
                self._log("No motors available to run demo.")
                return
        else:
            group = self._groups.get(group_name)
            if not group or not group.esc_ids:
                self._log(f"Group '{group_name}' is empty.")
                return
            esc_ids = list(group.esc_ids)
        self._start_demo(definition, esc_ids, group_name)

    def _start_demo(self, definition: DemoDefinition, esc_ids: list[int], label: str) -> None:
        if not self._bus_manager:
            self._log("[red]Cannot start demo; bus offline.[/red]")
            return
        self._stop_demo(disable=False)
        try:
            handle = sine_orchestra(
                self._bus_manager,
                esc_ids,
                amplitude_rps=definition.amplitude_rps,
                frequency_hz=definition.frequency_hz,
                mode=definition.mode,
            )
        except (BusManagerError, ValueError) as exc:
            self._log(f"[red]Failed to start demo:[/red] {exc}")
            return
        self._active_demo = ActiveDemo(definition=definition, esc_ids=list(esc_ids), handle=handle)
        self._log(f"Starting demo '{definition.title}' using {label} ({len(esc_ids)} motors).")

    def _stop_demo(self, disable: bool) -> None:
        if not self._active_demo:
            return
        demo = self._active_demo
        self._active_demo = None
        if demo.handle is not None:
            try:
                demo.handle.stop()
            except Exception:
                pass
        if self._bus_manager:
            try:
                if disable:
                    disable_all(self._bus_manager, demo.esc_ids)
                else:
                    brake_to_zero(self._bus_manager, demo.esc_ids)
            except BusManagerError:
                pass
        if disable:
            self._log("Demo stopped and motors disabled.")
        else:
            self._log("Demo stopped.")

    def _schedule_discovery(self, *, force_active: bool = False) -> None:
        if not self._mounted or self._bus_manager is None:
            return
        with self._threads_lock:
            if self._discovery_running:
                return
            self._discovery_running = True
        threading.Thread(target=self._discovery_worker, args=(force_active,), daemon=True).start()

    def _schedule_bus_stats_refresh(self) -> None:
        if not self._mounted:
            return
        with self._threads_lock:
            if self._bus_stats_running:
                return
        self._bus_stats_running = True
        threading.Thread(target=self._bus_stats_worker, daemon=True).start()

    def _watchdog_check(self) -> None:
        bus = self._bus_manager
        if bus is None:
            return
        now = monotonic()
        threshold = self._watchdog_threshold
        cooldown = self._watchdog_cooldown
        changed = False
        stale: list[tuple[int, float]] = []
        esc_ids = set(self._motors.keys()) | set(self._telemetry.keys())
        for esc_id in esc_ids:
            info = self._motors.get(esc_id)
            telemetry = self._telemetry.get(esc_id)
            last_seen: float | None = None
            if info is not None:
                last_seen = info.last_seen
            if telemetry is not None:
                last_seen = (
                    telemetry.timestamp
                    if last_seen is None
                    else max(last_seen, telemetry.timestamp)
                )
            if last_seen is None:
                continue
            age = now - last_seen
            if age < threshold:
                if esc_id in self._watchdog_tripped:
                    self._watchdog_tripped.discard(esc_id)
                    changed = True
                continue
            if esc_id not in self._watchdog_tripped:
                self._watchdog_tripped.add(esc_id)
                changed = True
            last_disable = self._watchdog_last_disable.get(esc_id)
            if last_disable is not None and (now - last_disable) < cooldown:
                continue
            stale.append((esc_id, age))
        if not stale:
            if changed and self._mounted:
                self._refresh_motor_table()
                self._refresh_detail_panel()
            return
        for esc_id, age in stale:
            try:
                disable(bus, esc_id)
            except BusManagerError as exc:  # pragma: no cover - hardware dependent
                self._log(f"[red]Watchdog disable failed for ESC 0x{esc_id:02X}:[/red] {exc}")
            else:
                self._log(
                    f"[red]Watchdog:[/red] ESC 0x{esc_id:02X} stale ({age:0.1f}s); issued disable."
                )
            self._watchdog_last_disable[esc_id] = now
        if self._mounted:
            self._refresh_motor_table()
            self._refresh_detail_panel()

    def _discovery_worker(self, force_active: bool) -> None:
        try:
            bus = self._bus_manager
            if bus is None:
                return
            motors = passive_sniff(bus, duration=0.6)
            if (not motors or force_active) and bus is not None:
                motors.extend(active_probe(bus))
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
        self._reapply_filters()
        if config_changed:
            self._persist_config()

    def _ingest_feedback(self, esc_id: int, feedback: Feedback, mst_id: int, timestamp: float) -> None:
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

        if self._maybe_update_limits(record):
            config_changed = True

        limits = self._resolve_limits(esc_id)
        engineering = feedback.to_engineering(
            p_max=limits[0],
            v_max=limits[1],
            t_max=limits[2],
        )
        telemetry_record = TelemetryRecord(
            feedback=feedback,
            timestamp=timestamp,
            position_rad=engineering.position_rad,
            velocity_rad_s=engineering.velocity_rad_s,
            torque_nm=engineering.torque_nm,
        )
        self._telemetry[esc_id] = telemetry_record
        if esc_id in self._watchdog_tripped:
            self._watchdog_tripped.discard(esc_id)
        self._watchdog_last_disable.pop(esc_id, None)
        writer = self._ensure_telemetry_log()
        if writer is not None:
            try:
                from . import logging as telemetry_logging

                row = telemetry_logging.telemetry_row_from_engineering(
                    engineering,
                    mst_id=mst_id,
                    timestamp=timestamp,
                )
                writer.write_row(row)
            except Exception as exc:  # pragma: no cover - depends on filesystem
                self._log(f"[red]Telemetry log write failed:[/red] {exc}")
                self._close_telemetry_log(mark_error=True)
        velocity_history = self._telemetry_history.setdefault(esc_id, deque(maxlen=200))
        velocity_history.append(engineering.velocity_rad_s)
        torque_history = self._torque_history.setdefault(esc_id, deque(maxlen=200))
        torque_history.append(engineering.torque_nm)
        temp_history = self._temp_history.setdefault(esc_id, deque(maxlen=200))
        temp_history.append(feedback.temp_mos)
        self._motors[esc_id] = MotorInfo(esc_id=esc_id, mst_id=mst_id, last_seen=timestamp)
        if self.selected_esc is None:
            self.selected_esc = esc_id
        if self._mounted:
            self._refresh_motor_table()
            self._refresh_telemetry_panel()
            self._refresh_detail_panel()
            self._refresh_velocity_sparkline()
            self._reapply_filters()
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
        table.update_rows(
            self._motors,
            self._motor_records,
            monotonic(),
            telemetry=self._telemetry,
            watchdog_tripped=self._watchdog_tripped,
            last_disable=self._watchdog_last_disable,
        )
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
            self._refresh_velocity_sparkline()
            self._refresh_control_panel()
            return
        panel.show_details(
            esc_id=esc_id,
            record=self._motor_records.get(esc_id),
            info=self._motors.get(esc_id),
            telemetry=self._telemetry.get(esc_id),
            now=monotonic(),
            watchdog_active=esc_id in self._watchdog_tripped,
            watchdog_last=self._watchdog_last_disable.get(esc_id),
        )
        self._refresh_velocity_sparkline()
        self._refresh_control_panel()

    def _refresh_telemetry_panel(self) -> None:
        panel = self.query_one(TelemetryPanel)
        panel.update_rows(self._telemetry, monotonic())

    def _refresh_group_panel(self) -> None:
        panel = self.query_one(GroupPanel)
        panel.update_groups(self._groups)

    def _refresh_velocity_sparkline(self) -> None:
        panel = self.query_one(VelocitySparkline)
        esc_id = self.selected_esc
        history = self._telemetry_history.get(esc_id, []) if esc_id is not None else []
        panel.update_series(esc_id, history)

    def _refresh_hint_panel(self) -> None:
        panel = self.query_one(HintPanel)
        panel.update_hints(self.active_bus, self.selected_esc)

    def _refresh_control_panel(self) -> None:
        if not self._mounted:
            return
        try:
            panel = self.query_one(MotorControlPanel)
        except (LookupError, NoMatches):
            return
        panel.update_controls(self.selected_esc, bus_online=self._bus_manager is not None)

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
        self._telemetry_history.clear()
        self._torque_history.clear()
        self._temp_history.clear()
        self._watchdog_tripped.clear()
        self._watchdog_last_disable.clear()
        manager.register_listener(self._handle_bus_message)
        self._reapply_filters()
        self._log(f"Connected to {channel}.")
        self._update_bus_stats({"state": "Initializing"})
        self._refresh_control_panel()

    def _close_bus(self) -> None:
        if self._bus_manager is not None:
            try:
                self._bus_manager.unregister_listener(self._handle_bus_message)
            except Exception:
                pass
            self._bus_manager.close()
            self._bus_manager = None
        self._refresh_control_panel()

    def _ensure_telemetry_log(self) -> "TelemetryCsvWriter | None":
        if self._telemetry_log_writer is not None:
            return self._telemetry_log_writer
        if self._telemetry_log_error:
            return None
        try:
            from . import logging as telemetry_logging

            self._telemetry_log_writer = telemetry_logging.open_csv(self._telemetry_log_path)
        except Exception as exc:  # pragma: no cover - filesystem/permissions dependent
            self._telemetry_log_error = True
            self._log(f"[red]Telemetry log unavailable:[/red] {exc}")
            self._telemetry_log_writer = None
        return self._telemetry_log_writer

    def _close_telemetry_log(self, *, mark_error: bool = False) -> None:
        writer = self._telemetry_log_writer
        if writer is None:
            return
        self._telemetry_log_writer = None
        try:
            writer.close()
        except Exception as exc:  # pragma: no cover - depends on filesystem
            self._log(f"[red]Telemetry log close failed:[/red] {exc}")
            self._telemetry_log_error = True
        else:
            if mark_error:
                self._telemetry_log_error = True

    def _persist_config(self) -> None:
        self._config.motors = list(self._motor_records.values())
        self._config.active_bus = self.active_bus
        self._cleanup_empty_groups()
        self._config.groups = [
            GroupRecord(name=name, esc_ids=sorted(set(record.esc_ids)))
            for name, record in sorted(self._groups.items())
        ]
        save_config(self._config, self._config_path)

    def _log(self, message: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        text = f"[{timestamp}] {message}"
        try:
            log = self.query_one(ActivityLog)
        except (LookupError, ScreenStackError):
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

    def _maybe_update_limits(self, record: MotorRecord) -> bool:
        esc_id = record.esc_id
        metadata = record.metadata
        if _has_limit_metadata(metadata):
            self._limits_loaded.add(esc_id)
            return False
        if esc_id in self._limit_errors:
            return False
        bus = self._bus_manager
        if bus is None:
            return False
        try:
            refresh_params(bus, esc_id)
        except BusManagerError as exc:  # pragma: no cover - hardware dependent
            if esc_id not in self._limit_errors:
                self._log(
                    f"[yellow]Warning:[/yellow] Refresh limits failed for ESC 0x{esc_id:02X}: {exc}"
                )
            self._limit_errors.add(esc_id)
            return False
        try:
            p_max = _coerce_positive(
                read_param_float(bus, esc_id, params.RID_P_MAX, timeout=0.3),
                DEFAULT_P_MAX,
            )
            v_max = _coerce_positive(
                read_param_float(bus, esc_id, params.RID_V_MAX, timeout=0.3),
                DEFAULT_V_MAX,
            )
            t_max = _coerce_positive(
                read_param_float(bus, esc_id, params.RID_T_MAX, timeout=0.3),
                DEFAULT_T_MAX,
            )
        except BusManagerError as exc:  # pragma: no cover - hardware dependent
            if esc_id not in self._limit_errors:
                self._log(
                    f"[yellow]Warning:[/yellow] RID reads failed for ESC 0x{esc_id:02X}: {exc}"
                )
            self._limit_errors.add(esc_id)
            return False
        metadata.update({"p_max": p_max, "v_max": v_max, "t_max": t_max})
        self._limits_loaded.add(esc_id)
        self._limit_errors.discard(esc_id)
        return True

    def _resolve_limits(self, esc_id: int) -> tuple[float, float, float]:
        record = self._motor_records.get(esc_id)
        metadata = record.metadata if record else {}
        p_max = _coerce_positive(metadata.get("p_max", metadata.get("P_MAX")), DEFAULT_P_MAX)
        v_max = _coerce_positive(metadata.get("v_max", metadata.get("V_MAX")), DEFAULT_V_MAX)
        t_max = _coerce_positive(metadata.get("t_max", metadata.get("T_MAX")), DEFAULT_T_MAX)
        return p_max, v_max, t_max

    def _resolve_mit_limits(self, esc_id: int) -> tuple[float, float, float, float, float]:
        p_max, v_max, t_max = self._resolve_limits(esc_id)
        record = self._motor_records.get(esc_id)
        metadata = record.metadata if record else {}

        kp_source = metadata.get("kp_max", metadata.get("KP_MAX"))
        kd_source = metadata.get("kd_max", metadata.get("KD_MAX"))
        kp_max = _coerce_positive(kp_source, DEFAULT_KP_MAX)
        kd_max = _coerce_positive(kd_source, DEFAULT_KD_MAX)
        return p_max, v_max, t_max, kp_max, kd_max

    def _reapply_filters(self) -> None:
        if not self._bus_manager:
            return
        mst_ids = [record.mst_id for record in self._motor_records.values() if record.mst_id]
        if not mst_ids:
            return
        try:
            self._bus_manager.set_filters(protocol.build_filters(mst_ids))
        except BusManagerError as exc:  # pragma: no cover
            self._log(f"[yellow]Warning:[/yellow] failed to apply filters: {exc}")

    def _cleanup_empty_groups(self) -> None:
        empty = [name for name, group in self._groups.items() if not group.esc_ids]
        for name in empty:
            del self._groups[name]

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        table = getattr(event, "data_table", None)
        if table is None:
            table = getattr(event, "control", None)
        if table is not None and table.id != "motor-table":
            return
        try:
            esc_id = int(event.row_key)
        except (TypeError, ValueError):
            return
        self.selected_esc = esc_id

    def action_manage_groups(self) -> None:
        modal = GroupModal(self._groups)
        self.push_screen(modal, callback=self._apply_group_definition)

    def get_commands(self) -> Iterable[Command]:  # pragma: no cover - UI integration
        commands = [
            ("Enable Selected", "Enable the selected motor.", self.action_enable_selected),
            ("Disable Selected", "Disable the selected motor.", self.action_disable_selected),
            ("Zero Selected", "Send zero-position command to the selected motor.", self.action_zero_selected),
            ("Set Velocity", "Prompt for a velocity setpoint for the selected motor.", self.action_set_velocity),
            ("MIT Command", "Send MIT-mode command to the selected motor.", self.action_set_mit),
            ("Launch Demo", "Open demo launcher dialog.", self.action_launch_demo),
            ("Stop Demo", "Stop any running demo and disable motors.", self.action_stop_demo),
            ("Group Actions", "Run enable/disable/velocity against a group.", self.action_prompt_group_action),
            ("Save Config", "Persist current configuration to disk.", self.action_save_config),
            ("Trigger Discovery", "Run passive + active motor discovery.", self.action_trigger_discovery),
        ]
        for text, help_text, func in commands:
            yield Command(
                text,
                DiscoveryHit(
                    text,
                    lambda func=func: self.call_from_thread(func),
                    help=help_text,
                ),
            )


def run(config_path: Path | None = None) -> None:
    """Convenience shim to launch the Textual app."""

    DmTuiApp(config_path=config_path).run()


if __name__ == "__main__":
    run()
