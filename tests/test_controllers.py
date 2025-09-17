from dm_tui.controllers import (
    assign_motor_ids,
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
