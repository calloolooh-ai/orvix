"""
tests for gesture_interpreter.py. uses hand-constructed dicts shaped like
what leapd sends rather than the (not yet captured) real fixture file, since
this module's logic doesn't depend on anything hardware-specific, just the
handful of fields it reads (palmPosition, pinchStrength, grabStrength).
"""

from orvix.config import Settings
from orvix.gesture_interpreter import GestureInterpreter, GestureType


def make_hand(x=0.0, y=200.0, z=0.0, pinch=0.0, grab=0.0, vy=0.0):
    return {
        "palmPosition": [x, y, z],
        "palmVelocity": [0.0, vy, 0.0],
        "pinchStrength": pinch,
        "grabStrength": grab,
    }


def test_open_hand_emits_point_move():
    interpreter = GestureInterpreter(Settings())
    events = interpreter.process_hand(make_hand(pinch=0.0, grab=0.0))
    assert len(events) == 1
    assert events[0].type == GestureType.POINT_MOVE


def test_pinch_crossing_threshold_emits_pinch_down_once():
    interpreter = GestureInterpreter(Settings())

    # hand open, cursor just tracks. has to be below pinch_freeze_threshold
    # too, or we'd be holding the cursor still ready for a click
    events = interpreter.process_hand(make_hand(pinch=0.1))
    assert events[0].type == GestureType.POINT_MOVE

    # crosses threshold, pinch down fires
    events = interpreter.process_hand(make_hand(pinch=0.9))
    assert events[0].type == GestureType.PINCH_DOWN

    # staying pinched but still within drag_hold_seconds shouldn't refire
    # pinch_down. the cursor should still track the hand though (we don't
    # know yet if this'll end up a tap or a drag), so it falls back to a
    # plain point_move rather than emitting nothing.
    events = interpreter.process_hand(make_hand(pinch=0.9))
    assert len(events) == 1
    assert events[0].type == GestureType.POINT_MOVE


def test_cursor_freezes_once_you_start_closing_your_fingers():
    """
    click stabilisation. closing your fingers tugs your palm sideways, so
    without this the cursor slides off the target in the moments before the
    click registers and you miss whatever you were aiming at.
    """
    interpreter = GestureInterpreter(Settings())

    # open hand, cursor tracks normally
    assert interpreter.process_hand(make_hand(x=0.0, pinch=0.0))[0].type == GestureType.POINT_MOVE

    # starting to close: past the freeze threshold but not yet a pinch.
    # the hand is drifting (x moves) and the cursor must NOT follow.
    assert interpreter.process_hand(make_hand(x=5.0, pinch=0.4)) == []
    assert interpreter.process_hand(make_hand(x=12.0, pinch=0.6)) == []

    # completing the pinch still clicks
    events = interpreter.process_hand(make_hand(x=15.0, pinch=0.9))
    assert events[0].type == GestureType.PINCH_DOWN


def test_freeze_can_be_turned_off():
    settings = Settings(pinch_freeze_threshold=0.0)
    interpreter = GestureInterpreter(settings)
    events = interpreter.process_hand(make_hand(pinch=0.5))
    assert events[0].type == GestureType.POINT_MOVE


def test_freezing_doesnt_block_movement_mid_drag():
    """mid-drag the pinch is held hard, but you obviously still need to move."""
    settings = Settings(drag_hold_seconds=0.0)  # go straight to dragging
    interpreter = GestureInterpreter(settings)

    interpreter.process_hand(make_hand(pinch=0.9))  # PINCH_DOWN
    interpreter.process_hand(make_hand(pinch=0.9))  # -> DRAGGING
    events = interpreter.process_hand(make_hand(x=20.0, pinch=0.9))

    assert events[0].type == GestureType.PINCH_DRAG


def test_quick_pinch_release_is_a_click_not_a_drag():
    settings = Settings()
    interpreter = GestureInterpreter(settings)

    interpreter.process_hand(make_hand(pinch=0.9))  # PINCH_DOWN
    # release immediately, well within drag_hold_seconds
    events = interpreter.process_hand(make_hand(pinch=0.1))

    assert len(events) == 1
    assert events[0].type == GestureType.PINCH_UP


def test_held_pinch_past_hold_window_becomes_drag():
    settings = Settings()
    settings.drag_hold_seconds = 0.0  # so the very next frame already counts as "held long enough"
    interpreter = GestureInterpreter(settings)

    interpreter.process_hand(make_hand(pinch=0.9))  # PINCH_DOWN
    events = interpreter.process_hand(make_hand(pinch=0.9))

    assert events[0].type == GestureType.PINCH_DRAG


def test_pinch_hysteresis_ignores_strength_between_thresholds():
    settings = Settings()  # pinch_threshold=0.75, pinch_release_threshold=0.5
    interpreter = GestureInterpreter(settings)

    interpreter.process_hand(make_hand(pinch=0.9))  # PINCH_DOWN
    # strength drops to 0.6, above release threshold, should stay "down"/dragging,
    # not release, this is the point of hysteresis
    events = interpreter.process_hand(make_hand(pinch=0.6))
    assert all(e.type != GestureType.PINCH_UP for e in events)


def test_grab_emits_start_then_scroll_then_end():
    settings = Settings()
    interpreter = GestureInterpreter(settings)

    events = interpreter.process_hand(make_hand(grab=0.9))
    assert events[0].type == GestureType.GRAB_START

    events = interpreter.process_hand(make_hand(grab=0.9, vy=50))
    assert events[0].type == GestureType.GRAB_SCROLL
    assert events[0].palm_velocity == (0.0, 50, 0.0)

    events = interpreter.process_hand(make_hand(grab=0.1))
    assert events[0].type == GestureType.GRAB_END


def test_high_grab_strength_without_fist_does_not_start_grab():
    # fingers still out (index + middle extended) means it's a partial curl,
    # not a fist, so grab must not fire even though grabStrength is high
    interpreter = GestureInterpreter(Settings())
    events = interpreter.process_hand(make_hand(grab=0.9), extended_fingers={1, 2})
    types = [e.type for e in events]
    assert GestureType.GRAB_START not in types


def test_closed_fist_starts_grab():
    # only the thumb left out (within grab_fist_max_extended default of 1)
    interpreter = GestureInterpreter(Settings())
    events = interpreter.process_hand(make_hand(grab=0.9), extended_fingers={0})
    assert events[0].type == GestureType.GRAB_START


def test_grab_falls_back_to_strength_when_no_extension_data():
    # None means the frame carried no finger-extension flags; grab should
    # still work off grabStrength alone rather than becoming impossible
    interpreter = GestureInterpreter(Settings())
    events = interpreter.process_hand(make_hand(grab=0.9), extended_fingers=None)
    assert events[0].type == GestureType.GRAB_START


def test_hand_lost_closes_out_open_pinch():
    interpreter = GestureInterpreter(Settings())
    interpreter.process_hand(make_hand(pinch=0.9))  # PINCH_DOWN

    events = interpreter.process_hand(None)

    types = [e.type for e in events]
    assert GestureType.PINCH_UP in types
    assert GestureType.HAND_LOST in types
    # pinch_up should come before hand_lost so a caller closing out mouse
    # state processes them in a sane order
    assert types.index(GestureType.PINCH_UP) < types.index(GestureType.HAND_LOST)


def test_hand_lost_closes_out_open_grab():
    interpreter = GestureInterpreter(Settings())
    interpreter.process_hand(make_hand(grab=0.9))  # GRAB_START

    events = interpreter.process_hand(None)

    assert GestureType.GRAB_END in [e.type for e in events]


def test_hand_lost_with_no_open_gesture_is_just_hand_lost():
    interpreter = GestureInterpreter(Settings())
    events = interpreter.process_hand(None)
    assert events == [events[0]]
    assert events[0].type == GestureType.HAND_LOST


def test_reset_with_no_open_gesture_is_a_no_op():
    interpreter = GestureInterpreter(Settings())
    assert interpreter.reset() == []


def test_reset_closes_out_open_pinch():
    interpreter = GestureInterpreter(Settings())
    interpreter.process_hand(make_hand(pinch=0.9))  # PINCH_DOWN

    events = interpreter.reset()

    assert [e.type for e in events] == [GestureType.PINCH_UP]


def test_reset_closes_out_open_grab():
    interpreter = GestureInterpreter(Settings())
    interpreter.process_hand(make_hand(grab=0.9))  # GRAB_START

    events = interpreter.reset()

    assert [e.type for e in events] == [GestureType.GRAB_END]


def test_reset_prevents_a_stale_mid_hold_from_becoming_an_instant_drag():
    # regression test: without reset(), a pinch that was mid-hold (DOWN, not
    # yet DRAGGING) when process_hand() stopped being called for a while (the
    # radial menu owning the hand, in practice) would keep its original
    # _pinch_started_at. real time elapsing during that gap could make the
    # drag-hold check read as satisfied the instant we resume, firing
    # PINCH_DRAG the user never actually held for.
    settings = Settings()
    settings.drag_hold_seconds = 10.0  # nowhere near elapsed in real test time
    interpreter = GestureInterpreter(settings)

    interpreter.process_hand(make_hand(pinch=0.9))  # PINCH_DOWN, starts the timer
    interpreter.reset()  # simulates the radial-menu-close cleanup

    # still pinching once control resumes: without reset() clearing the
    # stale timer, and with enough elapsed time, this would read as
    # "already held past drag_hold_seconds" and jump straight to DRAGGING
    events = interpreter.process_hand(make_hand(pinch=0.9))

    assert events[0].type == GestureType.PINCH_DOWN
