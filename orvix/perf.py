"""
perf.py

repeatable measurements for the numbers that were previously just one-off
manual measurements recorded in a comment (see config.py's one_euro_beta
and drag_hold_seconds docstrings: "took ~107ms", "568 drag frames against
only 6 pinches"). nobody could rerun those after touching the filter or the
dispatch path; this makes them a function call instead.

two things worth measuring for a gesture-to-cursor pipeline:
  - lag: how long the filtered cursor takes to catch up once the hand
    actually moves (measure_step_lag)
  - jitter: how much the filtered cursor wobbles when the hand is actually
    still, i.e. how much sensor noise leaks through (measure_rest_jitter)
one_euro_min_cutoff/one_euro_beta trade these two off against each other,
that's the whole point of the filter (see one_euro_filter.py's docstring).

also benchmarks the per-frame CPU cost of gesture_interpreter + coord_mapper
back to back (benchmark_dispatch_throughput). the real pipeline has no fps
cap of its own, it just processes frames as fast as leapd delivers them, but
100fps is a reasonable stand-in for how often a real device streams frames,
which puts a rough 10ms/frame budget on this benchmark. worth knowing how
much of that the pure interpret+map cost actually uses, so a future heavier
gesture doesn't quietly eat into headroom nobody's watching.

pure/synthetic throughout: no leapd, no real hand, no Quartz. drives
OneEuroFilter/GestureInterpreter/CoordMapper directly with generated input,
same objects the real pipeline uses, just fed synthetic frames instead of
a websocket.
"""

from __future__ import annotations

import dataclasses
import random
import statistics
import time

from orvix.config import Settings
from orvix.coord_mapper import CoordMapper
from orvix.gesture_interpreter import GestureInterpreter
from orvix.one_euro_filter import OneEuroFilter


@dataclasses.dataclass(frozen=True)
class StepLagResult:
    min_cutoff: float
    beta: float
    lag_ms: float  # time to cross threshold_fraction of the step, or inf if it never did


def measure_step_lag(
    min_cutoff: float,
    beta: float,
    step: float = 500.0,
    fps: float = 100.0,
    threshold_fraction: float = 0.95,
    settle_frames: int = 50,
    max_frames: int = 2000,
) -> StepLagResult:
    """
    hold the filter at rest (input 0) for settle_frames, then jump the input
    to `step` and hold it there; return how long (ms) until the filtered
    output first crosses threshold_fraction * step. mirrors "how long before
    the cursor keeps up with a slow deliberate hand movement" from the
    one_euro_beta comment in config.py.
    """
    dt = 1.0 / fps
    f = OneEuroFilter(min_cutoff=min_cutoff, beta=beta)
    t = 0.0

    for _ in range(settle_frames):
        f(t, 0.0)
        t += dt

    step_start = t
    target = threshold_fraction * step
    for _ in range(max_frames):
        value = f(t, step)
        if value >= target:
            return StepLagResult(min_cutoff, beta, (t - step_start) * 1000.0)
        t += dt

    return StepLagResult(min_cutoff, beta, float("inf"))


@dataclasses.dataclass(frozen=True)
class RestJitterResult:
    min_cutoff: float
    beta: float
    peak_deviation: float  # same units as the input noise (mm, if fed mm)


def measure_rest_jitter(
    min_cutoff: float,
    beta: float,
    noise_std: float = 1.0,
    fps: float = 100.0,
    n_frames: int = 400,
    warmup_frames: int = 20,
    seed: int = 0,
) -> RestJitterResult:
    """
    feed a stationary signal (mean 0) plus gaussian noise -- the kind of
    per-frame wobble real sensor hardware produces even when a hand is
    actually still -- and return the filtered output's peak deviation from
    zero once past the initial warmup. lower min_cutoff should shrink this
    (more smoothing at low speed) at the cost of more lag in
    measure_step_lag; that tradeoff is the whole reason both functions
    exist side by side.

    a fixed seed makes this reproducible run to run, which matters here
    since it's meant to be rerun after tuning changes and compared against
    a previous number, not just eyeballed once.
    """
    rng = random.Random(seed)
    dt = 1.0 / fps
    f = OneEuroFilter(min_cutoff=min_cutoff, beta=beta)
    t = 0.0
    values: list[float] = []

    for _ in range(n_frames):
        raw = rng.gauss(0.0, noise_std)
        values.append(f(t, raw))
        t += dt

    warm = values[warmup_frames:]
    peak = max(abs(v) for v in warm) if warm else 0.0
    return RestJitterResult(min_cutoff, beta, peak)


@dataclasses.dataclass(frozen=True)
class ThroughputReport:
    n_frames: int
    target_fps: float
    budget_ms: float
    mean_ms: float
    p50_ms: float
    p95_ms: float
    p99_ms: float
    within_budget_fraction: float  # fraction of frames that finished inside budget_ms


def _percentile(sorted_values: list[float], fraction: float) -> float:
    idx = min(len(sorted_values) - 1, int(len(sorted_values) * fraction))
    return sorted_values[idx]


def _synthetic_hand(i: int) -> dict:
    """a hand sweeping a small loop, varied enough that the interpreter/mapper aren't just hitting one cached path every frame."""
    x = -100.0 + (i % 200)
    y = 200.0 + (i % 100)
    pinch = 0.9 if (i % 37) == 0 else 0.0  # occasional pinch, exercises that code path too
    return {
        "id": 1,
        "palmPosition": [x, y, 0.0],
        "palmVelocity": [0.0, 0.0, 0.0],
        "palmNormal": [0.0, -1.0, 0.0],
        "pinchStrength": pinch,
        "grabStrength": 0.0,
    }


def benchmark_dispatch_throughput(
    n_frames: int = 2000,
    target_fps: float = 100.0,
) -> ThroughputReport:
    """
    times GestureInterpreter.process_hand + CoordMapper.map_to_screen back
    to back over n_frames of synthetic movement. no leapd, no Quartz (mouse
    output is never posted) -- purely "how much CPU does interpret+map cost
    per frame", which is the part of the real pipeline this module can
    actually control the cost of.
    """
    settings = Settings()
    interp = GestureInterpreter(settings)
    mapper = CoordMapper(settings.calibration, 1920, 1080, settings)

    dt = 1.0 / target_fps
    t = 0.0
    durations_ms: list[float] = []

    for i in range(n_frames):
        hand = _synthetic_hand(i)
        start = time.perf_counter()
        interp.process_hand(hand)
        mapper.map_to_screen(tuple(hand["palmPosition"]), t)
        durations_ms.append((time.perf_counter() - start) * 1000.0)
        t += dt

    durations_ms.sort()
    budget_ms = 1000.0 / target_fps
    within = sum(1 for d in durations_ms if d <= budget_ms) / len(durations_ms)

    return ThroughputReport(
        n_frames=n_frames,
        target_fps=target_fps,
        budget_ms=budget_ms,
        mean_ms=statistics.mean(durations_ms),
        p50_ms=_percentile(durations_ms, 0.50),
        p95_ms=_percentile(durations_ms, 0.95),
        p99_ms=_percentile(durations_ms, 0.99),
        within_budget_fraction=within,
    )
