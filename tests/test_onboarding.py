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
