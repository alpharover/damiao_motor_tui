# Hardware Validation Guide

This checklist verifies that dm-tui can safely command the Damiao bench before demos or milestone sign-off. Run it after completing the software checks in [`docs/TESTING.md`](TESTING.md) and before inviting observers onto the bench.

## Safety Prerequisites
- **Bench readiness**: Motors mounted securely, harness strain relieved, and work area clear of loose tools.
- **Emergency stop**: Space bar E-STOP confirmed within reach; external kill switch accessible if fitted.
- **Power sequencing**: Supplies off while connecting CAN and power leads. Bring up low-voltage logic before energising the drives.
- **Interface state**: `canB` (and `canA` if used) enabled with expected bitrate, no TX/RX errors on the bus health screen.
- **Motor status**: Motors disabled in the UI. Zero offsets validated after any mechanical adjustments.
- **Observer briefing**: Anyone nearby understands the enable/disable shortcuts (`E`, `D`, `Space`) and the plan for this validation.

## Pre-Test Sanity Checks
1. Complete the unit and `vcan` smoke tests from [`docs/TESTING.md`](TESTING.md).
2. Start dm-tui on the target channel and confirm discovery populates the expected motors.
3. Review live telemetry for idle noise (velocity < 1 rpm, torque near 0 Nm). Investigate anomalies before proceeding.
4. Arm data capture (CSV logging or external DAQ) if trend data is required for the project milestone.

## ±30 rpm Velocity Sweep
Purpose: Confirm low-speed command tracking without overshoot.

1. Enable a single motor via the control screen.
2. Command +30 rpm (≈3.14 rad/s). Hold for 10 s while observing velocity and torque traces.
3. Command –30 rpm for another 10 s. Verify symmetric response and no unexpected alarms.
4. Criteria: Steady-state velocity within ±1 rpm of the setpoint, torque ripple acceptable for the application, and no bus errors.
5. Repeat for each attached motor, documenting observations in the lab log.

## ±40 rpm Velocity Sweep
Purpose: Validate margin above demo speeds and confirm stability near the roadmap’s showcase targets.

1. With the same motor enabled, issue +40 rpm (≈4.19 rad/s) for 10 s.
2. Transition directly to –40 rpm for 10 s using the velocity prompt or scripted command.
3. Criteria: Smooth reversal without watchdog triggers, thermal rise acceptable (<5 °C if sensor data available), and no audible gear chatter.
4. If multiple motors will run coordinated demos, repeat with the group enabled to check synchronisation.

## Braking & Disable Checks
Purpose: Ensure operators can arrest motion quickly and predictably.

1. While holding +30 rpm, send a 0 rpm command. Verify the motor decelerates to rest within the expected braking window and remains stable.
2. From +30 rpm, issue the disable command (`D`). Confirm the drive coasts safely without faulting the CAN bus.
3. Trigger the global E-STOP (`Space`) during a +40 rpm command. Ensure all drives drop torque immediately and the UI reports the latched state.
4. Re-enable after each test only once the shaft is stationary and observers acknowledge readiness.

## MIT Responsiveness ("Taste Test")
Purpose: Confirm MIT (impedance) mode is configured and responsive for interactive demos.

1. Switch the motor to MIT mode via the control screen, ensuring `CTRL_MODE` is set correctly.
2. Apply a small stiffness command (e.g., 5 Nm/rad) with zero velocity feed-forward and minimal damping.
3. Gently deflect the output by hand. The motor should provide proportional restoring torque without oscillation.
4. Step the target position by ±5 degrees via the UI or scripted command. Verify the motor tracks within 1 degree with prompt settling (<0.5 s).
5. Increase stiffness incrementally to the planned demo value, repeating the deflection check while monitoring torque limits.
6. Exit MIT mode, return to velocity control, and disable the motor when finished.

## Post-Test Wrap-Up
- Save telemetry logs and note pass/fail status per motor in the bench journal.
- Inspect for loose connectors or abnormal heat before powering down.
- Update the roadmap milestone or project tracker with any issues uncovered.

Completion of this guide satisfies the roadmap requirement to document hardware validation for ±30/40 rpm sweeps, braking confidence, and MIT responsiveness.
