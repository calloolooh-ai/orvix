"""
tests for calibration.py's box building. the sampling itself needs a live
leap stream so it's out of scope here, but the trimming/validation logic is
pure and is the part that decides whether your cursor covers the screen, so
it's worth pinning down.
"""

import pytest

from orvix import calibration
from orvix.calibration import (
    MIN_SAMPLES,
    CalibrationError,
    _percentile,
    build_box,
    collect_range,
    describe_box,
)


def sweep(x_range, y_range, n=400):
    """fake a sweep: n samples spread evenly across the given ranges."""
    x_lo, x_hi = x_range
    y_lo, y_hi = y_range
    out = []
    for i in range(n):
        t = i / (n - 1)
        out.append((x_lo + t * (x_hi - x_lo), y_lo + t * (y_hi - y_lo), 0.0))
    return out


def test_percentile_picks_nearest_rank():
    values = [0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0]
    assert _percentile(values, 0.0) == 0.0
    assert _percentile(values, 0.5) == 5.0
    # clamps rather than running off the end
    assert _percentile(values, 1.0) == 9.0


def test_build_box_covers_the_swept_range():
    box = build_box(sweep((-100.0, 100.0), (150.0, 400.0)), trim=0.0)
    assert box.x_min == pytest.approx(-100.0)
    assert box.x_max == pytest.approx(100.0)
    assert box.y_min == pytest.approx(150.0)
    assert box.y_max == pytest.approx(400.0)


def test_trimming_discards_edge_outliers():
    # a clean sweep, plus a couple of junk samples like leapd emits at the
    # edge of the sensor cone. untrimmed these would blow the box wide open.
    samples = sweep((-100.0, 100.0), (150.0, 400.0), n=400)
    samples.append((-9000.0, 150.0, 0.0))
    samples.append((9000.0, 400.0, 0.0))

    untrimmed = build_box(samples, trim=0.0)
    assert untrimmed.x_min < -1000  # the junk made it in

    trimmed = build_box(samples, trim=0.02)
    assert trimmed.x_min > -150  # junk gone, real range kept
    assert trimmed.x_max < 150


def test_z_gets_padded_since_we_dont_really_map_it():
    box = build_box(sweep((-100.0, 100.0), (150.0, 400.0)), trim=0.0, z_padding=40.0)
    # every fake sample has z=0, so the padding is the whole range
    assert box.z_min == pytest.approx(-40.0)
    assert box.z_max == pytest.approx(40.0)


def test_too_few_samples_is_refused():
    with pytest.raises(CalibrationError, match="samples"):
        build_box(sweep((-100.0, 100.0), (150.0, 400.0), n=MIN_SAMPLES - 1))


def test_holding_still_is_refused_rather_than_saved():
    # barely moved, mapping this onto a screen would be unusably twitchy
    with pytest.raises(CalibrationError, match="too small"):
        build_box(sweep((0.0, 5.0), (200.0, 210.0)))


def test_a_stalled_axis_is_caught_even_if_the_other_is_fine():
    # swept side to side but never up and down
    with pytest.raises(CalibrationError, match="y range"):
        build_box(sweep((-100.0, 100.0), (200.0, 205.0)))


def test_describe_box_reports_the_span():
    box = build_box(sweep((-100.0, 100.0), (150.0, 400.0)), trim=0.0)
    text = describe_box(box)
    assert "200 x 250mm" in text


# -- collect_range's on_sample hook, used by calibration_viz.py for live
# visual feedback during the sweep --


def _fake_stream(hand_positions):
    """stand in for stream_frames: one frame per position, plus a couple of
    no-hand frames mixed in to prove those get skipped rather than sampled."""

    async def _gen(url=None):
        for pos in hand_positions:
            if pos is None:
                yield {"hands": []}
            else:
                yield {"hands": [{"type": "right", "palmPosition": list(pos)}]}

    return _gen


@pytest.mark.asyncio
async def test_on_sample_fires_once_per_captured_sample(monkeypatch):
    positions = [(0.0, 100.0, 0.0), None, (10.0, 110.0, 0.0), (20.0, 120.0, 0.0)]
    # calibration.py does `from orvix.leap_client import stream_frames`, so it
    # holds its own bound reference; patching leap_client.stream_frames alone
    # wouldn't touch it (and collect_range would silently try a real
    # websocket connection to a leapd that isn't running in CI).
    monkeypatch.setattr(calibration, "stream_frames", _fake_stream(positions))

    seen = []
    await collect_range("right", duration=999.0, on_sample=lambda x, y: seen.append((x, y)))

    # the None frame must not produce a sample: 3 hand frames in, 3 calls out
    assert seen == [(0.0, 100.0), (10.0, 110.0), (20.0, 120.0)]


@pytest.mark.asyncio
async def test_on_sample_is_optional(monkeypatch):
    positions = [(0.0, 100.0, 0.0), (10.0, 110.0, 0.0)]
    # calibration.py does `from orvix.leap_client import stream_frames`, so it
    # holds its own bound reference; patching leap_client.stream_frames alone
    # wouldn't touch it (and collect_range would silently try a real
    # websocket connection to a leapd that isn't running in CI).
    monkeypatch.setattr(calibration, "stream_frames", _fake_stream(positions))

    # must not raise just because no callback was passed
    samples = await collect_range("right", duration=999.0)
    assert len(samples) == 2


@pytest.mark.asyncio
async def test_calibrate_threads_on_sample_through_to_collect_range(monkeypatch):
    positions = [(float(i), 100.0 + i, 0.0) for i in range(150)]
    # calibration.py does `from orvix.leap_client import stream_frames`, so it
    # holds its own bound reference; patching leap_client.stream_frames alone
    # wouldn't touch it (and collect_range would silently try a real
    # websocket connection to a leapd that isn't running in CI).
    monkeypatch.setattr(calibration, "stream_frames", _fake_stream(positions))

    from orvix.config import Settings

    seen = []
    box = await calibration.calibrate(
        Settings(), duration=999.0, on_sample=lambda x, y: seen.append((x, y))
    )
    assert len(seen) == 150
    assert box.x_max > box.x_min
