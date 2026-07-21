"""
tests for perf.py: the lag/jitter/throughput measurements scripts/
profile_pipeline.py reports.

wall-clock timing is inherently noisy across machines, so these
deliberately avoid asserting on absolute thresholds ("must be under Xms") --
that's exactly the kind of assertion that's fine on a dev laptop and flaky
on a loaded CI runner. what's actually worth pinning down and safe to assert
on everywhere: the *relative* tradeoff (higher beta = less lag, more
jitter, which is the whole point of the filter, see one_euro_filter.py) and
that the benchmark functions run cleanly and return well-formed numbers.
"""

import math

from orvix.perf import (
    benchmark_dispatch_throughput,
    measure_rest_jitter,
    measure_step_lag,
)


# -- measure_step_lag --


def test_step_lag_decreases_as_beta_increases():
    # this is the exact tradeoff config.py's one_euro_beta comment
    # describes: higher beta means less smoothing at speed, so the filter
    # catches up to a step change faster
    low_beta = measure_step_lag(min_cutoff=1.0, beta=0.007)
    high_beta = measure_step_lag(min_cutoff=1.0, beta=0.15)
    assert high_beta.lag_ms < low_beta.lag_ms


def test_zero_beta_is_a_plain_fixed_cutoff_filter_slowest_of_all():
    # beta=0 means the cutoff never adapts with speed at all, so it should
    # be at least as slow as any beta>0 at the same min_cutoff
    zero_beta = measure_step_lag(min_cutoff=1.0, beta=0.0)
    some_beta = measure_step_lag(min_cutoff=1.0, beta=0.05)
    assert zero_beta.lag_ms >= some_beta.lag_ms


def test_step_lag_result_carries_the_params_it_was_measured_with():
    result = measure_step_lag(min_cutoff=1.0, beta=0.05)
    assert result.min_cutoff == 1.0
    assert result.beta == 0.05


def test_step_lag_is_never_negative():
    result = measure_step_lag(min_cutoff=1.0, beta=0.05)
    assert result.lag_ms >= 0.0


def test_step_lag_gives_up_cleanly_if_it_never_settles():
    # an absurdly low max_frames should hit the "never settled" path rather
    # than looping forever or raising
    result = measure_step_lag(min_cutoff=1.0, beta=0.0, max_frames=1)
    assert result.lag_ms == float("inf")


# -- measure_rest_jitter --


def test_rest_jitter_is_reproducible_with_a_fixed_seed():
    a = measure_rest_jitter(min_cutoff=1.0, beta=0.05, seed=42)
    b = measure_rest_jitter(min_cutoff=1.0, beta=0.05, seed=42)
    assert a.peak_deviation == b.peak_deviation


def test_rest_jitter_grows_with_beta_at_fixed_min_cutoff():
    # higher beta lets more noise-driven "speed" through as real signal,
    # which means less smoothing and more wobble at rest -- the cost side
    # of the same tradeoff test_step_lag_decreases_as_beta_increases checks
    low = measure_rest_jitter(min_cutoff=1.0, beta=0.0, seed=1)
    high = measure_rest_jitter(min_cutoff=1.0, beta=0.5, seed=1)
    assert high.peak_deviation >= low.peak_deviation


def test_rest_jitter_is_bounded_by_input_noise_magnitude():
    # a low-pass filter can't amplify noise beyond what's physically
    # plausible for reasonable filter params; loosely bounded (some
    # generous headroom) just to catch a badly broken filter, not to pin an
    # exact number
    result = measure_rest_jitter(min_cutoff=1.0, beta=0.05, noise_std=1.0, seed=0)
    assert 0.0 <= result.peak_deviation < 10.0


def test_rest_jitter_zero_noise_is_zero_jitter():
    result = measure_rest_jitter(min_cutoff=1.0, beta=0.05, noise_std=0.0)
    assert result.peak_deviation == 0.0


# -- benchmark_dispatch_throughput --


def test_throughput_report_covers_every_requested_frame():
    report = benchmark_dispatch_throughput(n_frames=200)
    assert report.n_frames == 200


def test_throughput_percentiles_are_ordered():
    report = benchmark_dispatch_throughput(n_frames=500)
    assert report.p50_ms <= report.p95_ms <= report.p99_ms


def test_throughput_timings_are_all_non_negative_and_finite():
    report = benchmark_dispatch_throughput(n_frames=200)
    for value in (report.mean_ms, report.p50_ms, report.p95_ms, report.p99_ms):
        assert value >= 0.0
        assert math.isfinite(value)


def test_throughput_budget_matches_target_fps():
    report = benchmark_dispatch_throughput(n_frames=50, target_fps=50.0)
    assert report.budget_ms == 20.0  # 1000 / 50


def test_within_budget_fraction_is_a_valid_fraction():
    report = benchmark_dispatch_throughput(n_frames=200)
    assert 0.0 <= report.within_budget_fraction <= 1.0
