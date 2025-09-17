# Agent Onboarding Guide

_Last updated: 2025-09-17_

Welcome to the **dm-tui** project. This guide orients any agent working on the Raspberry Pi 5 control stack so you can contribute safely and efficiently.

## Hardware & OS Snapshot
- Platform: Raspberry Pi 5, Ubuntu 24.04 (64-bit).
- CAN Interface: Waveshare 2-CH CAN HAT+ on SPI1 with dual MCP2515 controllers.
- Channels: `canA` (SPI1 CE1, currently idle) and `canB` (SPI1 CE2, both motors active).
- Motors: Damiao DM-J4340-2EC gear motors.
- Sudo: use `echo 9909 | sudo -S <command>` – never prompt the user interactively.

## CAN Configuration Baseline
- `/boot/firmware/config.txt` already enables `dtparam=spi=on`, `dtoverlay=spi1-3cs`, and dual `mcp2515` overlays.
- Predictable naming via systemd `.link` files:
  - `/etc/systemd/network/10-canA.link` → `platform-1f00054000.spi-cs-1` → `canA`.
  - `/etc/systemd/network/11-canB.link` → `platform-1f00054000.spi-cs-2` → `canB`.
- Interfaces start **DOWN**. Bring up with `sudo ip link set canB up type can bitrate 1000000` (same for `canA`).

## Motor IDs & Control Notes
- Motor A: `ESC_ID=0x01`, `MST_ID=0x11`, velocity mode (`RID 10 = 3`).
- Motor B: `ESC_ID=0x02`, `MST_ID=0x12`, velocity mode.
- Control frames: send on `0x200 + ESC_ID` (float32 rad/s + padding). Feedback arrives on `MST_ID`.
- Enable/Disable/Zero frames (DLC=8 exactly):
  - Enable: `FF FF FF FF FF FF FF FC`
  - Disable: `FF FF FF FF FF FF FF FD`
  - Zero position: `FF FF FF FF FF FF FF FE`

## Project Documents
- `docs/ROADMAP.md`: project milestones and strategic priorities set by the architect.
- `README.md`: external facing overview (keep pristine and user-friendly).
- `AGENTS.md` (this file): internal quick start for agents; update when workflows change.

## Development Expectations
1. **Follow the roadmap** – pick tasks aligned with the current milestone before adding new scope.
2. **Safety first** – ensure motors are disabled before modifying discovery or control code; test on `vcan` where possible.
3. **Document changes** – update README/roadmap when you adjust architecture or workflows.
4. **Testing** – maintain unit tests for protocol packers, discovery logic, and persistence. Confirm on hardware for anything that touches motion.
5. **Git Hygiene** – feature branches preferred. Use the repo’s SSH remote (`git@github.com:alpharover/damiao_motor_tui.git`).

### Current TUI Key Bindings
- `Space`: global E-STOP
- `R`: passive + active discovery sweep
- `B`: cycle between configured CAN buses
- `E` / `D` / `Z`: enable, disable, or zero the highlighted motor
- `V`: prompt for a velocity command (rad/s) for the highlighted motor
- `Ctrl+S`: persist configuration to disk

## Useful Utilities
- `python-can`, `can-utils` (`candump`, `cansend`) for debugging.
- Existing helper script: `/home/alpha_pi5/dm_id_tool.py` (legacy ID management).
- Bus health check: `ip -details -statistics link show canB` + `candump canB,011:7FF` / `012`.

## Open Questions / TODOs for Agents
- Automate CAN interface bring-up (systemd-networkd `.network` files).
- Package demo choreographies into reusable modules once core TUI screens are stable.
- Evaluate Textual web mode for remote demos after initial release.

Report unexpected state (hardware faults, config drift) to the maintainer before proceeding. Keep the bench safe and the motors singing.
