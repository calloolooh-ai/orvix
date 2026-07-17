"""
coord_mapper.py

maps a hand's position in Leap's 3D coordinate space (millimeters) into 2D
screen pixel coordinates, using the calibrated interaction box from config,
then smooths the result with a One Euro Filter so the cursor doesn't jitter
at rest but still stays responsive on fast moves (see one_euro_filter.py for
why that filter specifically).

pure logic module, no hardware or macOS calls, unit testable on its own.
"""

from __future__ import annotations

from orvix.config import CalibrationBox, Settings
from orvix.one_euro_filter import OneEuroFilter


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _map_range(value: float, in_min: float, in_max: float, out_min: float, out_max: float) -> float:
    """linearly map value from [in_min, in_max] into [out_min, out_max], clamped to the output range."""
    if in_max == in_min:
        # degenerate calibration box (e.g. calibration.py never ran), avoid
        # a divide by zero and just park in the middle of the output range
        return (out_min + out_max) / 2

    t = (value - in_min) / (in_max - in_min)
    t = _clamp(t, 0.0, 1.0)
    return out_min + t * (out_max - out_min)


class CoordMapper:
    """
    one instance per session (or per tracked hand, if we ever support
    tracking two hands independently). holds the One Euro Filter state,
    which needs continuity across frames, so don't recreate this mid-session.
    """

    def __init__(
        self,
        calibration: CalibrationBox,
        screen_width: int,
        screen_height: int,
        settings: Settings,
    ):
        self._calibration = calibration
        self._screen_width = screen_width
        self._screen_height = screen_height

        self._filter_x = OneEuroFilter(
            min_cutoff=settings.one_euro_min_cutoff, beta=settings.one_euro_beta
        )
        self._filter_y = OneEuroFilter(
            min_cutoff=settings.one_euro_min_cutoff, beta=settings.one_euro_beta
        )

    def map_to_screen(
        self, palm_position: tuple[float, float, float], timestamp: float
    ) -> tuple[int, int]:
        """
        palm_position is Leap-space [x, y, z] in mm. timestamp is seconds
        (frame timestamp or time.monotonic(), just needs to be monotonically
        increasing and in consistent units for the filter's speed estimate).

        returns (x, y) screen pixel coords, clamped to the screen bounds and
        smoothed. note Leap's y axis (height above sensor) maps to screen y
        inverted: higher hand -> higher on screen -> smaller pixel y.
        """
        x_mm, y_mm, _z_mm = palm_position
        cal = self._calibration

        raw_x = _map_range(x_mm, cal.x_min, cal.x_max, 0, self._screen_width)
        # inverted on purpose, see docstring: bigger leap y (hand higher up)
        # should land near the top of the screen, i.e. smaller pixel y
        raw_y = _map_range(y_mm, cal.y_min, cal.y_max, self._screen_height, 0)

        smoothed_x = self._filter_x(timestamp, raw_x)
        smoothed_y = self._filter_y(timestamp, raw_y)

        clamped_x = _clamp(smoothed_x, 0, self._screen_width)
        clamped_y = _clamp(smoothed_y, 0, self._screen_height)

        return int(round(clamped_x)), int(round(clamped_y))
