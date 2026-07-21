"""
tests for calibration_viz.py: the ascii calibration grid. pure functions,
no terminal or AppKit needed, so this pins down the actual geometry (marker
placement, y inversion, degenerate spans) rather than just "it doesn't crash".
"""

from orvix.calibration_viz import (
    REFERENCE_X_RANGE,
    REFERENCE_Y_RANGE,
    BoundsTracker,
    coverage_rect,
    fraction_along,
    marker_fraction,
    render_ascii_grid,
)


# -- fraction_along, shared by the ascii grid and overlay.py's AppKit HUD --


def test_fraction_along_endpoints_and_midpoint():
    assert fraction_along(0.0, 0.0, 100.0) == 0.0
    assert fraction_along(100.0, 0.0, 100.0) == 1.0
    assert fraction_along(50.0, 0.0, 100.0) == 0.5


def test_fraction_along_clamps_outside_the_range():
    assert fraction_along(-50.0, 0.0, 100.0) == 0.0
    assert fraction_along(500.0, 0.0, 100.0) == 1.0


def test_fraction_along_degenerate_span_returns_midpoint():
    assert fraction_along(5.0, 5.0, 5.0) == 0.5


def grid_rows(text):
    """strip the border/footer, return just the interior grid rows."""
    lines = text.split("\n")
    # first line is the top border, last is the span footer, second-to-last
    # is the bottom border
    return lines[1:-2]


def find_marker(text):
    for r, line in enumerate(grid_rows(text)):
        c = line.find("@")
        if c != -1:
            return r, c - 1  # -1 for the leading "|"
    return None


def test_marker_at_center_of_span_lands_in_the_middle_of_the_grid():
    grid = render_ascii_grid(
        min_x=-100, max_x=100, min_y=100, max_y=300,
        current_x=0, current_y=200,
        width=40, height=10,
    )
    row, col = find_marker(grid)
    assert col == 20  # halfway across
    assert row == 4 or row == 5  # halfway down (10 rows, 0-indexed)


def test_marker_at_min_corner_lands_bottom_left():
    # min_x -> leftmost column. min_y -> bottom row, since leap y-up means
    # higher y is higher on screen (smaller row index), so min y is the
    # bottom, same inversion coord_mapper applies for the real cursor
    grid = render_ascii_grid(
        min_x=-100, max_x=100, min_y=100, max_y=300,
        current_x=-100, current_y=100,
        width=40, height=10,
    )
    row, col = find_marker(grid)
    assert col == 0
    assert row == 9


def test_marker_at_max_corner_lands_top_right():
    grid = render_ascii_grid(
        min_x=-100, max_x=100, min_y=100, max_y=300,
        current_x=100, current_y=300,
        width=40, height=10,
    )
    row, col = find_marker(grid)
    assert col == 39
    assert row == 0


def test_degenerate_span_does_not_raise_and_centers_the_marker():
    # a single sample: min == max on both axes, would divide by zero if not
    # guarded
    grid = render_ascii_grid(
        min_x=5, max_x=5, min_y=200, max_y=200,
        current_x=5, current_y=200,
        width=20, height=6,
    )
    row, col = find_marker(grid)
    assert col == 10
    assert row == 2  # height=6: n_cells//2=3, then flipped to height-1-3=2


def test_grid_dimensions_match_requested_size():
    grid = render_ascii_grid(
        min_x=0, max_x=10, min_y=0, max_y=10,
        current_x=5, current_y=5,
        width=30, height=8,
    )
    rows = grid_rows(grid)
    assert len(rows) == 8
    for line in rows:
        # "|" + width cells + "|"
        assert len(line) == 32


def test_footer_reports_the_span_in_mm():
    grid = render_ascii_grid(
        min_x=-100, max_x=100, min_y=150, max_y=400,
        current_x=0, current_y=275,
        width=40, height=10,
    )
    footer = grid.split("\n")[-1]
    assert "200mm" in footer
    assert "250mm" in footer


# -- BoundsTracker --


def test_tracker_has_no_data_before_first_update():
    tracker = BoundsTracker()
    assert not tracker.has_data
    assert tracker.n_samples == 0


def test_tracker_render_before_any_data_does_not_raise():
    tracker = BoundsTracker()
    text = tracker.render_ascii(width=20, height=6)
    assert "waiting" in text
    # same shape as a populated grid so redraw-in-place doesn't jump around
    assert len(text.split("\n")) == 8  # top border + 6 rows + bottom border


def test_tracker_expands_bounds_as_samples_come_in():
    tracker = BoundsTracker()
    tracker.update(0.0, 200.0)
    tracker.update(-50.0, 180.0)
    tracker.update(50.0, 220.0)

    assert tracker.min_x == -50.0
    assert tracker.max_x == 50.0
    assert tracker.min_y == 180.0
    assert tracker.max_y == 220.0
    assert tracker.n_samples == 3


def test_tracker_last_position_updates_each_call():
    tracker = BoundsTracker()
    tracker.update(0.0, 200.0)
    tracker.update(10.0, 210.0)
    assert (tracker.last_x, tracker.last_y) == (10.0, 210.0)


def test_tracker_render_reflects_current_bounds_and_marker():
    tracker = BoundsTracker()
    tracker.update(-100.0, 100.0)
    tracker.update(100.0, 300.0)  # current position, also sets the bounds
    text = tracker.render_ascii(width=40, height=10)
    row, col = find_marker(text)
    # last update was the max corner
    assert col == 39
    assert row == 0


# -- coverage_rect / marker_fraction: the AppKit HUD's data, scored against a
# fixed reference envelope rather than auto-scaled like the ascii grid --


def test_coverage_rect_is_none_before_any_samples():
    assert coverage_rect(BoundsTracker()) is None


def test_marker_fraction_is_none_before_any_samples():
    assert marker_fraction(BoundsTracker()) is None


def test_coverage_rect_grows_as_the_reference_envelope_fills():
    tracker = BoundsTracker()
    x_lo, x_hi = REFERENCE_X_RANGE
    y_lo, y_hi = REFERENCE_Y_RANGE

    tracker.update(x_lo, y_lo)
    small = coverage_rect(tracker)
    assert small == (0.0, 0.0, 0.0, 0.0)  # a single point, zero area

    tracker.update(x_hi, y_hi)
    full = coverage_rect(tracker)
    assert full == (0.0, 0.0, 1.0, 1.0)  # now spans the entire reference envelope


def test_coverage_rect_clamps_a_sweep_that_overshoots_the_reference():
    tracker = BoundsTracker()
    x_lo, x_hi = REFERENCE_X_RANGE
    y_lo, y_hi = REFERENCE_Y_RANGE
    # way outside the reference envelope on every side
    tracker.update(x_lo - 1000.0, y_lo - 1000.0)
    tracker.update(x_hi + 1000.0, y_hi + 1000.0)
    rect = coverage_rect(tracker)
    assert rect == (0.0, 0.0, 1.0, 1.0)  # clamped, not negative or past 1.0


def test_marker_fraction_tracks_the_most_recent_sample_only():
    tracker = BoundsTracker()
    x_lo, x_hi = REFERENCE_X_RANGE
    y_lo, y_hi = REFERENCE_Y_RANGE
    mid_x = (x_lo + x_hi) / 2
    mid_y = (y_lo + y_hi) / 2

    tracker.update(x_lo, y_lo)  # bounds now include the corner
    tracker.update(mid_x, mid_y)  # but the marker follows this, the latest
    assert marker_fraction(tracker) == (0.5, 0.5)
