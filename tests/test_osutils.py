from dm_tui.osutils import _parse_can_statistics


SAMPLE = """
4: canB: <NOARP,UP,LOWER_UP> mtu 72 qdisc noop state UP mode DEFAULT group default qlen 1000
    link/can  promiscuity 0
    can state ERROR-ACTIVE (berr-counter tx 1 rx 2) restart-ms 0
          bitrate 1000000 sample-point 0.875
          tq 125 prop-seg 6 phase-seg1 7 phase-seg2 2 sjw 1
    RX: bytes  packets  errors  dropped overrun mcast
    10         3        0       0       0      0
    TX: bytes  packets  errors  dropped carrier collsns
    5          2        1       0       0       0
"""


def test_parse_can_statistics_extracts_key_fields():
    stats = _parse_can_statistics(SAMPLE)
    assert stats["oper_state"] == "UP"
    assert stats["state"] == "ERROR-ACTIVE"
    assert stats["bitrate"] == 1_000_000
    assert stats["tx_errors"] == 1
    assert stats["rx_packets"] == 3
    assert stats["tx_packets"] == 2
    assert stats["tx_queue_len"] == 1000
