"""
tests for leap_client.py's pure frame-parsing helpers: pick_hand,
fingertips_for_hand, extended_fingers_for_hand. these never touch the
websocket, they just work with plain frame/hand dicts shaped like what
leapd sends, so no leapd or real Leap Motion hardware needed.
"""

from orvix.leap_client import (
    extended_fingers_for_hand,
    fingertips_for_hand,
    pick_hand,
)


def _hand(hand_id, hand_type):
    return {"id": hand_id, "type": hand_type}


def _pointable(hand_id, finger_type, extended=None, tip=None):
    p = {"handId": hand_id, "type": finger_type}
    if extended is not None:
        p["extended"] = extended
    if tip is not None:
        p["tipPosition"] = tip
    return p


# pick_hand


def test_pick_hand_no_hands_returns_none():
    assert pick_hand({"hands": []}, "right") is None


def test_pick_hand_missing_hands_key_returns_none():
    assert pick_hand({}, "right") is None


def test_pick_hand_first_returns_whichever_is_listed_first():
    frame = {"hands": [_hand(1, "left"), _hand(2, "right")]}
    assert pick_hand(frame, "first") == _hand(1, "left")


def test_pick_hand_matches_preferred_type():
    frame = {"hands": [_hand(1, "left"), _hand(2, "right")]}
    assert pick_hand(frame, "right") == _hand(2, "right")


def test_pick_hand_does_not_fall_back_to_other_hand():
    # only a left hand is visible but we want right: must return None,
    # not silently substitute the left hand (would surprise cursor mapping)
    frame = {"hands": [_hand(1, "left")]}
    assert pick_hand(frame, "right") is None


def test_pick_hand_first_keeps_tracking_last_id_even_if_not_first_in_list():
    # a second hand (bystander, or the user's other hand) sorts ahead of
    # the one we were already tracking -- must not silently swap to it
    frame = {"hands": [_hand(2, "right"), _hand(1, "left")]}
    assert pick_hand(frame, "first", last_hand_id=1) == _hand(1, "left")


def test_pick_hand_first_falls_back_to_hands_0_once_last_id_is_gone():
    frame = {"hands": [_hand(2, "right")]}
    assert pick_hand(frame, "first", last_hand_id=1) == _hand(2, "right")


def test_pick_hand_first_with_no_last_id_yet_takes_hands_0():
    frame = {"hands": [_hand(1, "left"), _hand(2, "right")]}
    assert pick_hand(frame, "first", last_hand_id=None) == _hand(1, "left")


# fingertips_for_hand


def test_fingertips_for_hand_maps_type_to_tip_position():
    hand = _hand(1, "right")
    frame = {
        "hands": [hand],
        "pointables": [
            _pointable(1, 1, tip=[10.0, 20.0, 30.0]),
            _pointable(1, 0, tip=[1.0, 2.0, 3.0]),
        ],
    }
    tips = fingertips_for_hand(frame, hand)
    assert tips == {1: (10.0, 20.0, 30.0), 0: (1.0, 2.0, 3.0)}


def test_fingertips_for_hand_ignores_other_hands_pointables():
    hand = _hand(1, "right")
    frame = {
        "hands": [hand, _hand(2, "left")],
        "pointables": [_pointable(2, 1, tip=[99.0, 99.0, 99.0])],
    }
    assert fingertips_for_hand(frame, hand) == {}


def test_fingertips_for_hand_no_pointables_returns_empty_dict():
    hand = _hand(1, "right")
    assert fingertips_for_hand({"hands": [hand]}, hand) == {}


def test_fingertips_for_hand_skips_pointables_missing_tip_or_type():
    hand = _hand(1, "right")
    frame = {
        "hands": [hand],
        "pointables": [
            {"handId": 1, "type": None, "tipPosition": [1.0, 2.0, 3.0]},
            {"handId": 1, "type": 2, "tipPosition": None},
        ],
    }
    assert fingertips_for_hand(frame, hand) == {}


# extended_fingers_for_hand


def test_extended_fingers_for_hand_no_pointables_returns_none():
    hand = _hand(1, "right")
    assert extended_fingers_for_hand({"hands": [hand]}, hand) is None


def test_extended_fingers_for_hand_returns_set_of_extended_types():
    hand = _hand(1, "right")
    frame = {
        "hands": [hand],
        "pointables": [
            _pointable(1, 0, extended=True),
            _pointable(1, 1, extended=True),
            _pointable(1, 2, extended=False),
        ],
    }
    assert extended_fingers_for_hand(frame, hand) == {0, 1}


def test_extended_fingers_for_hand_all_curled_returns_empty_set_not_none():
    # empty set (all fingers curled, a real fist) must be distinguishable
    # from None (no usable data) -- otherwise a fist looks like "can't tell"
    hand = _hand(1, "right")
    frame = {
        "hands": [hand],
        "pointables": [
            _pointable(1, 0, extended=False),
            _pointable(1, 1, extended=False),
        ],
    }
    assert extended_fingers_for_hand(frame, hand) == set()


def test_extended_fingers_for_hand_ignores_other_hands_pointables():
    hand = _hand(1, "right")
    frame = {
        "hands": [hand, _hand(2, "left")],
        "pointables": [_pointable(2, 1, extended=True)],
    }
    assert extended_fingers_for_hand(frame, hand) is None
