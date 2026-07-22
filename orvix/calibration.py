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

from orvix.calibration_viz import BoundsTracker
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

# how long to wait for the next frame once a sweep/tilt-read is already
# underway before deciding the leap disappeared partway through (unplugged,
# bad cable) rather than just being between hand-visible frames. same
# reasoning as wait_for_hand's per-message wait_for: a device that goes
# silent mid-collection would otherwise hang the plain `async for` forever,
# since a missing physical device means leapd never emits anything at all,
# not even a no-hands frame.
STALL_TIMEOUT_SECONDS = 5.0

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

    # bound each individual "get me the next frame" step with wait_for
    # rather than only checking the clock after a frame arrives: if leapd
    # is up but no physical device is plugged in at all, it never sends a
    # single frame message, so a per-iteration clock check inside the loop
    # body never runs and this would otherwise hang forever instead of
    # timing out. closing the generator ourselves in the finally (once it's
    # not suspended mid-await) avoids the "aclose(): asynchronous generator
    # is already running" trap that throwing a cancellation into it would hit.
    stream = stream_frames()
    try:
        while True:
            remaining = timeout - (time.monotonic() - start)
            if remaining <= 0:
                break
            try:
                frame = await asyncio.wait_for(stream.__anext__(), timeout=remaining)
            except (TimeoutError, StopAsyncIteration):
                break
            if pick_hand(frame, preferred_hand) is not None:
                found = True
                break
    finally:
        await stream.aclose()

    if not found:
        raise CalibrationError(
            f"waited {timeout:.0f}s and never saw a {preferred_hand} hand. is the leap "
            "plugged in, and is that the hand you meant? (preferred_hand in your config)"
        )


async def collect_range(
    preferred_hand: str,
    duration: float = SWEEP_SECONDS,
    on_progress: Callable[[float, int], None] | None = None,
    on_sample: Callable[[float, float], None] | None = None,
    stall_timeout: float = STALL_TIMEOUT_SECONDS,
) -> list[tuple[float, float, float]]:
    """
    watch the hand for `duration` seconds and return every palm position we
    saw. frames where the hand isn't visible are skipped rather than
    counted, so a brief dropout costs you samples but doesn't corrupt them.

    on_progress(elapsed_fraction, n_samples) is called as it goes, so a
    caller can draw a progress bar or update a menu without this module
    needing to know anything about how it's being displayed.

    on_sample(x_mm, y_mm) is called once per captured sample (i.e. only when
    the hand was actually visible that frame), letting a caller track a live
    running envelope of where you've swept -- see calibration_viz.py -- again
    without this module needing to know anything about how that's rendered.
    kept separate from on_progress since not every caller wants per-sample
    detail and n_samples alone doesn't say *where* the range is.

    raises CalibrationError if no frame arrives for `stall_timeout` seconds
    partway through: by this point wait_for_hand already proved a device was
    there, so a stall here means it went away mid-sweep rather than "no hand
    yet". if the stream just ends cleanly (leapd closed the connection),
    that's not treated as an error, we return whatever was collected so far.
    """
    samples: list[tuple[float, float, float]] = []
    start = time.monotonic()

    stream = stream_frames()
    try:
        while True:
            elapsed = time.monotonic() - start
            if elapsed >= duration:
                break

            try:
                frame = await asyncio.wait_for(stream.__anext__(), timeout=stall_timeout)
            except TimeoutError:
                raise CalibrationError(
                    f"the leap stopped sending data partway through the sweep "
                    f"(no frame for {stall_timeout:.0f}s). is it still plugged in?"
                ) from None
            except StopAsyncIteration:
                break

            hand = pick_hand(frame, preferred_hand)
            if hand is not None:
                pos = tuple(hand["palmPosition"])
                samples.append(pos)
                if on_sample is not None:
                    on_sample(pos[0], pos[1])

            elapsed = time.monotonic() - start
            if on_progress is not None:
                on_progress(min(1.0, elapsed / duration), len(samples))
    finally:
        await stream.aclose()

    return samples


async def collect_neutral_tilt(
    preferred_hand: str,
    duration: float = NEUTRAL_TILT_SECONDS,
    stall_timeout: float = STALL_TIMEOUT_SECONDS,
) -> tuple[float, float]:
    """
    watch a held-still hand and average its palm normal, giving us where
    "flat" actually is for you. tilt mode subtracts this, the same way a
    joystick gets centred.

    needed because nobody's hand rests at a true zero: a real right hand
    measured x=-0.165 holding comfortably flat, which is enough to make an
    uncentred tilt mode creep sideways on its own.

    same stall guard as collect_range: a device that disappears mid-read
    raises CalibrationError instead of hanging, a clean end of stream just
    means we work with whatever readings we already have.
    """
    xs: list[float] = []
    zs: list[float] = []
    start = time.monotonic()

    stream = stream_frames()
    try:
        while True:
            if time.monotonic() - start >= duration:
                break
            try:
                frame = await asyncio.wait_for(stream.__anext__(), timeout=stall_timeout)
            except TimeoutError:
                raise CalibrationError(
                    f"the leap stopped sending data partway through the read "
                    f"(no frame for {stall_timeout:.0f}s). is it still plugged in?"
                ) from None
            except StopAsyncIteration:
                break

            hand = pick_hand(frame, preferred_hand)
            if hand is None:
                continue
            normal = hand.get("palmNormal")
            if not normal:
                continue
            xs.append(normal[0])
            zs.append(normal[2])
    finally:
        await stream.aclose()

    if len(xs) < 20:
        raise CalibrationError(
            "couldn't get a steady read on your hand's angle. hold it still over the sensor."
        )

    return statistics.mean(xs), statistics.mean(zs)


async def calibrate(
    settings: Settings,
    duration: float = SWEEP_SECONDS,
    on_progress: Callable[[float, int], None] | None = None,
    on_sample: Callable[[float, float], None] | None = None,
) -> CalibrationBox:
    """
    the whole flow minus any ui: wait for the hand, sweep, build the box.
    doesn't save, the caller decides that after showing the user.
    """
    await wait_for_hand(settings.preferred_hand)
    samples = await collect_range(settings.preferred_hand, duration, on_progress, on_sample)
    return build_box(samples)


def describe_box(box: CalibrationBox) -> str:
    """one-liner summary of a box, used by both the cli and the gui's alert."""
    return (
        f"x {box.x_min:.0f} to {box.x_max:.0f}mm  "
        f"y {box.y_min:.0f} to {box.y_max:.0f}mm  "
        f"(span {box.x_max - box.x_min:.0f} x {box.y_max - box.y_min:.0f}mm)"
    )


class _TerminalCalibrationView:
    """
    redraws a percent bar plus a live ascii envelope grid in place, so the
    terminal flow shows the same "is my sweep actually covering my range"
    feedback the GUI's on-screen overlay does, not just a percent-complete
    bar that says nothing about coverage.

    throttled independently of the ~100/s sample rate: a real terminal
    can't usefully redraw that often and it'd just be wasted I/O, so this
    only actually reprints every min_interval seconds. finish() forces one
    last draw so the final frame reflects where the sweep actually ended,
    not whatever was on screen when the throttle last skipped a redraw.
    """

    def __init__(self, width: int = 44, height: int = 12, min_interval: float = 0.08):
        self._tracker = BoundsTracker()
        self._fraction = 0.0
        self._n_samples = 0
        self._width = width
        self._height = height
        self._min_interval = min_interval
        self._last_draw = 0.0
        self._n_lines_drawn = 0

    def on_progress(self, fraction: float, n_samples: int) -> None:
        self._fraction = fraction
        self._n_samples = n_samples
        self._maybe_redraw()

    def on_sample(self, x: float, y: float) -> None:
        self._tracker.update(x, y)
        self._maybe_redraw()

    def finish(self) -> None:
        self._maybe_redraw(force=True)
        print()

    def _maybe_redraw(self, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_draw < self._min_interval:
            return
        self._last_draw = now
        self._redraw()

    def _redraw(self) -> None:
        filled = int(self._fraction * 30)
        bar = "#" * filled + "." * (30 - filled)
        percent_line = f"[{bar}] {self._fraction * 100:3.0f}%  {self._n_samples} samples"
        text = percent_line + "\n" + self._tracker.render_ascii(self._width, self._height)
        lines = text.split("\n")

        # move the cursor back up over whatever we drew last time, then
        # reprint every line with an erase-to-end-of-line first so a
        # shorter new line can't leave stray characters from the old one
        if self._n_lines_drawn:
            print(f"\033[{self._n_lines_drawn}A", end="")
        for line in lines:
            print(f"\033[2K{line}")
        self._n_lines_drawn = len(lines)


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
    view = _TerminalCalibrationView()
    samples = await collect_range(
        settings.preferred_hand, SWEEP_SECONDS,
        on_progress=view.on_progress, on_sample=view.on_sample,
    )
    view.finish()
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
