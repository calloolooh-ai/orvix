"""
calibration.py

interactive cli flow: asks you to hold your hand at a few reference points
(comfortable high/low, left/right, near/far) and records what Leap actually
sees, then writes that into your config as the calibration box that
coord_mapper.py maps onto your screen.

the factory defaults in config.py are just a guess at "comfortable desk
hand range", running this replaces them with your actual measured range,
which matters a lot since everyone sits/reaches differently.
"""

from __future__ import annotations

import asyncio
import statistics

from orvix.config import CalibrationBox, load_config, save_config
from orvix.leap_client import LeapConnectionError, pick_hand, stream_frames

# how many frames to average at each reference point, cuts down on noise
# from a single jittery sample
SAMPLES_PER_POINT = 30


async def _collect_samples(preferred_hand: str, count: int) -> list[tuple[float, float, float]]:
    """
    read frames until we've got `count` samples of the tracked hand's palm
    position. skips frames where the hand isn't visible rather than
    counting them, so a brief dropout doesn't throw off the average.
    """
    samples: list[tuple[float, float, float]] = []
    async for frame in stream_frames():
        hand = pick_hand(frame, preferred_hand)
        if hand is None:
            continue
        samples.append(tuple(hand["palmPosition"]))
        if len(samples) >= count:
            break
    return samples


def _average_point(samples: list[tuple[float, float, float]]) -> tuple[float, float, float]:
    xs, ys, zs = zip(*samples)
    return statistics.mean(xs), statistics.mean(ys), statistics.mean(zs)


async def _prompt_and_capture(prompt: str, preferred_hand: str) -> tuple[float, float, float]:
    input(f"\n{prompt}\npress enter when your hand is in position and holding still...")
    print("capturing, hold steady...")
    samples = await _collect_samples(preferred_hand, SAMPLES_PER_POINT)
    point = _average_point(samples)
    print(f"got it: x={point[0]:.1f} y={point[1]:.1f} z={point[2]:.1f}")
    return point


async def _run_async() -> None:
    settings = load_config()

    print("orvix calibration")
    print("==================")
    print("this walks you through a few reference hand positions so orvix knows")
    print("your actual comfortable reach, instead of a generic guess.")
    print("keep your hand flat, palm down, over the sensor for each step.\n")

    try:
        top_left = await _prompt_and_capture(
            "move your hand to the TOP-LEFT of your comfortable range", settings.preferred_hand
        )
        bottom_right = await _prompt_and_capture(
            "now move your hand to the BOTTOM-RIGHT of your comfortable range",
            settings.preferred_hand,
        )
    except LeapConnectionError as exc:
        print(f"\ncouldn't connect to leapd: {exc}")
        print("make sure leapd is running before calibrating, see docs/SETUP.md")
        raise SystemExit(1) from exc

    x_min = min(top_left[0], bottom_right[0])
    x_max = max(top_left[0], bottom_right[0])
    y_min = min(top_left[1], bottom_right[1])
    y_max = max(top_left[1], bottom_right[1])
    z_min = min(top_left[2], bottom_right[2]) - 40
    z_max = max(top_left[2], bottom_right[2]) + 40

    settings.calibration = CalibrationBox(
        x_min=x_min, x_max=x_max, y_min=y_min, y_max=y_max, z_min=z_min, z_max=z_max
    )
    save_config(settings)

    print("\ncalibration saved. run `python -m orvix.main --dry-run` to check it feels right")
    print("before running live.")


def run() -> None:
    """sync entry point, called from main.py's --calibrate flag."""
    asyncio.run(_run_async())
