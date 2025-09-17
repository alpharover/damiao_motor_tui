from dm_tui.controllers import command_velocity, disable, enable, zero


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
