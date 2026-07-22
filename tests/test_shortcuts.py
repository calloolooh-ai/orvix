"""
tests for shortcuts.py: the Shortcut dataclass's modifier validation, and
that NAMED_SHORTCUTS (the registry gesture rebinding picks from) stays
internally consistent -- every entry has a label, and "confirm" resolves to
the same Return shortcut CONFIRM_SHORTCUT does.
"""

import pytest

from orvix.shortcuts import (
    CONFIRM_SHORTCUT,
    DEFAULT_RADIAL_ACTIONS,
    NAMED_SHORTCUT_LABELS,
    NAMED_SHORTCUTS,
    RADIAL_SHORTCUTS,
    Shortcut,
)


def test_shortcut_accepts_known_modifiers():
    s = Shortcut(1, ("cmd", "shift"))
    assert s.mods == ("cmd", "shift")


def test_shortcut_rejects_an_unknown_modifier():
    with pytest.raises(ValueError, match="unknown modifier"):
        Shortcut(1, ("cmd", "banana"))


def test_shortcut_with_no_modifiers_is_fine():
    s = Shortcut(36)
    assert s.mods == ()


def test_named_shortcuts_includes_every_radial_shortcut():
    for name, shortcut in RADIAL_SHORTCUTS.items():
        assert NAMED_SHORTCUTS[name] == shortcut


def test_named_shortcuts_includes_confirm_and_it_matches_confirm_shortcut():
    assert NAMED_SHORTCUTS["confirm"] == CONFIRM_SHORTCUT


def test_every_named_shortcut_has_a_label():
    assert set(NAMED_SHORTCUTS) == set(NAMED_SHORTCUT_LABELS)


def test_labels_are_nonempty_human_text():
    for label in NAMED_SHORTCUT_LABELS.values():
        assert isinstance(label, str)
        assert label.strip() != ""


def test_spotlight_is_cmd_space():
    assert RADIAL_SHORTCUTS["spotlight"] == Shortcut(49, ("cmd",))


def test_spotlight_is_opt_in_not_a_default_wedge():
    # available for radial_actions/thumbs_up_action, but doesn't change the
    # default wheel layout for existing users
    assert "spotlight" not in DEFAULT_RADIAL_ACTIONS
