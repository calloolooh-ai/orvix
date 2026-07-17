"""
one_euro_filter.py

small vendored implementation of the One Euro Filter (Casiez, Roussel,
Vogel 2012, "1 euro filter: a simple speed-based low-pass filter for
noisy input in interactive systems").

why vendor this instead of adding a pypi dependency: it's about 30 lines of
actual logic and stable/finished math, not something that needs updates.
pulling in a whole package for it is more dependency surface than it's worth.

what it does, briefly: a plain low-pass filter has one fixed cutoff, so
you're always trading off jitter (when the hand is nearly still) against lag
(when the hand is moving fast), no single value is right for both. this
filter adapts its cutoff based on how fast the signal is currently changing:
slow movement -> aggressive smoothing (kills at-rest jitter), fast movement
-> light smoothing (stays responsive). exactly the tradeoff we care about
for cursor tracking.
"""

from __future__ import annotations

import math


def _smoothing_factor(t_e: float, cutoff: float) -> float:
    r = 2 * math.pi * cutoff * t_e
    return r / (r + 1)


def _exponential_smoothing(a: float, x: float, x_prev: float) -> float:
    return a * x + (1 - a) * x_prev


class OneEuroFilter:
    """
    one filter instance tracks one scalar value over time. for a 2D cursor
    position, use two instances (one per axis), see coord_mapper.py.

    min_cutoff: lower = more smoothing at low speed (less jitter when still,
        but more lag on slow deliberate moves)
    beta: higher = less smoothing at high speed (more responsive on fast
        moves, but can let more jitter through during quick motion)
    """

    def __init__(
        self,
        min_cutoff: float = 1.0,
        beta: float = 0.0,
        d_cutoff: float = 1.0,
    ):
        self.min_cutoff = min_cutoff
        self.beta = beta
        self.d_cutoff = d_cutoff

        self._x_prev: float | None = None
        self._dx_prev: float = 0.0
        self._t_prev: float | None = None

    def __call__(self, t: float, x: float) -> float:
        """feed the current timestamp (seconds) and raw value, get back the filtered value."""
        if self._t_prev is None:
            # first sample, nothing to smooth against yet
            self._x_prev = x
            self._t_prev = t
            return x

        t_e = t - self._t_prev
        if t_e <= 0:
            # duplicate/out-of-order timestamp, just return the last output
            # rather than dividing by zero in the smoothing factor
            return self._x_prev

        # estimate the signal's rate of change, smoothed on its own so the
        # speed estimate itself isn't jittery
        dx = (x - self._x_prev) / t_e
        a_d = _smoothing_factor(t_e, self.d_cutoff)
        dx_hat = _exponential_smoothing(a_d, dx, self._dx_prev)

        # cutoff adapts with speed: faster movement -> higher cutoff -> less smoothing
        cutoff = self.min_cutoff + self.beta * abs(dx_hat)
        a = _smoothing_factor(t_e, cutoff)
        x_hat = _exponential_smoothing(a, x, self._x_prev)

        self._x_prev = x_hat
        self._dx_prev = dx_hat
        self._t_prev = t

        return x_hat
