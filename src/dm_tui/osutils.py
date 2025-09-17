"""Operating system helpers for dm-tui."""

from __future__ import annotations

import re
import subprocess
from typing import Any, Dict


def read_bus_statistics(channel: str) -> Dict[str, Any]:
    """Return parsed statistics for *channel* via `ip -details -statistics`.

    Raises RuntimeError if the command fails or the interface is missing.
    """

    result = subprocess.run(
        ["ip", "-details", "-statistics", "link", "show", channel],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        message = result.stderr.strip() or f"ip exited with code {result.returncode}"
        raise RuntimeError(message)
    return _parse_can_statistics(result.stdout)


def _parse_can_statistics(output: str) -> Dict[str, Any]:
    stats: Dict[str, Any] = {"raw": output.strip()}
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    for index, line in enumerate(lines):
        if ":" in line and "state" in line and " qlen " in line:
            parts = line.split()
            if "state" in parts:
                stats["oper_state"] = parts[parts.index("state") + 1]
            if "qlen" in parts:
                try:
                    stats["tx_queue_len"] = int(parts[parts.index("qlen") + 1])
                except ValueError:
                    stats["tx_queue_len"] = parts[parts.index("qlen") + 1]
        elif line.startswith("can ") and "state" in line:
            state_match = re.search(r"state\s+([A-Z-]+)", line)
            if state_match:
                stats.setdefault("state", state_match.group(1))
            berr_match = re.search(r"tx\s+(\d+)\s+rx\s+(\d+)", line)
            if berr_match:
                stats["tx_errors"] = int(berr_match.group(1))
                stats["rx_errors"] = int(berr_match.group(2))
        elif line.startswith("bitrate"):
            bitrate_match = re.search(r"bitrate\s+(\d+)", line)
            if bitrate_match:
                stats["bitrate"] = int(bitrate_match.group(1))
        elif line.startswith("RX:"):
            values_line = lines[index + 1] if index + 1 < len(lines) else ""
            stats.update(_parse_counter_line("rx_", line[3:], values_line))
        elif line.startswith("TX:"):
            values_line = lines[index + 1] if index + 1 < len(lines) else ""
            stats.update(_parse_counter_line("tx_", line[3:], values_line))
    return stats


def _parse_counter_line(prefix: str, labels_line: str, values_line: str) -> Dict[str, Any]:
    labels = labels_line.strip().split()
    values = values_line.strip().split()
    parsed: Dict[str, Any] = {}
    for label, value in zip(labels, values):
        key = prefix + label.lower()
        try:
            parsed[key] = int(value)
        except ValueError:
            parsed[key] = value
    return parsed


__all__ = ["read_bus_statistics"]
