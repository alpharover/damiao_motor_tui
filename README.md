# dm-tui

Configuration and control utility for Damiao QDD motors.

Textual-powered terminal UI for discovering, configuring, and commanding Damiao DM-J4340-2EC motors over SocketCAN on Raspberry Pi 5.

## Highlights
- Discovers up to eight motors across `canA`/`canB`, with passive sniffing and safe 0 rad/s probing.
- Provides ID assignment wizard (RID writes + SAVE) and tracks motor registry metadata.
- Real-time telemetry: position, velocity, torque, temperatures, fault status, and bus health counters.
- Control panels for velocity, position-speed, and MIT modes with group commands and global E-STOP.
- Demo choreographies (sine orchestra, duet handshake, figure-8) built on python-can Broadcast Manager.
- CSV telemetry logging and optional `candump` capture for deep analysis.
- Headless Textual UI with live bus diagnostics, discovery triggers, and keyboard E-STOP for quick triage.
- ID assignment wizard writes ESC/MST IDs + CTRL_MODE via 0x7FF management frames, with metadata editor and group management built in.
- Motor detail pane with adjacent control panel (enable/disable/zero/velocity/MIT) and live telemetry summaries.

## Architecture Overview
```
dm_tui/
  app.py              # Textual app bootstrap & screen routing
  bus_manager.py      # SocketCAN wrapper (filters, notifier, periodic send)
  discovery.py        # Passive/active motor discovery & ID wizard helpers
  controllers.py      # High-level motor actions (enable, disable, groups)
  demos.py            # Sine, duet, figure-8 periodic command sequences
  dmlib/
    protocol.py       # Frame packers (enable/disable, speed, MIT, feedback decode)
    params.py         # RID read/write/save helpers
  persistence.py      # YAML/TOML config & motor registry I/O
  logging.py          # CSV logger and candump integration
  screens/
    bus.py, monitor.py, control.py, demos.py, settings.py, logs.py
```

## Hardware Baseline
- Raspberry Pi 5, Ubuntu 24.04, Waveshare 2-CH CAN HAT+ on SPI1 (`spi1-3cs` overlay).
- Systemd `.link` files map `canA` (SPI1 CE1) and `canB` (SPI1 CE2) for predictable naming.
- Motors ship with 1 Mbps CAN, default `ESC_ID`/`MST_ID` pairs (`0x01/0x11`, `0x02/0x12` currently in bench setup).
- 2025‑09‑17 note: manual enable/velocity/disable frames emitted on `canB` produced no motor motion and `candump` showed no traffic; investigate wiring/termination or competing CAN clients before the next bench run.

## Setup
```bash
# OS packages
sudo apt update
sudo apt install -y python3-pip can-utils

# Python dependencies (use venv if desired)
pip install python-can textual rich
```

Bring up CAN channels as needed:
```bash
sudo ip link set canB up type can bitrate 1000000
sudo ip link set canA up type can bitrate 1000000  # optional second bus
```

Increase transmit queue depth if demo traffic saturates the bus:
```bash
sudo ifconfig canB txqueuelen 65536
```

## Development Workflow
1. Read `docs/ROADMAP.md` for current milestone priorities.
2. Launch the TUI (entry point script TBD; for now run `python -m dm_tui.app`).
3. Use passive discovery first. If no motors appear, start an active probe (safe 0 rad/s cycle).
4. Configure IDs via the wizard (writes RIDs 7/8/10, saves with 0xAA) before issuing motion commands.
5. Leverage global E-STOP (`Space`) before editing demo scripts or periodic tasks.
6. Cycle CAN buses with `B`, trigger discovery with `R` (safe active probe fallback), use `E/D/Z` to enable/disable/zero the highlighted motor, `V` for velocity prompts, `T` for the MIT setpoint modal, `A` to run the ID assignment wizard, `M` to edit metadata (name, limits, group), `G` to manage groups, and persist config updates via `Ctrl+S`.

## Groups & Demos
- Tag motors with friendly names, limits, and group memberships via `M` (metadata modal). Groups persist in the YAML config and appear in the right-hand panel.
- Use `Ctrl+G` to run group actions (enable, disable, broadcast velocity) and `Ctrl+M` to edit group membership lists directly.
- Launch choreographed demos with `Ctrl+D` (Sine Orchestra, Handshake Duet, Figure Eight). Demos drive phase-offset velocity profiles across the chosen group or the entire bench and can be halted with `Ctrl+Shift+D`.
- All demo traffic runs through SocketCAN in real time; the telemetry panel and motor detail view update continuously for quick health checks.
- Demo engine uses `python-can` periodic tasks (Broadcast Manager) for low-jitter playback while payloads are updated in place.
- Velocity sparkline: the left column now includes a history plot of the highlighted motor’s velocity (rolling window of recent samples) for quick trend spotting.
- The command palette (`:`) exposes quick actions for launching/stopping demos, triggering discovery, running group actions, and saving configuration.

### Testing Strategy
- Unit tests on protocol packers, MIT bit packing, and feedback decoding using `vcan`.
- Loopback smoke tests with `cangen`/`candump` to confirm filters and reader behavior.
- Hardware validation checklist covering ±30/40 rpm sweeps, braking, and MIT responsiveness—see [`docs/HARDWARE_VALIDATION.md`](docs/HARDWARE_VALIDATION.md).

See [`docs/TESTING.md`](docs/TESTING.md) for step-by-step commands, including a `vcan` loopback recipe.

## Hardware Validation
After completing the software checks in [`docs/TESTING.md`](docs/TESTING.md), run the bench procedures in [`docs/HARDWARE_VALIDATION.md`](docs/HARDWARE_VALIDATION.md) to confirm safe motion before demos. The guide covers the ±30/40 rpm velocity sweeps, braking confidence drills, and the MIT “taste test” mentioned in the roadmap.

## Safety Considerations
- All command frames must be 8 bytes—drives ignore shorter DLC values.
- Always set `CTRL_MODE=3` before issuing velocity commands.
- Watch for SocketCAN “No buffer space available” errors—usually indicates no ACK or queue saturation.
- Use the built-in bus health screen to monitor error counters and interface state.
- A watchdog monitors telemetry age and automatically issues `disable` to any ESC whose feedback stops for more than a few
  seconds. Watchdog events are called out in the motor table, detail pane, and activity log so operators can spot the
  intervention immediately.
- Tune watchdog behaviour via environment variables before launching the TUI:
  - `DM_TUI_WATCHDOG_THRESHOLD` (seconds of inactivity before a motor is flagged, default `3.0`).
  - `DM_TUI_WATCHDOG_COOLDOWN` (minimum seconds between repeated disable commands per ESC, default `5.0`).
  - `DM_TUI_WATCHDOG_INTERVAL` (poll period for the watchdog scan, default `1.0`).

## Roadmap Snapshot
See [`docs/ROADMAP.md`](docs/ROADMAP.md) for detailed milestones (environment setup, protocol core, discovery UX, monitoring/control screens, demo polish, release prep).

## Contributing
- Feature branches via SSH remote `git@github.com:alpharover/damiao_motor_tui.git`.
- Update documentation alongside feature work.
- Coordinate major UI changes with the roadmap owner before implementation.

## License
Project license TBD. Until finalized, treat the code as © 2025 alpharover, all rights reserved.
