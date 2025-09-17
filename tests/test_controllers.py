from dm_tui.controllers import (
    MitTarget,
    assign_motor_ids,
    command_mit,
    command_mit_targets,
    command_velocity,
    disable,
    enable,
    write_param,
    zero,
)


class FakeBus:
    def __init__(self):
        self.sent = []

    def send(self, arb_id, data, **kwargs):
        self.sent.append((arb_id, data))


def test_enable_disable_zero_commands_build_expected_frames():
    bus = FakeBus()
    enable(bus, 1)
    disable(bus, 1)
    zero(bus, 1)
    assert bus.sent[0][0] == 1
    assert bus.sent[1][0] == 1
    assert bus.sent[2][0] == 1
    assert bus.sent[0][1][-1] == 0xFC
    assert bus.sent[1][1][-1] == 0xFD
    assert bus.sent[2][1][-1] == 0xFE


def test_command_velocity_targets_correct_arbitration_id():
    bus = FakeBus()
    command_velocity(bus, 3, 1.23)
    arb_id, data = bus.sent[0]
    assert arb_id == 0x200 + 3
    assert len(data) == 8


def test_command_mit_targets_correct_arbitration_id():
    bus = FakeBus()
    command_mit(
        bus,
        3,
        position_rad=0.5,
        velocity_rad_s=-0.2,
        torque_nm=0.8,
        kp=60.0,
        kd=2.0,
        position_limit=2.0,
        velocity_limit=4.0,
        torque_limit=3.0,
        kp_limit=200.0,
        kd_limit=8.0,
    )
    arb_id, data = bus.sent[0]
    assert arb_id == 0x300 + 3
    assert len(data) == 8


def test_command_mit_targets_iterates_collection():
    bus = FakeBus()
    targets = [
        MitTarget(esc_id=1, position_rad=0.1, velocity_rad_s=0.0, torque_nm=0.0, kp=10.0, kd=1.0),
        MitTarget(esc_id=2, position_rad=-0.1, velocity_rad_s=0.2, torque_nm=0.1, kp=12.0, kd=1.2),
    ]
    command_mit_targets(bus, targets)
    assert len(bus.sent) == 2
    assert bus.sent[0][0] == 0x301
    assert bus.sent[1][0] == 0x302


def test_write_param_targets_management_id():
    bus = FakeBus()
    write_param(bus, 1, 0x08, 0x02)
    arb_id, data = bus.sent[0]
    assert arb_id == 0x7FF
    assert data[0] == 0x55
    assert data[1] == 0x01
    assert data[2] == 0x08


def test_assign_motor_ids_sequences_commands():
    bus = FakeBus()
    assign_motor_ids(bus, current_esc=1, new_esc=2, new_mst=0x12, control_mode=3)
    assert bus.sent[0][0] == 1  # disable current ESC
    assert bus.sent[-1][0] == 0x7FF
