"""
tests for displays.py: turning a list of Quartz display rects into one
desktop bounding box. Quartz itself is monkeypatched out, this only needs to
be right about the geometry, not about talking to real hardware.
"""

import types

import orvix.displays as displays_mod


class _Point:
    def __init__(self, x, y):
        self.x = x
        self.y = y


class _Size:
    def __init__(self, width, height):
        self.width = width
        self.height = height


class _Rect:
    def __init__(self, x, y, width, height):
        self.origin = _Point(x, y)
        self.size = _Size(width, height)


def _patch_displays(monkeypatch, rects_by_id, active_ids, main_id=1):
    """rects_by_id: {display_id: (x, y, w, h)}. active_ids: which ids CGGetActiveDisplayList reports."""

    def fake_bounds(display_id):
        x, y, w, h = rects_by_id[display_id]
        return _Rect(x, y, w, h)

    monkeypatch.setattr(displays_mod.Quartz, "CGDisplayBounds", fake_bounds)
    monkeypatch.setattr(displays_mod.Quartz, "CGMainDisplayID", lambda: main_id)
    monkeypatch.setattr(displays_mod, "_active_display_ids", lambda: list(active_ids))


def test_single_display_is_its_own_bounds(monkeypatch):
    _patch_displays(monkeypatch, {1: (0.0, 0.0, 1920.0, 1080.0)}, active_ids=[1])
    bounds = displays_mod.get_desktop_bounds(multi_monitor=True)
    assert (bounds.origin_x, bounds.origin_y, bounds.width, bounds.height) == (0.0, 0.0, 1920.0, 1080.0)


def test_two_displays_side_by_side(monkeypatch):
    # main display at (0,0) 1920x1080, second display placed directly to its
    # right, same height
    _patch_displays(
        monkeypatch,
        {1: (0.0, 0.0, 1920.0, 1080.0), 2: (1920.0, 0.0, 1920.0, 1080.0)},
        active_ids=[1, 2],
    )
    bounds = displays_mod.get_desktop_bounds(multi_monitor=True)
    assert bounds.origin_x == 0.0
    assert bounds.origin_y == 0.0
    assert bounds.width == 3840.0
    assert bounds.height == 1080.0


def test_display_placed_left_of_main_gives_negative_origin(monkeypatch):
    # a display positioned to the left of main has negative x in Quartz's
    # global space, this is the case that broke a hard-coded 0 origin
    _patch_displays(
        monkeypatch,
        {1: (0.0, 0.0, 1920.0, 1080.0), 2: (-1920.0, 0.0, 1920.0, 1080.0)},
        active_ids=[1, 2],
    )
    bounds = displays_mod.get_desktop_bounds(multi_monitor=True)
    assert bounds.origin_x == -1920.0
    assert bounds.width == 3840.0


def test_display_placed_above_main_gives_negative_origin_y(monkeypatch):
    _patch_displays(
        monkeypatch,
        {1: (0.0, 0.0, 1920.0, 1080.0), 2: (0.0, -1080.0, 1920.0, 1080.0)},
        active_ids=[1, 2],
    )
    bounds = displays_mod.get_desktop_bounds(multi_monitor=True)
    assert bounds.origin_y == -1080.0
    assert bounds.height == 2160.0


def test_mismatched_display_heights_produce_a_bounding_box_not_a_sum(monkeypatch):
    # an L-shaped arrangement: main is taller than the second display, so the
    # bounding box's height is dictated by whichever display is offset
    # highest/lowest, not a naive sum of heights
    _patch_displays(
        monkeypatch,
        {1: (0.0, 0.0, 1920.0, 1080.0), 2: (1920.0, 300.0, 1280.0, 720.0)},
        active_ids=[1, 2],
    )
    bounds = displays_mod.get_desktop_bounds(multi_monitor=True)
    assert bounds.width == 3200.0
    assert bounds.height == 1080.0  # second display's 300..1020 fits inside main's 0..1080


def test_multi_monitor_false_ignores_secondary_displays_entirely(monkeypatch):
    _patch_displays(
        monkeypatch,
        {1: (0.0, 0.0, 1920.0, 1080.0), 2: (1920.0, 0.0, 2560.0, 1440.0)},
        active_ids=[1, 2],
        main_id=1,
    )
    bounds = displays_mod.get_desktop_bounds(multi_monitor=False)
    assert (bounds.origin_x, bounds.origin_y, bounds.width, bounds.height) == (0.0, 0.0, 1920.0, 1080.0)


def test_center_property_is_midpoint_of_the_bounds(monkeypatch):
    _patch_displays(monkeypatch, {1: (-500.0, 0.0, 1000.0, 500.0)}, active_ids=[1])
    bounds = displays_mod.get_desktop_bounds(multi_monitor=True)
    assert bounds.center == (0.0, 250.0)


def test_active_display_ids_falls_back_to_main_display_on_error(monkeypatch):
    def failing_list(*_args):
        return (1, None, 0)  # non-zero style error, no ids returned

    monkeypatch.setattr(displays_mod.Quartz, "CGGetActiveDisplayList", failing_list)
    monkeypatch.setattr(displays_mod.Quartz, "CGMainDisplayID", lambda: 7)
    ids = displays_mod._active_display_ids()
    assert ids == [7]
