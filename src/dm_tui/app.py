"""Textual entry point for dm-tui.

This initial scaffold boots a minimal Textual application so we can
iterate on layouts while back-end modules evolve.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import Footer, Header, Static

from .persistence import AppConfig, load_config


class StatusPanel(Static):
    """Placeholder widget until live telemetry panels are implemented."""

    DEFAULT_CSS = """StatusPanel { height: 100%; }"""

    def __init__(self, title: str, *, id: str | None = None) -> None:
        super().__init__(f"[b]{title}[/b]\nWaiting for hardware...", id=id)


class DmTuiApp(App[None]):
    """Minimal Textual application shell for dm-tui."""

    TITLE = "dm-tui"
    CSS_PATH: Iterable[Path] | Path | None = None
    BINDINGS = [
        Binding("ctrl+c", "quit", "Quit"),
        Binding("q", "quit", "Quit"),
        Binding("r", "reload", "Reload"),
    ]

    def __init__(self, config_path: Path | None = None) -> None:
        super().__init__()
        self._config_path = config_path
        self._config: AppConfig | None = None

    def on_mount(self) -> None:  # noqa: D401 - Textual lifecycle method.
        """Load the persisted configuration when the app starts."""
        self._config = load_config(self._config_path)
        self.query_one(StatusPanel, id="overview").update(
            "\n".join(
                [
                    "[b]Configured Buses[/b]",
                    *[
                        f"• {bus.channel} @ {bus.bitrate/1000:.0f} kbps"
                        for bus in self._config.buses
                    ],
                    "",
                    "[b]Tracked Motors[/b]",
                    "None yet" if not self._config.motors else "",
                ]
            )
        )

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="content"):
            yield StatusPanel("Overview", id="overview")
            yield StatusPanel("Bus Health", id="bus")
            yield StatusPanel("Activity Log", id="log")
        yield Footer()


def run(config_path: Path | None = None) -> None:
    """Convenience shim to launch the Textual app."""
    DmTuiApp(config_path=config_path).run()


if __name__ == "__main__":
    run()
