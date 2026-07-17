"""
tests for the vendored One Euro Filter, the smoothing behavior coord_mapper.py
relies on for jitter-free-but-responsive cursor tracking.
"""

from orvix.one_euro_filter import OneEuroFilter


def test_first_call_returns_input_unchanged():
    f = OneEuroFilter()
    assert f(0.0, 5.0) == 5.0


def test_smooths_a_jittery_near_constant_signal():
    f = OneEuroFilter(min_cutoff=1.0, beta=0.007)
    f(0.0, 100.0)
    # small back-and-forth noise around 100, like a hand held roughly still
    outputs = []
    t = 0.0
    for noisy_value in [101.0, 99.0, 100.5, 99.5, 100.2]:
        t += 0.01
        outputs.append(f(t, noisy_value))

    # filtered output should have less spread than the raw noisy input
    assert max(outputs) - min(outputs) < max([101.0, 99.0, 100.5, 99.5, 100.2]) - min(
        [101.0, 99.0, 100.5, 99.5, 100.2]
    )


def test_tracks_a_fast_consistent_move_without_huge_lag():
    f = OneEuroFilter(min_cutoff=1.0, beta=0.007)
    t = 0.0
    f(t, 0.0)
    # simulate a hand sweeping steadily from 0 to 100 over half a second
    value = 0.0
    for _ in range(50):
        t += 0.01
        value += 2.0
        output = f(t, value)
    # after a sustained fast move, filtered output should be close to the
    # real value, not lagging way behind
    assert abs(output - value) < 15


def test_duplicate_timestamp_does_not_crash():
    f = OneEuroFilter()
    f(1.0, 10.0)
    # same timestamp twice, should return last output instead of dividing by zero
    result = f(1.0, 20.0)
    assert result == 10.0
