"""
tests for extra_gestures.py: the pose helpers and each of the five detectors,
driven by synthetic per-frame signals.
"""

import math

import pytest

from orvix.extra_gestures import (
    ExtraAction,
    ExtraGestures,
    HandSignals,
    is_halt_hand,
    is_thumbs_up,
    roll_from_normal,
    scaled_volume_percent,
)


# ---- pose geometry ----

def test_roll_is_zero_for_a_palm_down_hand():
    assert abs(roll_from_normal((0.0, -1.0, 0.0))) < 1e-9


def test_roll_tracks_wrist_twist_direction():
    # twisting so the palm turns toward +x gives a positive-then-negative arc;
    # just check the two twist directions have opposite sign
    left = roll_from_normal((-0.7, -0.7, 0.0))
    right = roll_from_normal((0.7, -0.7, 0.0))
    assert left < 0 < right


def test_thumbs_up_needs_only_the_thumb_and_an_upright_hand():
    assert is_thumbs_up({0}, (1.0, 0.0, 0.0)) is True
    assert is_thumbs_up({0, 1}, (1.0, 0.0, 0.0)) is False  # index out too
    assert is_thumbs_up({0}, (0.0, -1.0, 0.0)) is False  # palm-down, not upright
    assert is_thumbs_up(None, (1.0, 0.0, 0.0)) is False  # no finger data


def test_halt_hand_needs_an_open_upright_palm():
    assert is_halt_hand({0, 1, 2, 3, 4}, (0.9, 0.1, 0.0)) is True
    assert is_halt_hand({0, 1, 2, 3, 4}, (0.0, -1.0, 0.0)) is False  # palm-down
    assert is_halt_hand({1, 2}, (1.0, 0.0, 0.0)) is False  # only two fingers


# ---- zoom ----

def make(**kw):
    # all detectors on unless overridden, thresholds explicit for clarity
    base = dict(
        zoom_step_mm=10.0,
        volume_step_deg=10.0,
        dwell_radius_px=18.0,
        dwell_seconds=0.6,
        pause_hold_seconds=0.4,
        confirm_hold_seconds=0.4,
    )
    base.update(kw)
    return ExtraGestures(**base)


def test_two_hands_pulling_apart_zooms_in():
    ex = make()
    ex.observe(HandSignals(two_hand_pinch_span=100.0), now=0.0)
    out = ex.observe(HandSignals(two_hand_pinch_span=135.0), now=0.1)  # +35mm
    assert out.count(ExtraAction.ZOOM_IN) == 3  # 35 / 10mm step
    assert ExtraAction.ZOOM_OUT not in out


def test_two_hands_coming_together_zooms_out():
    ex = make()
    ex.observe(HandSignals(two_hand_pinch_span=150.0), now=0.0)
    out = ex.observe(HandSignals(two_hand_pinch_span=128.0), now=0.1)
    assert out.count(ExtraAction.ZOOM_OUT) == 2


def test_releasing_the_zoom_resets_so_it_doesnt_lurch_on_reopen():
    ex = make()
    ex.observe(HandSignals(two_hand_pinch_span=100.0), now=0.0)
    ex.observe(HandSignals(two_hand_pinch_span=None), now=0.1)  # let go
    out = ex.observe(HandSignals(two_hand_pinch_span=300.0), now=0.2)  # far apart now
    assert out == []  # no giant jump from the old span


# ---- volume ----

def test_twisting_the_fist_changes_volume():
    ex = make()
    ex.observe(HandSignals(fist_roll_rad=0.0), now=0.0)
    out = ex.observe(HandSignals(fist_roll_rad=math.radians(32)), now=0.1)
    assert out.count(ExtraAction.VOLUME_UP) == 3
    back = ex.observe(HandSignals(fist_roll_rad=math.radians(10)), now=0.2)
    assert back.count(ExtraAction.VOLUME_DOWN) == 2


def test_volume_twist_rate_is_zero_before_any_twist():
    ex = make()
    assert ex.volume_twist_rate_deg_s == 0.0


def test_volume_twist_rate_tracks_degrees_per_second():
    ex = make()
    ex.observe(HandSignals(fist_roll_rad=0.0), now=0.0)
    ex.observe(HandSignals(fist_roll_rad=math.radians(20)), now=0.5)  # 40 deg/s
    assert ex.volume_twist_rate_deg_s == pytest.approx(40.0, abs=0.5)


def test_volume_twist_rate_is_zero_when_volume_gesture_disabled():
    ex = make(volume_enabled=False)
    ex.observe(HandSignals(fist_roll_rad=0.0), now=0.0)
    ex.observe(HandSignals(fist_roll_rad=math.radians(20)), now=0.5)
    assert ex.volume_twist_rate_deg_s == 0.0


def test_volume_twist_rate_resets_when_hand_drops_the_fist():
    ex = make()
    ex.observe(HandSignals(fist_roll_rad=0.0), now=0.0)
    ex.observe(HandSignals(fist_roll_rad=math.radians(20)), now=0.5)
    ex.observe(HandSignals(fist_roll_rad=None), now=0.6)  # let go of the fist
    assert ex.volume_twist_rate_deg_s == 0.0


# ---- proportional volume percent mapping ----

def test_scaled_volume_percent_clamps_to_minimum_below_the_slow_threshold():
    assert scaled_volume_percent(10.0, min_percent=5, max_percent=20, slow_deg_s=30.0, fast_deg_s=200.0) == 5


def test_scaled_volume_percent_clamps_to_maximum_above_the_fast_threshold():
    assert scaled_volume_percent(300.0, min_percent=5, max_percent=20, slow_deg_s=30.0, fast_deg_s=200.0) == 20


def test_scaled_volume_percent_interpolates_between_the_thresholds():
    # halfway between 30 and 200 deg/s -> halfway between 5 and 20 percent
    # (12.5 rounds to 12: Python's round() is banker's rounding, half-to-even)
    midpoint = (30.0 + 200.0) / 2
    assert scaled_volume_percent(midpoint, min_percent=5, max_percent=20, slow_deg_s=30.0, fast_deg_s=200.0) == 12


# ---- dwell ----

def test_holding_still_dwell_clicks_once():
    ex = make(dwell_seconds=0.6)
    p = (400.0, 300.0)
    assert ex.observe(HandSignals(hover_point=p), now=0.0) == []
    assert ex.observe(HandSignals(hover_point=p), now=0.3) == []
    assert ex.observe(HandSignals(hover_point=p), now=0.61) == [ExtraAction.DWELL_CLICK]
    # doesn't machine-gun while you keep holding
    assert ex.observe(HandSignals(hover_point=p), now=0.9) == []


def test_dwell_progress_climbs_then_clears_on_click():
    ex = make(dwell_seconds=0.6)
    p = (400.0, 300.0)
    ex.observe(HandSignals(hover_point=p), now=0.0)
    assert ex.dwell_progress == 0.0
    ex.observe(HandSignals(hover_point=p), now=0.3)
    assert 0.4 < ex.dwell_progress < 0.6  # about halfway
    ex.observe(HandSignals(hover_point=p), now=0.61)  # fires
    assert ex.dwell_progress == 0.0  # ring hidden once the click lands


def test_dwell_progress_is_zero_when_paused():
    ex = make(dwell_seconds=0.6, pause_hold_seconds=0.4)
    p = (400.0, 300.0)
    ex.observe(HandSignals(hover_point=p), now=0.0)
    ex.observe(HandSignals(hover_point=p), now=0.3)
    # pause: dwell progress must read 0 so the ring disappears while suspended
    ex.observe(HandSignals(palms_out=True), now=0.4)
    ex.observe(HandSignals(palms_out=True), now=0.81)
    assert ex.paused
    assert ex.dwell_progress == 0.0


def test_moving_away_rearms_the_dwell():
    ex = make(dwell_seconds=0.6)
    p = (400.0, 300.0)
    ex.observe(HandSignals(hover_point=p), now=0.0)
    ex.observe(HandSignals(hover_point=p), now=0.61)  # first click
    ex.observe(HandSignals(hover_point=(600.0, 300.0)), now=0.7)  # move away
    ex.observe(HandSignals(hover_point=(600.0, 300.0)), now=0.71)
    out = ex.observe(HandSignals(hover_point=(600.0, 300.0)), now=1.4)
    assert out == [ExtraAction.DWELL_CLICK]


# ---- pause ----

def test_palms_out_toggles_pause_and_swallows_everything():
    ex = make(pause_hold_seconds=0.4)
    ex.observe(HandSignals(palms_out=True), now=0.0)
    out = ex.observe(HandSignals(palms_out=True), now=0.41)
    assert out == [ExtraAction.PAUSE_ON]
    assert ex.paused
    # while paused, even a big zoom does nothing
    zoomed = ex.observe(HandSignals(palms_out=False, two_hand_pinch_span=999.0), now=0.5)
    assert zoomed == []


def test_second_palms_out_resumes():
    ex = make(pause_hold_seconds=0.4)
    ex.observe(HandSignals(palms_out=True), now=0.0)
    ex.observe(HandSignals(palms_out=True), now=0.41)  # pause on
    ex.observe(HandSignals(palms_out=False), now=0.5)  # drop pose
    ex.observe(HandSignals(palms_out=True), now=0.6)
    out = ex.observe(HandSignals(palms_out=True), now=1.01)
    assert out == [ExtraAction.PAUSE_OFF]
    assert not ex.paused


def test_resuming_from_pause_does_not_fire_a_stale_dwell_click():
    # regression: while paused, the dwell detector's feed() is never called
    # (observe() returns early), so its anchor/timer are frozen. if the pause
    # lasts longer than dwell_seconds, resuming used to make (now - _since)
    # read as already-elapsed, firing an instant unrequested click the frame
    # tracking resumes even though the user never actually held still.
    ex = make(pause_hold_seconds=0.4, dwell_seconds=0.6)
    point = (100.0, 100.0)
    ex.observe(HandSignals(hover_point=point), now=0.0)  # dwell anchor set
    ex.observe(HandSignals(palms_out=True), now=0.1)
    ex.observe(HandSignals(palms_out=True), now=0.51)  # pause on
    assert ex.paused
    # a long pause -- much longer than dwell_seconds -- during which the
    # dwell anchor/timer are frozen, not advanced
    ex.observe(HandSignals(palms_out=True), now=1.0)
    ex.observe(HandSignals(palms_out=False), now=1.1)  # drop pose
    ex.observe(HandSignals(palms_out=True), now=1.2)
    out_resume = ex.observe(HandSignals(palms_out=True), now=1.61)  # pause off
    assert out_resume == [ExtraAction.PAUSE_OFF]
    assert not ex.paused
    # first frame back, still hovering the same point: should just resume a
    # fresh dwell countdown, not instantly fire from the stale timer
    out = ex.observe(HandSignals(hover_point=point), now=1.62)
    assert out == []
    assert ex.dwell_progress < 1.0


def test_resuming_from_pause_does_not_fire_a_stale_confirm():
    # same staleness bug, for the thumbs-up confirm hold: a thumbs-up held
    # continuously through a pause used to "complete" instantly on resume
    # because its hold timer never got reset while paused.
    ex = make(pause_hold_seconds=0.4, confirm_hold_seconds=0.4)
    ex.observe(HandSignals(thumbs_up=True), now=0.0)  # confirm hold starts
    ex.observe(HandSignals(palms_out=True), now=0.1)
    ex.observe(HandSignals(palms_out=True), now=0.51)  # pause on
    assert ex.paused
    ex.observe(HandSignals(palms_out=True), now=1.0)
    ex.observe(HandSignals(palms_out=False), now=1.1)  # drop pose
    ex.observe(HandSignals(palms_out=True), now=1.2)
    out_resume = ex.observe(HandSignals(palms_out=True), now=1.61)  # pause off
    assert out_resume == [ExtraAction.PAUSE_OFF]
    assert not ex.paused
    out = ex.observe(HandSignals(thumbs_up=True), now=1.62)
    assert out == []


# ---- confirm ----

def test_thumbs_up_hold_confirms_once():
    ex = make(confirm_hold_seconds=0.4)
    ex.observe(HandSignals(thumbs_up=True), now=0.0)
    assert ex.observe(HandSignals(thumbs_up=True), now=0.41) == [ExtraAction.CONFIRM]
    assert ex.observe(HandSignals(thumbs_up=True), now=0.9) == []  # not again while held
    ex.observe(HandSignals(thumbs_up=False), now=1.0)  # drop
    ex.observe(HandSignals(thumbs_up=True), now=1.1)
    assert ex.observe(HandSignals(thumbs_up=True), now=1.6) == [ExtraAction.CONFIRM]


def test_disabled_detectors_stay_silent():
    ex = make(zoom_enabled=False, confirm_enabled=False)
    ex.observe(HandSignals(two_hand_pinch_span=100.0), now=0.0)
    assert ex.observe(HandSignals(two_hand_pinch_span=200.0), now=0.1) == []
    ex.observe(HandSignals(thumbs_up=True), now=0.0)
    assert ex.observe(HandSignals(thumbs_up=True), now=1.0) == []
