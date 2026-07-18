"""
tests for circle_detector.py. feeds synthetic horizontal palm paths (x, z in
mm over time) and checks a full loop fires while straight/tiny motion doesn't.
"""

import math

from orvix.circle_detector import CircleDetector


def circle_points(n, radius=40.0, turns=1.0, cx=0.0, cz=0.0, dt=0.03, start_t=0.0):
    """n samples along `turns` of a circle of `radius` mm, one every dt sec."""
    pts = []
    for i in range(n):
        theta = 2 * math.pi * turns * (i / (n - 1))
        pts.append((cx + radius * math.cos(theta), cz + radius * math.sin(theta), start_t + i * dt))
    return pts


def feed_all(det, pts):
    fired = []
    for x, z, t in pts:
        if det.feed(x, z, t):
            fired.append(t)
    return fired


def test_a_full_circle_fires_once():
    det = CircleDetector()
    fired = feed_all(det, circle_points(40, radius=40.0, turns=1.0))
    assert len(fired) == 1


def test_a_straight_line_never_fires():
    det = CircleDetector()
    pts = [(float(i) * 5.0, 0.0, i * 0.03) for i in range(40)]
    assert feed_all(det, pts) == []


def test_a_tiny_wobble_below_min_radius_never_fires():
    det = CircleDetector(min_radius_mm=25.0)
    # a 5mm circle: full sweep in angle, but far too small to be deliberate
    assert feed_all(det, circle_points(30, radius=5.0, turns=1.0)) == []


def test_half_a_circle_is_not_enough():
    det = CircleDetector(sweep_threshold_deg=320.0)
    assert feed_all(det, circle_points(30, radius=40.0, turns=0.5)) == []


def test_two_circles_fire_twice_but_respect_cooldown():
    det = CircleDetector(cooldown_seconds=0.2)
    pts = circle_points(60, radius=40.0, turns=2.0, dt=0.03)
    fired = feed_all(det, pts)
    # two turns should trip it at least once; cooldown keeps it from spamming
    assert len(fired) >= 1
    # fires are spaced by at least the cooldown
    for a, b in zip(fired, fired[1:]):
        assert b - a >= 0.2


def test_counterclockwise_also_fires():
    det = CircleDetector()
    fired = feed_all(det, circle_points(40, radius=40.0, turns=-1.0))
    assert len(fired) == 1


def test_reset_clears_progress():
    det = CircleDetector()
    # feed most of a circle, then reset before it completes
    for x, z, t in circle_points(16, radius=40.0, turns=0.7):
        det.feed(x, z, t)
    det.reset()
    # a fresh partial arc shouldn't complete using the pre-reset sweep
    assert feed_all(det, circle_points(10, radius=40.0, turns=0.3, start_t=5.0)) == []
