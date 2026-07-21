"""
tests for handrender.py's pure/thread-safe pieces: the clamp/map helpers
(same shape as coord_mapper's, but this module keeps its own copy since it
projects into the visualizer's view size, not the real cursor's screen),
HandsState's thread-safe snapshotting, and _parse_frame turning a raw leapd
frame into the compact per-hand structure the renderer projects.

the AppKit drawing itself isn't covered here (no meaningful way to assert on
pixels drawn to an offscreen view), this is the logic upstream of that.
"""

from orvix.handrender import HandsState, _clamp, _map_range, _parse_frame


# -- _clamp / _map_range --


def test_clamp_bounds_both_directions():
    assert _clamp(-5, 0, 10) == 0
    assert _clamp(15, 0, 10) == 10
    assert _clamp(5, 0, 10) == 5


def test_map_range_linear_interpolation():
    assert _map_range(5, 0, 10, 0, 100) == 50
    assert _map_range(0, 0, 10, 0, 100) == 0
    assert _map_range(10, 0, 10, 0, 100) == 100


def test_map_range_clamps_outside_input_range():
    assert _map_range(-5, 0, 10, 0, 100) == 0
    assert _map_range(15, 0, 10, 0, 100) == 100


def test_map_range_degenerate_input_span_returns_output_midpoint():
    assert _map_range(5, 3, 3, 0, 100) == 50


# -- HandsState --


def test_hands_state_starts_empty():
    state = HandsState()
    snap = state.snapshot()
    assert snap["hands"] == []
    assert snap["seq"] == 0
    assert snap["error"] is None


def test_hands_state_update_bumps_seq_and_stores_hands():
    state = HandsState()
    state.update([{"palm": (0, 0, 0)}])
    snap = state.snapshot()
    assert snap["hands"] == [{"palm": (0, 0, 0)}]
    assert snap["seq"] == 1

    state.update([])
    assert state.snapshot()["seq"] == 2


def test_hands_state_snapshot_is_a_copy_not_a_live_reference():
    state = HandsState()
    hands = [{"palm": (0, 0, 0)}]
    state.update(hands)
    snap = state.snapshot()
    snap["hands"].append({"palm": (1, 1, 1)})
    # mutating the snapshot's list must not leak back into the state
    assert len(state.snapshot()["hands"]) == 1


def test_hands_state_set_error_is_visible_in_snapshot():
    state = HandsState()
    state.set_error("leapd stream stopped: boom")
    assert state.snapshot()["error"] == "leapd stream stopped: boom"


# -- _parse_frame --


def test_parse_frame_with_no_hands_returns_empty_list():
    assert _parse_frame({}) == []
    assert _parse_frame({"hands": []}) == []


def test_parse_frame_extracts_palm_wrist_elbow():
    frame = {
        "hands": [
            {
                "id": 1,
                "type": "right",
                "palmPosition": [1.0, 2.0, 3.0],
                "wrist": [4.0, 5.0, 6.0],
                "elbow": [7.0, 8.0, 9.0],
                "pinchStrength": 0.5,
                "grabStrength": 0.2,
            }
        ]
    }
    parsed = _parse_frame(frame)
    assert len(parsed) == 1
    hand = parsed[0]
    assert hand["palm"] == (1.0, 2.0, 3.0)
    assert hand["wrist"] == (4.0, 5.0, 6.0)
    assert hand["elbow"] == (7.0, 8.0, 9.0)
    assert hand["pinch"] == 0.5
    assert hand["grab"] == 0.2
    assert hand["type"] == "right"


def test_parse_frame_defaults_wrist_and_elbow_to_palm_when_missing():
    frame = {"hands": [{"id": 1, "palmPosition": [1.0, 2.0, 3.0]}]}
    hand = _parse_frame(frame)[0]
    assert hand["wrist"] == (1.0, 2.0, 3.0)
    assert hand["elbow"] == (1.0, 2.0, 3.0)


def test_parse_frame_matches_fingers_to_the_right_hand_by_id():
    frame = {
        "hands": [{"id": 1, "palmPosition": [0.0, 0.0, 0.0]}, {"id": 2, "palmPosition": [10.0, 0.0, 0.0]}],
        "pointables": [
            {"handId": 1, "type": 0, "extended": True, "tipPosition": [1.0, 1.0, 1.0]},
            {"handId": 2, "type": 0, "extended": False, "tipPosition": [9.0, 9.0, 9.0]},
        ],
    }
    parsed = _parse_frame(frame)
    hand1, hand2 = parsed
    assert 0 in hand1["fingers"]
    assert hand1["fingers"][0]["extended"] is True
    assert hand1["fingers"][0]["joints"] == [(1.0, 1.0, 1.0)]
    assert hand2["fingers"][0]["extended"] is False


def test_parse_frame_skips_pointables_with_no_finger_type():
    frame = {
        "hands": [{"id": 1, "palmPosition": [0.0, 0.0, 0.0]}],
        "pointables": [{"handId": 1, "type": None, "tipPosition": [1.0, 1.0, 1.0]}],
    }
    hand = _parse_frame(frame)[0]
    assert hand["fingers"] == {}


def test_parse_frame_collects_every_joint_thats_present():
    frame = {
        "hands": [{"id": 1, "palmPosition": [0.0, 0.0, 0.0]}],
        "pointables": [
            {
                "handId": 1,
                "type": 1,
                "extended": True,
                "carpPosition": [1.0, 0.0, 0.0],
                "mcpPosition": [2.0, 0.0, 0.0],
                "pipPosition": [3.0, 0.0, 0.0],
                "dipPosition": [4.0, 0.0, 0.0],
                "tipPosition": [5.0, 0.0, 0.0],
            }
        ],
    }
    hand = _parse_frame(frame)[0]
    joints = hand["fingers"][1]["joints"]
    assert joints == [(1.0, 0.0, 0.0), (2.0, 0.0, 0.0), (3.0, 0.0, 0.0), (4.0, 0.0, 0.0), (5.0, 0.0, 0.0)]
