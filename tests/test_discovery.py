from types import SimpleNamespace

from dm_tui.discovery import active_probe


def _feedback_frame(esc_id: int) -> bytes:
    # status nibble 1, ESC ID low nibble
    status_byte = (1 << 4) | (esc_id & 0x0F)
    # zeroed counts with temps 40/41
    return bytes([status_byte, 0, 0, 0, 0, 0, 40, 41])


class FakeBus:
    def __init__(self, messages):
        self.messages = list(messages)
        self.sent = []

    def send(self, arb_id, data):
        self.sent.append((arb_id, data))

    def get_message(self, timeout=None):
        if self.messages:
            return self.messages.pop(0)
        return None


def test_active_probe_identifies_motor():
    frame = _feedback_frame(esc_id=2)
    msg = SimpleNamespace(arbitration_id=0x12, data=frame)
    bus = FakeBus([msg])
    discovered = active_probe(bus, esc_candidates=[2], probe_duration=0.05)
    assert len(discovered) == 1
    entry = discovered[0]
    assert entry.esc_id == 2
    assert entry.mst_id == 0x12
    assert bus.sent[0][0] == 2  # disable frame to ESC ID
