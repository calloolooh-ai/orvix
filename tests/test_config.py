"""
tests for config.py's settings round-trip: defaults, and that a yaml file
missing newer fields (like multi_monitor, added after some users already had
a config.yaml on disk) doesn't blow up load_config.
"""

from orvix.config import Settings, load_config, save_config


def test_multi_monitor_defaults_to_true():
    assert Settings().multi_monitor is True


def test_save_then_load_round_trips_multi_monitor(tmp_path):
    path = tmp_path / "config.yaml"
    save_config(Settings(multi_monitor=False), path)
    loaded = load_config(path)
    assert loaded.multi_monitor is False


def test_missing_multi_monitor_key_falls_back_to_default(tmp_path):
    # simulates an existing config.yaml written before this field existed
    path = tmp_path / "config.yaml"
    path.write_text("cursor_mode: tilt\n")
    loaded = load_config(path)
    assert loaded.multi_monitor is True
    assert loaded.cursor_mode == "tilt"


def test_load_config_with_no_file_returns_defaults(tmp_path):
    path = tmp_path / "does_not_exist.yaml"
    loaded = load_config(path)
    assert loaded == Settings()


def test_thumbs_up_action_defaults_to_confirm():
    assert Settings().thumbs_up_action == "confirm"


def test_save_then_load_round_trips_thumbs_up_action(tmp_path):
    path = tmp_path / "config.yaml"
    save_config(Settings(thumbs_up_action="undo"), path)
    loaded = load_config(path)
    assert loaded.thumbs_up_action == "undo"
