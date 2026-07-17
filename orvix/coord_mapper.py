"""
coord_mapper.py

turns a hand's position in Leap's 3D space (millimeters) into 2D screen
pixel coordinates. two strategies, picked by settings.cursor_mode:

CoordMapper (absolute): your position inside the calibration box IS your
    position on screen. point at a corner, cursor's there. depends entirely
    on the box being right for you, hence calibration.

RelativeCoordMapper: the cursor moves by however far your hand moved, like
    a trackpad. ignores the calibration box completely, so there's nothing
    to calibrate and no dead edges. worth knowing why that last part
    matters: the leap sees through a pyramid, not a box, so it can see
    further left/right the higher your hand is. an absolute box therefore
    has corners you literally cannot reach when your hand is low, and the
    screen edges stop responding. relative mode has no such geometry to get
    wrong.

both smooth with a One Euro Filter so the cursor doesn't jitter at rest but
stays responsive on fast moves, see one_euro_filter.py for why that filter.
in relative mode the filtering happens on the raw mm position before we
difference it, since differencing noise is what makes a relative cursor
drift on its own.

pure logic module, no hardware or macOS calls, unit testable on its own.
"""

from __future__ import annotations

import math
from typing import Protocol

from orvix.config import CalibrationBox, Settings
from orvix.one_euro_filter import OneEuroFilter


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


class Mapper(Protocol):
    """what main.py needs from a mapper, whichever strategy is in use."""

    def map_to_screen(
        self, palm_position: tuple[float, float, float], timestamp: float
    ) -> tuple[int, int]: ...

    def reset(self) -> None: ...


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

    def reset(self) -> None:
        """
        no-op: absolute mapping has no carried-over state to invalidate when
        the hand disappears, the next position stands on its own. exists so
        callers can treat both mappers the same.
        """


class RelativeCoordMapper:
    """
    trackpad-style mapping. holds the cursor position itself and nudges it
    by the hand's movement each frame, so the calibration box is irrelevant.

    one instance per session, same as CoordMapper: the filters and the
    cursor position both need continuity across frames.
    """

    def __init__(
        self,
        screen_width: int,
        screen_height: int,
        settings: Settings,
        start: tuple[float, float] | None = None,
    ):
        self._screen_width = screen_width
        self._screen_height = screen_height
        self._settings = settings

        # park it in the middle to begin with, it'll be wherever you push it
        # from there
        self._x, self._y = start if start is not None else (screen_width / 2, screen_height / 2)

        self._prev: tuple[float, float] | None = None
        self._filter_x = OneEuroFilter(
            min_cutoff=settings.one_euro_min_cutoff, beta=settings.one_euro_beta
        )
        self._filter_y = OneEuroFilter(
            min_cutoff=settings.one_euro_min_cutoff, beta=settings.one_euro_beta
        )
        self._t_prev: float | None = None

    def _gain(self, speed_mm_s: float) -> float:
        """
        px per mm at this hand speed. flat below slow_speed, flat above
        fast_speed, linear between. the point is that slow deliberate moves
        stay precise while fast ones can still cross a wide screen.
        """
        s = self._settings
        if speed_mm_s <= s.relative_slow_speed:
            return s.relative_min_gain
        if speed_mm_s >= s.relative_fast_speed:
            return s.relative_max_gain
        t = (speed_mm_s - s.relative_slow_speed) / (s.relative_fast_speed - s.relative_slow_speed)
        return s.relative_min_gain + t * (s.relative_max_gain - s.relative_min_gain)

    def map_to_screen(
        self, palm_position: tuple[float, float, float], timestamp: float
    ) -> tuple[int, int]:
        x_mm, y_mm, _z = palm_position

        # smooth the raw mm first, then difference. doing it the other way
        # round would difference the noise and the cursor would wander off
        # on its own while your hand sits still.
        fx = self._filter_x(timestamp, x_mm)
        fy = self._filter_y(timestamp, y_mm)

        if self._prev is None or self._t_prev is None:
            # first frame, or first frame after the hand came back. anchor
            # here and don't move, otherwise the cursor jumps by however far
            # your hand travelled while it was out of view.
            self._prev = (fx, fy)
            self._t_prev = timestamp
            return int(round(self._x)), int(round(self._y))

        dt = timestamp - self._t_prev
        if dt <= 0:
            return int(round(self._x)), int(round(self._y))

        dx_mm = fx - self._prev[0]
        dy_mm = fy - self._prev[1]
        self._prev = (fx, fy)
        self._t_prev = timestamp

        speed = math.hypot(dx_mm, dy_mm) / dt
        gain = self._gain(speed)

        self._x += dx_mm * gain
        # leap y goes up, screen y goes down, so this one flips
        self._y -= dy_mm * gain

        self._x = _clamp(self._x, 0, self._screen_width)
        self._y = _clamp(self._y, 0, self._screen_height)

        return int(round(self._x)), int(round(self._y))

    def reset(self) -> None:
        """
        hand's gone. drop the anchor so that when it comes back we start
        from wherever it reappears instead of applying one huge delta. this
        is also what lets you re-centre: pull your hand away, put it back
        somewhere comfier, carry on from the same cursor position.
        """
        self._prev = None
        self._t_prev = None


def make_mapper(settings: Settings, screen_width: int, screen_height: int) -> Mapper:
    """pick a mapper based on settings.cursor_mode."""
    if settings.cursor_mode == "relative":
        return RelativeCoordMapper(screen_width, screen_height, settings)
    return CoordMapper(settings.calibration, screen_width, screen_height, settings)
