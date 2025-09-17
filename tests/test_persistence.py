from pathlib import Path

from dm_tui.persistence import AppConfig, BusConfig, MotorRecord, load_config, save_config


def test_load_config_creates_defaults_when_missing(tmp_path: Path):
    cfg_path = tmp_path / "config.yaml"
    config = load_config(cfg_path)
    assert config.buses[0].channel == "canB"
    assert config.motors == []


def test_save_and_reload_round_trip(tmp_path: Path):
    cfg_path = tmp_path / "config.yaml"
    config = AppConfig(
        buses=[BusConfig(channel="canA", bitrate=500000)],
        motors=[MotorRecord(esc_id=1, mst_id=0x11, name="motor-a")],
    )
    save_config(config, cfg_path)
    reloaded = load_config(cfg_path)
    assert reloaded.buses[0].channel == "canA"
    assert reloaded.buses[0].bitrate == 500000
    assert reloaded.motors[0].name == "motor-a"
