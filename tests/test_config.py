"""
tests for config.py's settings round-trip: defaults, and that a yaml file
missing newer fields (like multi_monitor, added after some users already had
a config.yaml on disk) doesn't blow up load_config.
"""

import pytest

from orvix.config import (
    Settings,
    delete_profile,
    list_profiles,
    load_config,
    load_profile,
    save_config,
    save_profile,
)


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


def test_list_profiles_empty_when_dir_missing(tmp_path):
    assert list_profiles(tmp_path / "profiles") == []


def test_save_profile_then_list_and_load(tmp_path):
    profiles_dir = tmp_path / "profiles"
    save_profile("demo", Settings(cursor_mode="tilt"), profiles_dir)
    save_profile("precision", Settings(cursor_mode="absolute"), profiles_dir)

    assert list_profiles(profiles_dir) == ["demo", "precision"]
    assert load_profile("demo", profiles_dir).cursor_mode == "tilt"
    assert load_profile("precision", profiles_dir).cursor_mode == "absolute"


def test_load_profile_missing_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_profile("nope", tmp_path / "profiles")


def test_delete_profile(tmp_path):
    profiles_dir = tmp_path / "profiles"
    save_profile("temp", Settings(), profiles_dir)
    assert list_profiles(profiles_dir) == ["temp"]

    delete_profile("temp", profiles_dir)
    assert list_profiles(profiles_dir) == []


def test_delete_profile_missing_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        delete_profile("nope", tmp_path / "profiles")


@pytest.mark.parametrize("bad_name", ["", ".", "..", "a/b", "../escape", "a b"])
def test_invalid_profile_name_rejected(tmp_path, bad_name):
    with pytest.raises(ValueError):
        save_profile(bad_name, Settings(), tmp_path / "profiles")
