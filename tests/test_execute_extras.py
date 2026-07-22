"""
tests for main._execute_extras: turning ExtraAction values into actual
mouse_control calls. this had no coverage before (test_dispatch.py only
covers the core pinch/grab path via _dispatch), and it's exactly the part
gesture rebinding touches -- CONFIRM now looks up settings.thumbs_up_action
in shortcuts.NAMED_SHORTCUTS instead of always firing a literal Return.
"""

import math

from orvix.config import CalibrationBox, Settings
from orvix.coord_mapper import CoordMapper
from orvix.extra_gestures import ExtraAction, ExtraGestures, HandSignals
from orvix.main import _execute_extras
from orvix.shortcuts import CONFIRM_SHORTCUT, NAMED_SHORTCUTS


class FakeMouse:
    def __init__(self):
        self.calls: list[tuple] = []

    def zoom(self, steps):
        self.calls.append(("zoom", steps))

    def set_volume_relative(self, delta_percent):
        self.calls.append(("set_volume_relative", delta_percent))

    def click(self):
        self.calls.append(("click",))

    def key_shortcut(self, keycode, mods=()):
        self.calls.append(("key_shortcut", keycode, mods))


def make_mapper():
    return CoordMapper(CalibrationBox(), 1920, 1080, Settings())


def test_zoom_in_and_out_step_the_scroll_wheel_with_cmd():
    mouse = FakeMouse()
    _execute_extras([ExtraAction.ZOOM_IN, ExtraAction.ZOOM_OUT], mouse, make_mapper(), Settings())
    assert mouse.calls == [("zoom", 1), ("zoom", -1)]


def test_volume_actions_use_the_configured_step_percent():
    settings = Settings(volume_step_percent=10)
    mouse = FakeMouse()
    _execute_extras([ExtraAction.VOLUME_UP, ExtraAction.VOLUME_DOWN], mouse, make_mapper(), settings)
    assert mouse.calls == [("set_volume_relative", 10), ("set_volume_relative", -10)]


def test_a_slow_twist_uses_the_minimum_volume_step():
    settings = Settings(
        volume_step_percent=5,
        volume_max_percent=20,
        volume_rate_slow_deg_s=30.0,
        volume_rate_fast_deg_s=200.0,
    )
    extras = ExtraGestures(volume_step_deg=10.0)
    # small twist over a full second: well under volume_rate_slow_deg_s
    extras.observe(HandSignals(fist_roll_rad=0.0), now=0.0)
    actions = extras.observe(HandSignals(fist_roll_rad=math.radians(15)), now=1.0)

    mouse = FakeMouse()
    _execute_extras(actions, mouse, make_mapper(), settings, extras)
    assert mouse.calls == [("set_volume_relative", 5)]


def test_a_fast_twist_uses_a_bigger_volume_step_than_a_slow_one():
    settings = Settings(
        volume_step_percent=5,
        volume_max_percent=20,
        volume_rate_slow_deg_s=30.0,
        volume_rate_fast_deg_s=200.0,
    )
    extras = ExtraGestures(volume_step_deg=10.0)
    # same 15deg twist, but in 20ms instead of a full second -> much faster
    extras.observe(HandSignals(fist_roll_rad=0.0), now=0.0)
    actions = extras.observe(HandSignals(fist_roll_rad=math.radians(15)), now=0.02)

    mouse = FakeMouse()
    _execute_extras(actions, mouse, make_mapper(), settings, extras)
    assert len(mouse.calls) == 1
    assert mouse.calls[0][0] == "set_volume_relative"
    assert mouse.calls[0][1] > 5  # scaled above the slow-twist minimum


def test_dwell_click_fires_a_plain_click():
    mouse = FakeMouse()
    _execute_extras([ExtraAction.DWELL_CLICK], mouse, make_mapper(), Settings())
    assert mouse.calls == [("click",)]


def test_confirm_defaults_to_the_literal_return_shortcut():
    mouse = FakeMouse()
    _execute_extras([ExtraAction.CONFIRM], mouse, make_mapper(), Settings())
    assert mouse.calls == [("key_shortcut", CONFIRM_SHORTCUT.keycode, CONFIRM_SHORTCUT.mods)]


def test_confirm_honours_a_remapped_thumbs_up_action():
    settings = Settings(thumbs_up_action="undo")
    mouse = FakeMouse()
    _execute_extras([ExtraAction.CONFIRM], mouse, make_mapper(), settings)
    undo = NAMED_SHORTCUTS["undo"]
    assert mouse.calls == [("key_shortcut", undo.keycode, undo.mods)]


def test_confirm_falls_back_to_return_for_an_unknown_action_name():
    # e.g. a hand-edited or stale config.yaml naming something that no
    # longer exists in NAMED_SHORTCUTS -- must not silently do nothing
    settings = Settings(thumbs_up_action="not_a_real_action")
    mouse = FakeMouse()
    _execute_extras([ExtraAction.CONFIRM], mouse, make_mapper(), settings)
    assert mouse.calls == [("key_shortcut", CONFIRM_SHORTCUT.keycode, CONFIRM_SHORTCUT.mods)]


def test_pause_actions_reset_the_mapper_and_dont_touch_the_mouse():
    mouse = FakeMouse()
    mapper = make_mapper()
    # feed it a position so there's filter state to reset
    mapper.map_to_screen((0.0, 200.0, 0.0), timestamp=0.0)

    _execute_extras([ExtraAction.PAUSE_ON, ExtraAction.PAUSE_OFF], mouse, mapper, Settings())

    assert mouse.calls == []  # pause is silent on the mouse side


def test_empty_action_list_does_nothing():
    mouse = FakeMouse()
    _execute_extras([], mouse, make_mapper(), Settings())
    assert mouse.calls == []
