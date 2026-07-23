"""
tests for overlay.py: OverlayController (radial wheel), DwellRingController
(dwell-click progress ring), and CalibrationOverlayController (calibration
sweep HUD). previously zero coverage despite being the largest untested
module in orvix/.

AppKit is real here, not mocked (same convention as test_gui.py -- this dev
machine has pyobjc, and building borderless NSWindows offscreen without
calling the Cocoa run loop works fine). what's out of scope: the actual
_WheelView/_RingView/_CalibrationHUDView drawRect_ pixel output -- that's
cosmetic AppKit drawing, not logic, and each view already wraps its _draw()
in a try/except so a draw bug can't crash the pipeline. what's in scope: the
safe-no-op-without-AppKit contract, show/hide state transitions, the
Quartz-to-Cocoa y-axis flip math in OverlayController._show, and the
warned-once exception swallowing every render() shares.
"""

import logging

import pytest

import orvix.overlay as overlay
from orvix.overlay import (
    CalibrationOverlayController,
    DwellRingController,
    OverlayController,
)
from orvix.shortcuts import RADIAL_SHORTCUTS


# -- availability reflects the real AppKit import state on this machine --


def test_available_matches_module_appkit_flag():
    assert OverlayController().available == overlay._APPKIT_OK
    assert CalibrationOverlayController().available == overlay._APPKIT_OK


def test_dwell_ring_has_no_available_property():
    # unlike the other two controllers, DwellRingController never exposes
    # `available` -- gui.py only ever calls .render() on it. document that
    # so a future refactor notices if this drifts.
    assert not hasattr(DwellRingController(), "available")


def test_wedge_labels_cover_every_valid_radial_action():
    # every shortcuts.RADIAL_SHORTCUTS name (including opt-in ones like
    # spotlight/force_quit/lock_screen, which config.py's radial_actions
    # validation accepts) plus "close" needs a real label here, or a wedge
    # drawn for one falls back to the raw action id instead of a title.
    for action in (*RADIAL_SHORTCUTS, "close"):
        assert action in overlay._LABELS


# -- OverlayController: hide-before-show is always safe --


def test_hiding_before_ever_shown_does_not_create_a_window():
    ctl = OverlayController()
    ctl.render(None)
    assert ctl._window is None


def test_render_none_after_a_show_hides_without_crashing():
    ctl = OverlayController()
    state = {"actions": ["copy", "paste"], "hovered": 0, "progress": 0.0}
    ctl.render(state)
    assert ctl._window is not None
    ctl.render(None)  # must not raise
    assert ctl._window.isVisible() is False


def test_show_flips_quartz_y_into_cocoa_bottom_left_coords():
    ctl = OverlayController()
    ctl._ensure_window()
    ctl._screen_height = 1000.0
    state = {"actions": ["copy"], "hovered": None, "progress": 0.0, "center": (400.0, 250.0)}
    ctl._show(state)
    # cocoa_y = screen_height - cy = 1000 - 250 = 750; window origin is
    # centered on that point minus half the box size.
    origin = ctl._window.frame().origin
    box = overlay._BOX
    assert origin.x == pytest.approx(400.0 - box / 2.0)
    assert origin.y == pytest.approx(750.0 - box / 2.0)


def test_repeated_show_reuses_the_same_window():
    ctl = OverlayController()
    state = {"actions": ["copy"], "hovered": None, "progress": 0.0, "center": (0.0, 0.0)}
    ctl.render(state)
    first_window = ctl._window
    ctl.render(state)
    assert ctl._window is first_window


def test_a_broken_state_dict_is_swallowed_not_raised(caplog):
    ctl = OverlayController()
    with caplog.at_level(logging.WARNING, logger="orvix.overlay"):
        ctl.render({"actions": ["copy"]})  # missing required "center" key -> KeyError inside _show
    assert ctl._warned is True
    assert any("radial overlay failed to draw" in r.message for r in caplog.records)


def test_a_second_failure_logs_quietly_not_as_a_repeat_warning(caplog):
    ctl = OverlayController()
    ctl.render({"actions": ["copy"]})  # first failure -> warning
    caplog.clear()
    with caplog.at_level(logging.DEBUG, logger="orvix.overlay"):
        ctl.render({"actions": ["copy"]})  # second failure -> debug only
    assert not any(r.levelno == logging.WARNING for r in caplog.records)
    assert any(r.levelno == logging.DEBUG for r in caplog.records)


# -- DwellRingController: progress-driven show/hide --


def test_zero_or_none_progress_hides_without_crashing():
    ctl = DwellRingController()
    ctl.render(0)
    ctl.render(None)
    assert ctl._window is None


def test_positive_progress_shows_and_sets_view_progress():
    ctl = DwellRingController()
    ctl.render(0.4)
    assert ctl._window is not None
    assert ctl._view._progress == pytest.approx(0.4)


def test_ring_progress_is_clamped_into_zero_one():
    ctl = DwellRingController()
    ctl.render(5.0)
    assert ctl._view._progress == 1.0


def test_render_progress_then_zero_hides_the_ring():
    ctl = DwellRingController()
    ctl.render(0.5)
    ctl.render(0)
    assert ctl._window.isVisible() is False


def test_dwell_ring_swallows_render_failures(caplog):
    ctl = DwellRingController()
    with caplog.at_level(logging.WARNING, logger="orvix.overlay"):
        # a non-numeric progress can't be coerced to float inside _RingView.set_progress
        ctl.render("not-a-number")
    assert ctl._warned is True
    assert any("dwell ring failed to draw" in r.message for r in caplog.records)


# -- CalibrationOverlayController: HUD show/hide + top-of-screen placement --


def test_calibration_hud_hides_before_ever_shown():
    ctl = CalibrationOverlayController()
    ctl.render(None)
    assert ctl._window is None


def test_calibration_hud_shows_and_parks_near_top_of_main_screen():
    ctl = CalibrationOverlayController()
    ctl.render({"rect": (0.1, 0.1, 0.5, 0.5), "marker": (0.3, 0.3), "fraction": 0.5, "n_samples": 12})
    assert ctl._window is not None
    import AppKit

    screen = AppKit.NSScreen.mainScreen().frame()
    origin = ctl._window.frame().origin
    expected_x = screen.origin.x + (screen.size.width - overlay._HUD_W) / 2.0
    expected_y = screen.origin.y + screen.size.height - overlay._HUD_H - 60.0
    assert origin.x == pytest.approx(expected_x)
    assert origin.y == pytest.approx(expected_y)


def test_calibration_hud_defaults_missing_optional_state_fields():
    ctl = CalibrationOverlayController()
    # "rect" and "marker" are optional (None is a valid "no data yet" state);
    # "fraction"/"n_samples" fall back to 0.0/0 via .get() defaults.
    ctl.render({})
    assert ctl._view._rect is None
    assert ctl._view._marker is None
    assert ctl._view._fraction == 0.0
    assert ctl._view._n_samples == 0


def test_calibration_hud_render_none_hides_after_a_show():
    ctl = CalibrationOverlayController()
    ctl.render({"rect": None, "marker": None, "fraction": 0.0, "n_samples": 0})
    ctl.render(None)
    assert ctl._window.isVisible() is False
