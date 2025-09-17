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
6. Cycle CAN buses with `B`, trigger discovery with `R`, and persist config updates via `Ctrl+S`.

### Testing Strategy
- Unit tests on protocol packers, MIT bit packing, and feedback decoding using `vcan`.
- Loopback smoke tests with `cangen`/`candump` to confirm filters and reader behavior.
- Hardware validation checklist: ±30/40 rpm sweeps, synchronized braking, MIT mode responsiveness.

See [`docs/TESTING.md`](docs/TESTING.md) for step-by-step commands, including a `vcan` loopback recipe.

## Safety Considerations
- All command frames must be 8 bytes—drives ignore shorter DLC values.
- Always set `CTRL_MODE=3` before issuing velocity commands.
- Watch for SocketCAN “No buffer space available” errors—usually indicates no ACK or queue saturation.
- Use the built-in bus health screen to monitor error counters and interface state.

## Roadmap Snapshot
See [`docs/ROADMAP.md`](docs/ROADMAP.md) for detailed milestones (environment setup, protocol core, discovery UX, monitoring/control screens, demo polish, release prep).

## Contributing
- Feature branches via SSH remote `git@github.com:alpharover/damiao_motor_tui.git`.
- Update documentation alongside feature work.
- Coordinate major UI changes with the roadmap owner before implementation.

## License
Project license TBD. Until finalized, treat the code as © 2025 alpharover, all rights reserved.
