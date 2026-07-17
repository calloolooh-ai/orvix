"""
calibration.py

works out the Leap-space (millimeter) box that maps onto your screen, by
watching you sweep your hand around your comfortable range for a few
seconds and taking a trimmed min/max of everything it saw.

why a sweep instead of holding still at corners: the old flow asked you to
park your hand at top-left, then bottom-right, and built the box from those
two samples. that has two problems. your reach is a curved envelope rather
than a rectangle you can trace the corners of, so two points systematically
under-measure it, and with only one capture per corner any wobble or
mis-positioning skews the entire box with nothing to average it out. a
sweep collects hundreds of samples across the whole range you actually use,
which is both easier to perform (just move around, no holding still) and a
lot more forgiving.

the trimming matters: raw min/max is very sensitive to the handful of junk
samples leapd emits at the edge of the sensor cone, where tracking gets
unreliable and the palm position can jump tens of mm. throwing away the
extreme few percent on each end cuts those without meaningfully shrinking
the real range.

the actual sampling lives in collect_range() so the terminal flow and the
gui both drive the same code and can't drift apart on what a calibration
means.
"""

from __future__ import annotations

import asyncio
import statistics
import time
from collections.abc import Callable

from orvix.config import CalibrationBox, Settings, load_config, save_config
from orvix.leap_client import LeapConnectionError, pick_hand, stream_frames

# how long to watch you sweep for. long enough to cover the range without
# feeling like a chore, and at ~100fps it's still hundreds of samples even
# if your hand drops out of view for a chunk of it.
SWEEP_SECONDS = 15.0

# how long to watch a still hand to find where "flat" is, for tilt mode
NEUTRAL_TILT_SECONDS = 4.0

# fraction to discard off each end of each axis before taking min/max, see
# the note up top about junk samples at the edge of the sensor cone
TRIM_FRACTION = 0.02

# z isn't used for 2D cursor mapping, we just record a padded range around
# whatever depth you happened to work at so it's there for future gestures
Z_PADDING_MM = 40.0

# below this many samples the trimmed min/max isn't meaningful, almost
# always means the hand wasn't actually over the sensor
MIN_SAMPLES = 100

# a range this small means you didn't really sweep, you just held still.
# mapping a tiny box onto a whole screen would make the cursor unusably
# twitchy, so refuse rather than save something that feels broken.
MIN_SPAN_MM = 40.0


class CalibrationError(RuntimeError):
    """raised when a sweep didn't produce a usable box, with a human explanation."""


def _percentile(sorted_values: list[float], fraction: float) -> float:
    """
    value at `fraction` through an already-sorted list. nearest-rank rather
    than interpolating, we're trimming outliers not doing stats, and the
    index clamp keeps tiny sample counts from running off the end.
    """
    if not sorted_values:
        raise ValueError("no values")
    idx = int(len(sorted_values) * fraction)
    idx = max(0, min(len(sorted_values) - 1, idx))
    return sorted_values[idx]


def build_box(
    samples: list[tuple[float, float, float]],
    trim: float = TRIM_FRACTION,
    z_padding: float = Z_PADDING_MM,
) -> CalibrationBox:
    """
    turn raw palm samples into a calibration box. pure function, no io, so
    the trimming logic is testable without any hardware.

    raises CalibrationError if the sweep was too short or too still to be
    usable, since silently saving a garbage box is worse than saying no.
    """
    if len(samples) < MIN_SAMPLES:
        raise CalibrationError(
            f"only saw {len(samples)} samples of your hand, need at least {MIN_SAMPLES}. "
            "was your hand actually over the sensor the whole time?"
        )

    xs = sorted(s[0] for s in samples)
    ys = sorted(s[1] for s in samples)
    zs = sorted(s[2] for s in samples)

    x_min, x_max = _percentile(xs, trim), _percentile(xs, 1 - trim)
    y_min, y_max = _percentile(ys, trim), _percentile(ys, 1 - trim)
    z_min, z_max = _percentile(zs, trim), _percentile(zs, 1 - trim)

    for axis, lo, hi in (("x", x_min, x_max), ("y", y_min, y_max)):
        if hi - lo < MIN_SPAN_MM:
            raise CalibrationError(
                f"your {axis} range came out at only {hi - lo:.0f}mm, which is too small to "
                f"map onto a screen (need {MIN_SPAN_MM:.0f}mm+). try again and sweep your hand "
                "across your whole comfortable range, not just where it rests."
            )

    return CalibrationBox(
        x_min=x_min,
        x_max=x_max,
        y_min=y_min,
        y_max=y_max,
        z_min=z_min - z_padding,
        z_max=z_max + z_padding,
    )


async def wait_for_hand(preferred_hand: str, timeout: float = 30.0) -> None:
    """
    block until the hand is actually visible, so the sweep timer doesn't
    start counting down while you're still reaching for the sensor.
    """
    start = time.monotonic()
    found = False

    # break out and raise afterwards rather than raising from inside the
    # loop body: stream_frames is an async generator that closes its
    # websocket in a finally, and throwing through it mid-iteration trips
    # "aclose(): asynchronous generator is already running" and buries the
    # real message under a wall of asyncio traceback.
    async for frame in stream_frames():
        if pick_hand(frame, preferred_hand) is not None:
            found = True
            break
        if time.monotonic() - start > timeout:
            break

    if not found:
        raise CalibrationError(
            f"waited {timeout:.0f}s and never saw a {preferred_hand} hand. is the leap "
            "plugged in, and is that the hand you meant? (preferred_hand in your config)"
        )


async def collect_range(
    preferred_hand: str,
    duration: float = SWEEP_SECONDS,
    on_progress: Callable[[float, int], None] | None = None,
) -> list[tuple[float, float, float]]:
    """
    watch the hand for `duration` seconds and return every palm position we
    saw. frames where the hand isn't visible are skipped rather than
    counted, so a brief dropout costs you samples but doesn't corrupt them.

    on_progress(elapsed_fraction, n_samples) is called as it goes, so a
    caller can draw a progress bar or update a menu without this module
    needing to know anything about how it's being displayed.
    """
    samples: list[tuple[float, float, float]] = []
    start = time.monotonic()

    async for frame in stream_frames():
        elapsed = time.monotonic() - start
        if elapsed >= duration:
            break

        hand = pick_hand(frame, preferred_hand)
        if hand is not None:
            samples.append(tuple(hand["palmPosition"]))

        if on_progress is not None:
            on_progress(min(1.0, elapsed / duration), len(samples))

    return samples


async def collect_neutral_tilt(
    preferred_hand: str, duration: float = NEUTRAL_TILT_SECONDS
) -> tuple[float, float]:
    """
    watch a held-still hand and average its palm normal, giving us where
    "flat" actually is for you. tilt mode subtracts this, the same way a
    joystick gets centred.

    needed because nobody's hand rests at a true zero: a real right hand
    measured x=-0.165 holding comfortably flat, which is enough to make an
    uncentred tilt mode creep sideways on its own.
    """
    xs: list[float] = []
    zs: list[float] = []
    start = time.monotonic()

    async for frame in stream_frames():
        if time.monotonic() - start >= duration:
            break
        hand = pick_hand(frame, preferred_hand)
        if hand is None:
            continue
        normal = hand.get("palmNormal")
        if not normal:
            continue
        xs.append(normal[0])
        zs.append(normal[2])

    if len(xs) < 20:
        raise CalibrationError(
            "couldn't get a steady read on your hand's angle. hold it still over the sensor."
        )

    return statistics.mean(xs), statistics.mean(zs)


async def calibrate(
    settings: Settings,
    duration: float = SWEEP_SECONDS,
    on_progress: Callable[[float, int], None] | None = None,
) -> CalibrationBox:
    """
    the whole flow minus any ui: wait for the hand, sweep, build the box.
    doesn't save, the caller decides that after showing the user.
    """
    await wait_for_hand(settings.preferred_hand)
    samples = await collect_range(settings.preferred_hand, duration, on_progress)
    return build_box(samples)


def describe_box(box: CalibrationBox) -> str:
    """one-liner summary of a box, used by both the cli and the gui's alert."""
    return (
        f"x {box.x_min:.0f} to {box.x_max:.0f}mm  "
        f"y {box.y_min:.0f} to {box.y_max:.0f}mm  "
        f"(span {box.x_max - box.x_min:.0f} x {box.y_max - box.y_min:.0f}mm)"
    )


def _draw_progress(fraction: float, n_samples: int) -> None:
    filled = int(fraction * 30)
    bar = "#" * filled + "." * (30 - filled)
    print(f"\r  [{bar}] {fraction * 100:3.0f}%  {n_samples} samples", end="", flush=True)


async def _run_async() -> None:
    settings = load_config()

    print("orvix calibration")
    print("=================")
    print()
    print("this watches you move your hand around so orvix knows your actual reach")
    print("instead of guessing. you don't need to hold still or hit exact spots.")
    print()
    print(f"when it starts, spend {SWEEP_SECONDS:.0f}s sweeping your hand around the whole area")
    print("you'd want to use: left to right, high to low, like you're wiping a window.")
    print("keep it over the sensor, palm down.")
    print()
    input("press enter when you're ready...")
    print()
    print("put your hand over the sensor...")

    await wait_for_hand(settings.preferred_hand)
    print("got it, start sweeping!")
    print()
    samples = await collect_range(settings.preferred_hand, SWEEP_SECONDS, _draw_progress)
    print()
    print()
    box = build_box(samples)

    old = settings.calibration
    print("before:", describe_box(old))
    print("after: ", describe_box(box))
    print()

    settings.calibration = box

    # tilt mode needs to know where your "flat" is, which is never actually
    # zero. cheap to measure while we've got you here, and useless to guess.
    print("one more thing, for tilt mode.")
    input("hold your hand flat and comfortable over the sensor, then press enter...")
    print(f"hold still for {NEUTRAL_TILT_SECONDS:.0f}s...")
    try:
        cx, cz = await collect_neutral_tilt(settings.preferred_hand)
    except CalibrationError as exc:
        print(f"couldn't measure your neutral tilt ({exc})")
        print("skipping it, tilt mode may drift. the rest is saved.")
    else:
        settings.tilt_center_x = cx
        settings.tilt_center_z = cz
        print(f"neutral tilt: x={cx:+.3f} z={cz:+.3f}")
        if abs(cx) > settings.tilt_deadzone or abs(cz) > settings.tilt_deadzone:
            print("(your hand rests well off flat, so centring it here is what stops")
            print(" tilt mode creeping sideways on its own)")

    save_config(settings)

    print()
    print("saved. try `orvix cli --dry-run` to check it feels right before going live.")


def run() -> None:
    """
    sync entry point, called from main.py's --calibrate flag.

    the error handling lives out here rather than inside the coroutine so
    SystemExit isn't raised while asyncio is still finalizing the leap
    stream's async generator, which turns a one line "leapd isn't running"
    into an unreadable traceback.
    """
    try:
        asyncio.run(_run_async())
    except LeapConnectionError as exc:
        print(f"\ncouldn't connect to leapd: {exc}")
        print("make sure leapd is running before calibrating, see docs/SETUP.md")
        raise SystemExit(1) from exc
    except CalibrationError as exc:
        print(f"\ncalibration failed: {exc}")
        raise SystemExit(1) from exc
    except KeyboardInterrupt:
        print("\n\ncancelled, your old calibration is untouched.")
        raise SystemExit(130) from None
