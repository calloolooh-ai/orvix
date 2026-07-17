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

    # below threshold, nothing happens
    events = interpreter.process_hand(make_hand(pinch=0.5))
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
