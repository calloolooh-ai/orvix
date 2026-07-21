"""
tests for handviz.py's pure/thread-safe pieces: _clamp, HandState's
thread-safe snapshotting, Ripple's lifetime check, and _ease_out. same
split as test_handrender.py -- the AppKit drawing isn't covered, the logic
feeding it is.
"""

from orvix.handviz import HandState, Ripple, _clamp, _ease_out


def test_clamp_bounds_both_directions():
    assert _clamp(-5, 0, 10) == 0
    assert _clamp(15, 0, 10) == 10
    assert _clamp(5, 0, 10) == 5


# -- HandState --


def test_hand_state_starts_absent():
    state = HandState()
    snap = state.snapshot()
    assert snap["present"] is False
    assert snap["seq"] == 0
    assert snap["error"] is None


def test_hand_state_update_bumps_seq_and_stores_values():
    state = HandState()
    state.update(True, (1.0, 2.0, 3.0), 50.0, {0: (1.0, 1.0, 1.0)}, 0.5, 0.1)
    snap = state.snapshot()
    assert snap["present"] is True
    assert snap["palm"] == (1.0, 2.0, 3.0)
    assert snap["palm_speed"] == 50.0
    assert snap["tips"] == {0: (1.0, 1.0, 1.0)}
    assert snap["pinch"] == 0.5
    assert snap["grab"] == 0.1
    assert snap["seq"] == 1


def test_hand_state_snapshot_tips_is_a_copy():
    state = HandState()
    state.update(True, (0.0, 0.0, 0.0), 0.0, {0: (1.0, 1.0, 1.0)}, 0.0, 0.0)
    snap = state.snapshot()
    snap["tips"][1] = (9.0, 9.0, 9.0)
    # mutating the snapshot's dict must not leak back into the state
    assert 1 not in state.snapshot()["tips"]


def test_hand_state_set_error_is_visible_in_snapshot():
    state = HandState()
    state.set_error("leapd stream stopped: boom")
    assert state.snapshot()["error"] == "leapd stream stopped: boom"


def test_hand_state_seq_increments_across_repeated_updates():
    state = HandState()
    for _ in range(5):
        state.update(False, (0.0, 0.0, 0.0), 0.0, {}, 0.0, 0.0)
    assert state.snapshot()["seq"] == 5


# -- Ripple --


def test_ripple_is_alive_before_its_life_elapses():
    ripple = Ripple(cx=0, cy=0, life=1.0, r0=10, r1=100, w0=3.0, rgb=(1, 1, 1))
    assert ripple.alive(now=ripple.t0) is True
    assert ripple.alive(now=ripple.t0 + 0.5) is True


def test_ripple_is_dead_after_its_life_elapses():
    ripple = Ripple(cx=0, cy=0, life=1.0, r0=10, r1=100, w0=3.0, rgb=(1, 1, 1))
    assert ripple.alive(now=ripple.t0 + 1.5) is False


def test_ripple_stores_its_construction_params():
    ripple = Ripple(cx=5.0, cy=6.0, life=2.0, r0=1.0, r1=50.0, w0=4.0, rgb=(0.1, 0.2, 0.3), peak=0.9)
    assert (ripple.cx, ripple.cy) == (5.0, 6.0)
    assert ripple.r0 == 1.0
    assert ripple.r1 == 50.0
    assert ripple.w0 == 4.0
    assert ripple.rgb == (0.1, 0.2, 0.3)
    assert ripple.peak == 0.9


def test_ripple_peak_defaults_to_point_seven():
    ripple = Ripple(cx=0, cy=0, life=1.0, r0=0, r1=1, w0=1, rgb=(1, 1, 1))
    assert ripple.peak == 0.7


# -- _ease_out --


def test_ease_out_endpoints():
    assert _ease_out(0.0) == 0.0
    assert _ease_out(1.0) == 1.0


def test_ease_out_decelerates_faster_than_linear_partway_through():
    # ease-out means most of the motion happens early: at t=0.5 it should
    # already be more than halfway to the end value
    assert _ease_out(0.5) > 0.5
