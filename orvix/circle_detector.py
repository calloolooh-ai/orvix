"""
circle_detector.py

spots the "draw a circle in the air" gesture that opens the radial menu
(gesture 12). you circle your hand roughly parallel to the desk, so the
motion traces a loop in the x (left-right) and z (depth) plane while height
barely changes; we watch those two axes and fire once you've swept most of a
full turn.

pure and stateful like the other detectors: feed it (x, z, time) per frame,
it returns True on the single frame a circle completes. no Leap or Cocoa, so
it's testable against synthetic loops and straight lines.
"""

from __future__ import annotations

import collections
import math


def _normalize(angle: float) -> float:
    """wrap to (-pi, pi] so a step across the atan2 seam doesn't look huge."""
    return (angle + math.pi) % (2 * math.pi) - math.pi


class CircleDetector:
    def __init__(
        self,
        *,
        window_seconds: float = 1.5,
        min_points: int = 5,
        min_radius_mm: float = 35.0,
        sweep_threshold_deg: float = 400.0,
        cooldown_seconds: float = 0.8,
    ):
        self._window = window_seconds
        self._min_points = min_points
        self._min_radius = min_radius_mm
        self._threshold = math.radians(sweep_threshold_deg)
        self._cooldown = cooldown_seconds

        self._pts: collections.deque[tuple[float, float, float]] = collections.deque()
        self._cooldown_until: float | None = None

    def reset(self) -> None:
        """drop all state, e.g. when the hand leaves view."""
        self._pts.clear()
        self._cooldown_until = None

    def feed(self, x: float, z: float, t: float) -> bool:
        """
        add one frame's horizontal palm position. returns True exactly on the
        frame the buffered path winds a full-ish turn around its own centre,
        then arms a short cooldown so one circle can't open the menu twice.
        """
        self._pts.append((x, z, t))
        while self._pts and t - self._pts[0][2] > self._window:
            self._pts.popleft()

        if self._cooldown_until is not None and t < self._cooldown_until:
            return False

        if len(self._pts) < self._min_points:
            return False

        # centre of the whole buffered path. computing the winding across the
        # entire buffer each frame (rather than one incremental step) is what
        # makes this robust: while the loop is only half drawn the centroid
        # sits off to one side, so a per-step angle around it would be wrong.
        # measured across the full buffer, the net turn is what it should be.
        n = len(self._pts)
        cx = sum(p[0] for p in self._pts) / n
        cz = sum(p[1] for p in self._pts) / n

        # too small a loop is just jitter on a still hand, not a real circle.
        radius = sum(math.hypot(p[0] - cx, p[1] - cz) for p in self._pts) / n
        if radius < self._min_radius:
            return False

        winding = 0.0
        prev = math.atan2(self._pts[0][1] - cz, self._pts[0][0] - cx)
        for px, pz, _ in list(self._pts)[1:]:
            cur = math.atan2(pz - cz, px - cx)
            winding += _normalize(cur - prev)
            prev = cur

        if abs(winding) >= self._threshold:
            self._pts.clear()
            self._cooldown_until = t + self._cooldown
            return True
        return False
