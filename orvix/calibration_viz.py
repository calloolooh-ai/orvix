"""
calibration_viz.py

turns the raw stream of palm samples calibration.py collects into something
you can actually watch happen: an ASCII grid (terminal) tracing your swept
envelope live, plus the AppKit overlay in overlay.py draws the same data as
an on-screen HUD.

the old calibration UX just showed a percent-complete bar, which tells you
time is passing but nothing about whether you're actually covering your
comfortable range, or all bunched in one corner. watching the box fill in
live answers that as you go, instead of finding out after the sweep ends
that one axis came out too narrow (build_box already refuses those, but
"try again" without knowing *why* it was too narrow isn't a great loop).

pure logic module, no terminal or AppKit code here, so it's unit testable
without either. calibration.py's terminal flow and overlay.py's HUD window
both consume BoundsTracker so they can't disagree about what's been swept.
"""

from __future__ import annotations

import dataclasses

# leap's y axis increases upward (hand higher = bigger y); the grid's rows
# are laid out screen-style (row 0 = top), so higher y has to land in a
# smaller row index or the picture would be upside down relative to the
# calibration box's actual y-inverted mapping onto the screen (coord_mapper
# does the same flip for the same reason).

_EMPTY = "."
_FILLED = "#"
_MARKER = "@"


@dataclasses.dataclass
class BoundsTracker:
    """
    running min/max of every (x, y) sample seen so far, plus the most recent
    one. this is deliberately just the reduction calibration.py's build_box
    already needs (min/max per axis), not a full history, so it stays cheap
    to update at 100+ samples/sec.
    """

    min_x: float | None = None
    max_x: float | None = None
    min_y: float | None = None
    max_y: float | None = None
    last_x: float | None = None
    last_y: float | None = None
    n_samples: int = 0

    @property
    def has_data(self) -> bool:
        return self.n_samples > 0

    def update(self, x: float, y: float) -> None:
        self.min_x = x if self.min_x is None else min(self.min_x, x)
        self.max_x = x if self.max_x is None else max(self.max_x, x)
        self.min_y = y if self.min_y is None else min(self.min_y, y)
        self.max_y = y if self.max_y is None else max(self.max_y, y)
        self.last_x = x
        self.last_y = y
        self.n_samples += 1

    def render_ascii(self, width: int = 44, height: int = 12) -> str:
        """
        a bordered ascii grid: the observed x/y span, filled in with a
        rectangle outline, and '@' marking the most recent sample. returns
        a placeholder frame (same size, so redraw-in-place doesn't jump)
        when there's no data yet.
        """
        if not self.has_data:
            return _empty_frame(width, height, "waiting for a hand...")

        return render_ascii_grid(
            self.min_x, self.max_x, self.min_y, self.max_y,
            self.last_x, self.last_y,
            width=width, height=height,
        )


def _empty_frame(width: int, height: int, message: str) -> str:
    top = "+" + "-" * width + "+"
    pad = max(0, (width - len(message)) // 2)
    middle_row = "|" + " " * pad + message + " " * (width - pad - len(message)) + "|"
    blank_row = "|" + _EMPTY * width + "|"
    rows = [top]
    mid = height // 2
    for row in range(height):
        rows.append(middle_row if row == mid else blank_row)
    rows.append(top)
    return "\n".join(rows)


def fraction_along(value: float, lo: float, hi: float) -> float:
    """
    where value sits in [lo, hi] as a 0..1 fraction, clamped. shared by the
    ascii grid (scaled to a cell index below) and overlay.py's AppKit HUD
    (scaled to a pixel offset instead), so the two live views can't drift
    apart on where a given sample actually lands.

    a degenerate span (single sample, or every sample identical) returns
    0.5 rather than dividing by zero, i.e. park it in the middle.
    """
    if hi <= lo:
        return 0.5
    t = (value - lo) / (hi - lo)
    return max(0.0, min(1.0, t))


def _to_cell(value: float, lo: float, hi: float, n_cells: int) -> int:
    """map value in [lo, hi] to a cell index in [0, n_cells - 1], clamped."""
    t = fraction_along(value, lo, hi)
    return min(n_cells - 1, int(t * n_cells))


# a generous reference envelope to score coverage against: wider than
# CalibrationBox's defaults (x +-150, y 150-400) so a solid sweep visibly
# fills most, but not quite all, of the HUD -- if it filled completely at
# exactly the default box there'd be nothing left to show growing once
# you're already covering a typical range. only used by coverage_rect() /
# marker_fraction() below (the AppKit overlay), not the ascii grid, which
# auto-scales to whatever's been swept so far instead of a fixed reference.
REFERENCE_X_RANGE = (-180.0, 180.0)
REFERENCE_Y_RANGE = (120.0, 420.0)


def coverage_rect(
    tracker: BoundsTracker,
    x_range: tuple[float, float] = REFERENCE_X_RANGE,
    y_range: tuple[float, float] = REFERENCE_Y_RANGE,
) -> tuple[float, float, float, float] | None:
    """
    the observed (min_x..max_x, min_y..max_y) envelope expressed as
    (left, bottom, width, height) fractions of the reference range, or None
    before any samples arrive. an AppKit view scales this into its own pixel
    box to draw a rectangle that visibly grows as you sweep; kept as plain
    fractions here so the geometry is testable without AppKit installed.
    """
    if not tracker.has_data:
        return None
    x_lo, x_hi = x_range
    y_lo, y_hi = y_range
    left = fraction_along(tracker.min_x, x_lo, x_hi)
    right = fraction_along(tracker.max_x, x_lo, x_hi)
    bottom = fraction_along(tracker.min_y, y_lo, y_hi)
    top = fraction_along(tracker.max_y, y_lo, y_hi)
    return (left, bottom, right - left, top - bottom)


def marker_fraction(
    tracker: BoundsTracker,
    x_range: tuple[float, float] = REFERENCE_X_RANGE,
    y_range: tuple[float, float] = REFERENCE_Y_RANGE,
) -> tuple[float, float] | None:
    """where the most recent sample sits in the reference range, as (x, y) fractions, or None before any samples."""
    if not tracker.has_data:
        return None
    return (fraction_along(tracker.last_x, *x_range), fraction_along(tracker.last_y, *y_range))


def render_ascii_grid(
    min_x: float,
    max_x: float,
    min_y: float,
    max_y: float,
    current_x: float,
    current_y: float,
    width: int = 44,
    height: int = 12,
) -> str:
    """
    pure function: turn the observed (min_x..max_x, min_y..max_y) envelope
    and the current position into a bordered ascii grid. the whole grid IS
    the observed span (it grows to fit what you've swept, there's no fixed
    scale), so the border itself traces your envelope and '@' shows where
    you are inside it right now.
    """
    grid = [[_EMPTY for _ in range(width)] for _ in range(height)]

    col = _to_cell(current_x, min_x, max_x, width)
    # leap y up = higher on screen = smaller row index, same inversion
    # coord_mapper applies when it maps the calibration box onto the screen
    row = height - 1 - _to_cell(current_y, min_y, max_y, height)
    grid[row][col] = _MARKER

    lines = ["+" + "-" * width + "+"]
    for r in grid:
        lines.append("|" + "".join(r) + "|")
    lines.append("+" + "-" * width + "+")

    span = f" x {max_x - min_x:.0f}mm  y {max_y - min_y:.0f}mm "
    lines.append(span.center(width + 2, "-"))
    return "\n".join(lines)
