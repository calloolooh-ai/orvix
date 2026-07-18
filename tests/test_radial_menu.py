"""
tests for radial_menu.py. drives the selection engine with made-up pointer
positions in screen pixels, no Leap or Cocoa involved. the wheel is centred
at (500, 500) throughout; wedges go clockwise from top (0 = up).
"""

import math

import pytest

from orvix.radial_menu import RadialMenu, RadialOutcome

ACTIONS = [
    "mission_control",  # 0 up
    "maximize",  # 1 up-right
    "app_switcher",  # 2 right
    "undo",  # 3 down-right
    "copy",  # 4 down
    "paste",  # 5 down-left
    "screenshot",  # 6 left
    "close",  # 7 up-left
]

CENTER = (500.0, 500.0)


def point_at(index: int, radius: float = 120.0) -> tuple[float, float]:
    """a pointer sitting squarely in wedge `index`, `radius` px from center."""
    angle = math.radians(-90 + index * 45)
    return (CENTER[0] + radius * math.cos(angle), CENTER[1] + radius * math.sin(angle))


def make_menu(**kw) -> RadialMenu:
    return RadialMenu(ACTIONS, dead_zone_px=55.0, dwell_seconds=0.6, **kw)


def test_pointer_maps_to_the_right_wedge():
    menu = make_menu()
    menu.open(CENTER, now=0.0)
    for i in range(len(ACTIONS)):
        upd = menu.update(point_at(i), pinching=False, now=0.0)
        assert upd.hovered_index == i, f"wedge {i} mishit"


def test_center_dead_zone_hovers_nothing():
    menu = make_menu()
    menu.open(CENTER, now=0.0)
    upd = menu.update((510.0, 505.0), pinching=False, now=0.0)  # ~11px from center
    assert upd.hovered_index is None


def test_pinch_fires_the_hovered_wedge():
    menu = make_menu()
    menu.open(CENTER, now=0.0)
    menu.update(point_at(4), pinching=False, now=0.0)  # hover "copy"
    upd = menu.update(point_at(4), pinching=True, now=0.05)
    assert upd.outcome == RadialOutcome.FIRED
    assert upd.fired_action == "copy"
    assert not menu.is_open  # closes itself on fire


def test_pinch_only_fires_on_the_closing_edge_not_every_held_frame():
    menu = make_menu()
    menu.open(CENTER, now=0.0)
    first = menu.update(point_at(2), pinching=True, now=0.0)
    assert first.outcome == RadialOutcome.FIRED
    # the menu is closed now; a held pinch on the next frame must not re-fire
    second = menu.update(point_at(2), pinching=True, now=0.1)
    assert second.outcome == RadialOutcome.NONE


def test_pinch_held_from_open_does_not_instantly_select():
    # seed the opening frame as already pinching: it should take a release +
    # re-pinch to select, not fire on frame one
    menu = make_menu()
    menu.open(CENTER, now=0.0, pinching=True)
    held = menu.update(point_at(1), pinching=True, now=0.02)
    assert held.outcome == RadialOutcome.NONE
    menu.update(point_at(1), pinching=False, now=0.04)  # release
    upd = menu.update(point_at(1), pinching=True, now=0.06)  # fresh pinch
    assert upd.outcome == RadialOutcome.FIRED
    assert upd.fired_action == "maximize"


def test_dwell_fires_after_the_hold_window():
    menu = make_menu()
    menu.open(CENTER, now=0.0)
    p = point_at(0)
    assert menu.update(p, pinching=False, now=0.0).outcome == RadialOutcome.NONE
    assert menu.update(p, pinching=False, now=0.3).outcome == RadialOutcome.NONE
    upd = menu.update(p, pinching=False, now=0.61)
    assert upd.outcome == RadialOutcome.FIRED
    assert upd.fired_action == "mission_control"


def test_dwell_reports_progress_while_holding():
    menu = make_menu()
    menu.open(CENTER, now=0.0)
    p = point_at(3)
    menu.update(p, pinching=False, now=0.0)
    upd = menu.update(p, pinching=False, now=0.3)  # half of 0.6s
    assert 0.4 < upd.dwell_progress < 0.6


def test_moving_to_another_wedge_restarts_the_dwell_clock():
    menu = make_menu()
    menu.open(CENTER, now=0.0)
    menu.update(point_at(0), pinching=False, now=0.0)
    menu.update(point_at(0), pinching=False, now=0.4)  # almost there on wedge 0
    # slide onto wedge 2; clock resets
    upd = menu.update(point_at(2), pinching=False, now=0.45)
    assert upd.dwell_progress == 0.0
    # would-be fire time for the old wedge passes, nothing fires yet
    assert menu.update(point_at(2), pinching=False, now=0.7).outcome == RadialOutcome.NONE
    # a full window on the new wedge does fire it
    assert menu.update(point_at(2), pinching=False, now=1.06).outcome == RadialOutcome.FIRED


def test_dropping_into_dead_zone_resets_dwell():
    menu = make_menu()
    menu.open(CENTER, now=0.0)
    menu.update(point_at(5), pinching=False, now=0.0)
    menu.update(point_at(5), pinching=False, now=0.5)  # nearly fired
    menu.update(CENTER, pinching=False, now=0.55)  # back to center
    # re-hover; the earlier 0.5s must not carry over
    menu.update(point_at(5), pinching=False, now=0.6)
    assert menu.update(point_at(5), pinching=False, now=1.0).outcome == RadialOutcome.NONE


def test_close_wedge_dismisses_rather_than_firing():
    menu = make_menu()
    menu.open(CENTER, now=0.0)
    upd = menu.update(point_at(7), pinching=True, now=0.0)  # "close"
    assert upd.outcome == RadialOutcome.DISMISSED
    assert upd.fired_action is None
    assert not menu.is_open


def test_pinch_in_dead_zone_dismisses():
    menu = make_menu()
    menu.open(CENTER, now=0.0)
    upd = menu.update((505.0, 500.0), pinching=True, now=0.0)
    assert upd.outcome == RadialOutcome.DISMISSED


def test_dwell_in_dead_zone_never_fires():
    menu = make_menu()
    menu.open(CENTER, now=0.0)
    for t in (0.0, 0.5, 1.0, 2.0):
        assert menu.update(CENTER, pinching=False, now=t).outcome == RadialOutcome.NONE
    assert menu.is_open  # still waiting


def test_cancel_closes_without_firing():
    menu = make_menu()
    menu.open(CENTER, now=0.0)
    menu.cancel()
    assert not menu.is_open
    assert menu.update(point_at(0), pinching=True, now=0.0).outcome == RadialOutcome.NONE


def test_empty_action_list_is_rejected():
    with pytest.raises(ValueError):
        RadialMenu([])
