"""
tests for gui.py: the pure label/checkmark helpers, and OrvixApp's menu
construction + setter callbacks. previously zero coverage, even though the
menu bar is the primary way most people actually run orvix.

AppKit/rumps are real here, not mocked out (this dev machine has them, same
as the py2app build in test_setup_py2app.py) -- constructing an NSStatusItem-
backed App works fine without calling .run(), which is what actually spins
up the Cocoa run loop. what every test guards against is touching the real
~/.orvix/config.yaml on whatever machine runs this suite: load_config and
save_config are monkeypatched everywhere, never left to hit disk.
"""

import pytest

import orvix.gui as gui
from orvix.config import Settings
from orvix.gui import (
    FIST_CHOICES,
    FIST_LABELS,
    _dwell_label_for,
    _fist_choice_for,
)


# -- pure helpers --


def test_dwell_label_matches_the_configured_seconds():
    assert _dwell_label_for(Settings(radial_dwell_seconds=0.6)) == "0.6s"


def test_dwell_label_is_off_for_zero():
    assert _dwell_label_for(Settings(radial_dwell_seconds=0.0)) == "Off (pinch only)"


def test_dwell_label_is_blank_for_an_unlisted_value():
    # a value set outside the GUI (hand-edited config) shouldn't force a
    # checkmark onto the nearest listed choice
    assert _dwell_label_for(Settings(radial_dwell_seconds=0.123)) == ""


def test_fist_choice_off_when_require_fist_is_false():
    assert _fist_choice_for(Settings(grab_require_fist=False)) == "off"


@pytest.mark.parametrize("choice", ["strict", "thumb", "loose"])
def test_fist_choice_round_trips_through_settings(choice):
    from orvix.gui import FIST_SETTINGS

    require_fist, max_extended = FIST_SETTINGS[choice]
    settings = Settings(grab_require_fist=require_fist, grab_fist_max_extended=max_extended)
    assert _fist_choice_for(settings) == choice


def test_fist_choice_blank_for_a_custom_max_extended():
    settings = Settings(grab_require_fist=True, grab_fist_max_extended=99)
    assert _fist_choice_for(settings) == ""


# -- OrvixApp construction + setters --


@pytest.fixture
def isolated_app(monkeypatch, tmp_path):
    """
    an OrvixApp built against a throwaway Settings() and a save_config that
    just records calls instead of writing anywhere, so tests can drive menu
    callbacks freely without any risk to the real machine's config.
    """
    monkeypatch.setattr(gui, "load_config", lambda: Settings())
    saved = []
    monkeypatch.setattr(gui, "save_config", lambda settings, *a, **k: saved.append(settings))
    monkeypatch.setattr(gui, "DEFAULT_CONFIG_PATH", tmp_path / "config.yaml")

    app = gui.OrvixApp()
    app._test_saved = saved
    return app


def test_pinch_and_grab_menus_offer_every_action_choice(isolated_app):
    titles = {item.title for item in isolated_app.pinch_menu.values()}
    assert titles == {"Click / Drag", "Scroll", "Disabled"}
    titles = {item.title for item in isolated_app.grab_menu.values()}
    assert titles == {"Click / Drag", "Scroll", "Disabled"}


def test_thumbs_menu_offers_every_named_shortcut(isolated_app):
    from orvix.shortcuts import NAMED_SHORTCUT_LABELS

    titles = {item.title for item in isolated_app.thumbs_menu.values()}
    assert titles == set(NAMED_SHORTCUT_LABELS.values())


def test_default_settings_check_the_right_menu_items(isolated_app):
    # defaults: pinch=click, grab=scroll, thumbs_up_action=confirm ("Return")
    checked_pinch = [i.title for i in isolated_app.pinch_menu.values() if i.state]
    checked_grab = [i.title for i in isolated_app.grab_menu.values() if i.state]
    checked_thumbs = [i.title for i in isolated_app.thumbs_menu.values() if i.state]
    assert checked_pinch == ["Click / Drag"]
    assert checked_grab == ["Scroll"]
    assert checked_thumbs == ["Return"]


def test_multi_monitor_toggle_reflects_settings_default_on(isolated_app):
    assert bool(isolated_app.multi_monitor_toggle.state) is True


def test_action_setter_updates_settings_and_saves(isolated_app):
    setter = isolated_app._make_action_setter("pinch", "scroll")
    sender = isolated_app.pinch_menu.get("Scroll")

    setter(sender)

    assert isolated_app.settings.pinch_action == "scroll"
    assert sender.state  # menu item itself gets checked
    assert len(isolated_app._test_saved) == 1


def test_thumbs_up_setter_updates_settings_and_refreshes_checkmarks(isolated_app):
    setter = isolated_app._make_thumbs_up_setter("undo")
    sender = isolated_app.thumbs_menu.get("Undo")

    setter(sender)

    assert isolated_app.settings.thumbs_up_action == "undo"
    # the checkmark moved off "Return" onto "Undo"
    checked = [i.title for i in isolated_app.thumbs_menu.values() if i.state]
    assert checked == ["Undo"]


def test_multi_monitor_toggle_flips_the_setting(isolated_app):
    sender = isolated_app.multi_monitor_toggle
    assert isolated_app.settings.multi_monitor is True

    isolated_app._toggle_multi_monitor(sender)

    assert isolated_app.settings.multi_monitor is False
    assert bool(sender.state) is False


def test_dry_run_toggle_flips_independently_of_settings(isolated_app):
    # dry-run is deliberately not part of Settings/config.yaml, it's a
    # per-session menu checkbox, see _toggle_dry_run
    sender = isolated_app.dry_run
    assert bool(sender.state) is False

    isolated_app._toggle_dry_run(sender)

    assert bool(sender.state) is True
    assert bool(isolated_app.dry_run.state) is True
