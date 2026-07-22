"""tests for onboarding.py's first-run detection, a plain path check."""

from orvix.onboarding import is_first_run


def test_first_run_is_true_when_no_config_exists(tmp_path):
    assert is_first_run(tmp_path / "config.yaml") is True


def test_first_run_is_false_once_a_config_file_exists(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("cursor_mode: relative\n")
    assert is_first_run(path) is False


def test_first_run_is_false_for_an_empty_config_file(tmp_path):
    # an empty file still counts as "a config was saved", even if it has
    # nothing in it (e.g. save_config wrote defaults with nothing non-default)
    path = tmp_path / "config.yaml"
    path.touch()
    assert is_first_run(path) is False


def test_first_run_is_true_when_profiles_dir_missing_and_no_config(tmp_path):
    profiles_dir = tmp_path / "profiles"
    assert is_first_run(tmp_path / "config.yaml", profiles_dir) is True


def test_first_run_is_true_when_profiles_dir_exists_but_empty(tmp_path):
    profiles_dir = tmp_path / "profiles"
    profiles_dir.mkdir()
    assert is_first_run(tmp_path / "config.yaml", profiles_dir) is True


def test_first_run_is_false_when_a_profile_was_saved_without_config(tmp_path):
    # _save_profile_as in gui.py only calls save_profile, never save_config,
    # so a user could have a saved profile with config.yaml still missing
    profiles_dir = tmp_path / "profiles"
    profiles_dir.mkdir()
    (profiles_dir / "my_setup.yaml").write_text("cursor_mode: relative\n")
    assert is_first_run(tmp_path / "config.yaml", profiles_dir) is False


def test_first_run_still_false_via_config_path_when_profiles_dir_omitted(tmp_path):
    # existing callers that don't pass profiles_dir keep working exactly
    # as before, config.yaml presence alone still governs
    path = tmp_path / "config.yaml"
    path.write_text("cursor_mode: relative\n")
    assert is_first_run(path) is False
