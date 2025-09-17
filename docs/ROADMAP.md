# dm-tui Project Roadmap

_Last updated: 2025-09-17_

## Vision
Deliver a headless Textual-based terminal UI ("dm-tui") that discovers, configures, commands, and showcases Damiao gear motors attached to the Raspberry Pi 5 CAN stack. The tool must feel reliable for lab operators, offer impressive demo sequences, and remain safe for shared bench use.

## Guiding Principles
- **Hardware first**: respect the dual-channel SocketCAN setup (canA/canB) and maintain safe defaults (motors disabled unless explicitly commanded).
- **Operator trust**: provide clear status, fast access to an E-STOP, and actionable fault messages.
- **Extensibility**: keep CAN protocol helpers, discovery logic, and UI loosely coupled for future automation or web frontends.

## Milestones

### Milestone 1 – Environment Foundations (Week 1)
- [ ] Initialize Python project structure with Textual + python-can dependencies.
- [ ] Implement BusManager abstraction (open/close bus, filters, periodic send, notifier hookup).
- [ ] Provide configuration file loading/saving (YAML/TOML) for channel defaults and motor registry.
- [ ] Validate SocketCAN interactions using `vcan` loopback tests.

### Milestone 2 – Protocol & Safety Core (Week 2)
- [ ] Implement frame pack/unpack utilities for enable/disable/zero, speed, position-speed, and MIT.
- [ ] Decode feedback frames into engineering units leveraging RID-configured limits.
- [ ] Implement RID read/write/save helpers for ID assignment and mode control.
- [ ] Integrate global E-STOP and watchdog (highlight stale feedback, auto-disable if needed).

### Milestone 3 – Discovery & Configuration UX (Week 3)
- [ ] Passive sniff logic recognizing motors by feedback signature.
- [ ] Gentle active probe (0 rad/s) workflow to locate quiet motors.
- [ ] ID assignment wizard writing RIDs 7/8/10 safely, persisting via SAVE (0xAA).
- [ ] Motor registry management (names, group tags, metadata) in persistence layer.

### Milestone 4 – Monitoring & Control Screens (Week 4)
- [ ] Textual screen for bus health (bitrate, error counters, txqueuelen guidance).
- [ ] Live monitor panel with trendlines/metrics for position, velocity, torque, temps.
- [ ] Control screen with per-motor enable/disable, zero, velocity and MIT controls.
- [ ] Group command support and command palette bindings (`Space`, `e`, `d`, `r`, `g`).

### Milestone 5 – Demo Experiences & Logging (Week 5)
- [ ] Implement sine orchestra, handshake duet, and figure-8 demos via `send_periodic` handles.
- [ ] Provide demo control overlay with start/stop, amplitude/frequency tweaks, and E-STOP integration.
- [ ] Add CSV telemetry logging and optional `candump` capture integration.
- [ ] Document procedures for hardware validation (±30/40 rpm sweeps, braking, MIT taste test).

### Milestone 6 – Polish & Release (Week 6)
- [ ] Harden error handling (`No buffer space`, ERR nibble decoding, bus state changes).
- [ ] Add system configuration helpers (bitrate bring-up tips, `txqueuelen` suggestions).
- [ ] Write automated tests for protocol packers, discovery logic (vcan), and config persistence.
- [ ] Prepare packaging instructions (pip install, entry point script) and publish initial GitHub release.

## Cross-Cutting Tasks
- **Documentation**: Keep README, AGENTS.md, and usage docs refreshed each milestone.
- **Testing**: Maintain unit tests (pytest) plus hardware checklists. Track coverage of critical protocol paths.
- **CI/CD**: Plan to add GitHub Actions (lint + tests) once codebase stabilizes.

## Risks & Mitigations
- **Bus contention or wiring faults** → surface error counters in UI, provide troubleshooting guide.
- **Mode mismatches** → enforce CTRL_MODE writes before sending velocity commands.
- **Periodic task drift** → rely on python-can Broadcast Manager and monitor schedule jitter.
- **Operator confusion** → deliver meaningful notifications, inline help, and top-level E-STOP.

## Success Criteria
- Motors auto-discovered and configurable without manual `dm_id_tool.py` usage.
- Console operators can run demos, monitor telemetry, and recover from faults in under 30 seconds.
- Repository includes tests, docs, and packaging steps enabling new collaborators to contribute within a day.
