"""Persistence helpers for dm-tui configuration state."""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Iterable

import yaml

DEFAULT_CONFIG_DIR = Path("~/.config/dm_tui").expanduser()
DEFAULT_CONFIG_PATH = DEFAULT_CONFIG_DIR / "config.yaml"


@dataclass(slots=True)
class BusConfig:
    """Configuration describing a SocketCAN interface."""

    channel: str
    bitrate: int = 1_000_000
    filters: list[dict[str, int]] = field(default_factory=list)


@dataclass(slots=True)
class MotorRecord:
    """Persisted metadata for a discovered/configured motor."""

    esc_id: int
    mst_id: int
    name: str | None = None
    group: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class AppConfig:
    """Top-level configuration persisted between dm-tui sessions."""

    buses: list[BusConfig] = field(default_factory=lambda: [BusConfig(channel="canB")])
    motors: list[MotorRecord] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Serialize the config to primitive types for YAML dumping."""
        return {
            "buses": [asdict(bus) for bus in self.buses],
            "motors": [asdict(motor) for motor in self.motors],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AppConfig":
        buses = [BusConfig(**bus) for bus in data.get("buses", [])]
        motors = [MotorRecord(**motor) for motor in data.get("motors", [])]
        if not buses:
            buses = [BusConfig(channel="canB")]
        return cls(buses=buses, motors=motors)


def load_config(path: Path | None = None) -> AppConfig:
    """Load configuration from *path* or fall back to the default location."""
    config_path = path or DEFAULT_CONFIG_PATH
    if not config_path.exists():
        # Ensure parent directory exists so saves succeed later.
        config_path.parent.mkdir(parents=True, exist_ok=True)
        return AppConfig()

    with config_path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    return AppConfig.from_dict(data)


def save_config(config: AppConfig, path: Path | None = None) -> None:
    """Persist *config* as YAML to *path* (defaulting to the standard location)."""
    config_path = path or DEFAULT_CONFIG_PATH
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with config_path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(config.to_dict(), handle, sort_keys=False)


def ensure_bus(config: AppConfig, channel: str, bitrate: int = 1_000_000) -> AppConfig:
    """Return a config with *channel* present, adding it if necessary."""
    if channel not in {bus.channel for bus in config.buses}:
        config.buses.append(BusConfig(channel=channel, bitrate=bitrate))
    return config


def list_config_files(directory: Path | None = None) -> Iterable[Path]:
    """Yield configuration files present in *directory* (defaults to config dir)."""
    config_dir = (directory or DEFAULT_CONFIG_DIR).expanduser()
    if not config_dir.exists():
        return []
    return sorted(config_dir.glob("*.yaml"))
