"""
tests for main.py's small pure helpers that had no coverage before:
_radial_state, _build_extras, _compute_signals. _dispatch/_execute_extras
are covered in test_dispatch.py/test_execute_extras.py, _fire_radial in
test_radial_dispatch.py -- this fills in the rest of the module's
non-async, non-hardware-dependent surface.
"""

import math

from orvix.config import Settings
from orvix.displays import DesktopBounds
from orvix.extra_gestures import ExtraGestures, HandSignals
from orvix.gesture_interpreter import GestureEvent, GestureType
from orvix.main import _bounds_changed, _build_extras, _compute_signals, _radial_state
from orvix.radial_menu import RadialMenu


# -- _radial_state --


def test_radial_state_reports_the_menus_own_center_and_actions():
    menu = RadialMenu(["copy", "paste", "close"])
    menu.open((640.0, 360.0), now=0.0)

    state = _radial_state(menu, hovered=1, progress=0.5)

    assert state["center"] == (640.0, 360.0)
    assert state["actions"] == ["copy", "paste", "close"]
    assert state["hovered"] == 1
    assert state["progress"] == 0.5


def test_radial_state_passes_through_none_hovered():
    menu = RadialMenu(["copy", "paste", "close"])
    state = _radial_state(menu, hovered=None, progress=0.0)
    assert state["hovered"] is None


# -- _build_extras --


def test_build_extras_wires_every_enable_flag_from_settings():
    settings = Settings(
        zoom_enabled=False,
        fist_twist_volume_enabled=False,
        dwell_click_enabled=False,
        palms_out_pause_enabled=False,
        thumbs_up_confirm_enabled=False,
    )
    extras = _build_extras(settings)
    assert isinstance(extras, ExtraGestures)

    # with everything disabled, feeding every signal at once produces no
    # actions -- the cheapest way to prove all five enable flags landed
    # (a single missed wire would let one signal through)
    sig = HandSignals(
        two_hand_pinch_span=100.0,
        fist_roll_rad=1.0,
        hover_point=(10.0, 10.0),
        palms_out=True,
        thumbs_up=True,
    )
    actions = extras.observe(sig, now=10.0)
    assert actions == []


def test_build_extras_defaults_leave_gestures_live():
    extras = _build_extras(Settings())
    # palms-out pause is on by default and fires immediately (no hold time
    # elapsed check needed here beyond the default hold_seconds boundary)
    actions = extras.observe(HandSignals(palms_out=True), now=1.0)
    actions = extras.observe(HandSignals(palms_out=True), now=1.0 + Settings().pause_hold_seconds + 0.01)
    assert actions  # pause toggled, something fired


# -- _compute_signals --


def make_frame(hands):
    return {"hands": hands}


def hand(x=0.0, y=200.0, z=0.0, pinch=0.0, grab=0.0, normal=(0.0, -1.0, 0.0)):
    return {
        "id": id(object()),
        "palmPosition": [x, y, z],
        "palmNormal": list(normal),
        "pinchStrength": pinch,
        "grabStrength": grab,
    }


def test_two_hand_pinch_span_is_none_with_fewer_than_two_pinching_hands():
    settings = Settings()
    frame = make_frame([hand(pinch=0.9)])
    sig = _compute_signals(frame, primary=frame["hands"][0], events=[], settings=settings)
    assert sig.two_hand_pinch_span is None


def test_two_hand_pinch_span_is_the_distance_between_both_palms():
    settings = Settings()
    a = hand(x=0.0, y=200.0, z=0.0, pinch=0.9)
    b = hand(x=30.0, y=200.0, z=40.0, pinch=0.9)
    frame = make_frame([a, b])
    sig = _compute_signals(frame, primary=a, events=[], settings=settings)
    assert sig.two_hand_pinch_span == math.dist((0.0, 200.0, 0.0), (30.0, 200.0, 40.0))


def test_palms_out_needs_two_upright_open_hands():
    settings = Settings()
    # a "stop" pose: sideways-facing normal is what is_halt_hand checks, but
    # it also needs finger-extension data to know the hand is open, which
    # this synthetic frame doesn't carry -- so this proves the negative case
    frame = make_frame([hand(normal=(1.0, 0.0, 0.0)), hand(normal=(1.0, 0.0, 0.0))])
    sig = _compute_signals(frame, primary=frame["hands"][0], events=[], settings=settings)
    assert sig.palms_out is False  # no pointables data -> can't confirm "open"


def test_palms_out_true_with_finger_extension_data_present():
    settings = Settings()
    open_hand_a = {**hand(normal=(1.0, 0.0, 0.0)), "id": 1}
    open_hand_b = {**hand(normal=(-1.0, 0.0, 0.0)), "id": 2}
    frame = {
        "hands": [open_hand_a, open_hand_b],
        "pointables": [
            {"handId": 1, "type": t, "extended": True} for t in range(5)
        ] + [
            {"handId": 2, "type": t, "extended": True} for t in range(5)
        ],
    }
    sig = _compute_signals(frame, primary=open_hand_a, events=[], settings=settings)
    assert sig.palms_out is True


def test_thumbs_up_signal_reflects_the_primary_hand_only():
    settings = Settings()
    primary = {**hand(normal=(1.0, 0.0, 0.0)), "id": 1}
    frame = {
        "hands": [primary],
        "pointables": [{"handId": 1, "type": 0, "extended": True}]
        + [{"handId": 1, "type": t, "extended": False} for t in (1, 2, 3, 4)],
    }
    sig = _compute_signals(frame, primary=primary, events=[], settings=settings)
    assert sig.thumbs_up is True


def test_fist_roll_only_set_above_grab_threshold():
    settings = Settings(grab_threshold=0.8)
    below = {**hand(grab=0.5), "id": 1}
    frame = make_frame([below])
    sig = _compute_signals(frame, primary=below, events=[], settings=settings)
    assert sig.fist_roll_rad is None

    above = {**hand(grab=0.9), "id": 1}
    frame2 = make_frame([above])
    sig2 = _compute_signals(frame2, primary=above, events=[], settings=settings)
    assert sig2.fist_roll_rad is not None


def test_hover_point_comes_from_a_point_move_event_only():
    settings = Settings()
    frame = make_frame([hand()])
    move_event = GestureEvent(GestureType.POINT_MOVE, palm_position=(5.0, 210.0, 0.0))
    sig = _compute_signals(frame, primary=frame["hands"][0], events=[move_event], settings=settings)
    assert sig.hover_point == (5.0, 210.0)


def test_hover_point_is_none_without_a_move_event():
    settings = Settings()
    frame = make_frame([hand()])
    pinch_event = GestureEvent(GestureType.PINCH_DOWN, palm_position=(5.0, 210.0, 0.0))
    sig = _compute_signals(frame, primary=frame["hands"][0], events=[pinch_event], settings=settings)
    assert sig.hover_point is None


def test_no_primary_hand_skips_fist_and_thumbs_signals():
    settings = Settings()
    frame = make_frame([])
    sig = _compute_signals(frame, primary=None, events=[], settings=settings)
    assert sig.fist_roll_rad is None
    assert sig.thumbs_up is False


# -- _bounds_changed --


def test_bounds_changed_is_none_when_desktop_is_unchanged():
    fresh = DesktopBounds(0.0, 0.0, 1920.0, 1080.0)
    assert _bounds_changed(1920.0, 1080.0, (0.0, 0.0), fresh) is None


def test_bounds_changed_detects_a_resize_eg_monitor_unplugged():
    fresh = DesktopBounds(0.0, 0.0, 1920.0, 1080.0)
    assert _bounds_changed(3840.0, 1080.0, (0.0, 0.0), fresh) == (1920.0, 1080.0, (0.0, 0.0))


def test_bounds_changed_detects_an_origin_shift():
    fresh = DesktopBounds(-1920.0, 0.0, 3840.0, 1080.0)
    assert _bounds_changed(1920.0, 1080.0, (0.0, 0.0), fresh) == (3840.0, 1080.0, (-1920.0, 0.0))
