#!/usr/bin/env python3
"""
profile_pipeline.py

runs orvix/perf.py's measurements and prints a report. rerun this after
touching one_euro_min_cutoff/one_euro_beta or the dispatch path, instead of
re-measuring by hand and updating a comment (see config.py's one_euro_beta
docstring for the numbers this originally reproduced).

usage: python scripts/profile_pipeline.py
"""

from __future__ import annotations

from orvix.perf import (
    benchmark_dispatch_throughput,
    measure_rest_jitter,
    measure_step_lag,
)


def _fmt_lag(ms: float) -> str:
    return "never settled" if ms == float("inf") else f"{ms:.0f}ms"


def main() -> None:
    print("orvix pipeline profile")
    print("=======================")
    print()

    print("-- One Euro Filter: lag vs jitter tradeoff --")
    print("(min_cutoff=1.0 throughout, varying beta; step=500mm, noise_std=1.0mm)")
    print()
    print(f"{'beta':>8}  {'step lag':>10}  {'rest jitter':>12}")
    for beta in (0.0, 0.007, 0.05, 0.15):
        lag = measure_step_lag(min_cutoff=1.0, beta=beta)
        jitter = measure_rest_jitter(min_cutoff=1.0, beta=beta)
        marker = "  <- current default" if beta == 0.05 else ""
        print(f"{beta:>8}  {_fmt_lag(lag.lag_ms):>10}  {jitter.peak_deviation:>10.1f}mm{marker}")

    print()
    print("-- dispatch throughput: GestureInterpreter + CoordMapper, per frame --")
    report = benchmark_dispatch_throughput()
    print(f"target_fps={report.target_fps:.0f}  budget={report.budget_ms:.2f}ms/frame  n={report.n_frames}")
    print(f"mean={report.mean_ms:.4f}ms  p50={report.p50_ms:.4f}ms  p95={report.p95_ms:.4f}ms  p99={report.p99_ms:.4f}ms")
    print(f"within budget: {report.within_budget_fraction * 100:.1f}% of frames")


if __name__ == "__main__":
    main()
