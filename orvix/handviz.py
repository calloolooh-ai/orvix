"""
handviz.py

a full-screen, click-through "energy field" visualizer that floats over your
desktop and paints ripples off your hand as you move it above the sensor.

it is deliberately *separate* from the mouse-control pipeline: it opens its
own leapd connection and never touches the cursor, so you can run it purely
as eye candy without orvix fighting your trackpad. launch it with

    python -m orvix.handviz

the shape of it mirrors overlay.py: a borderless, translucent, click-through
NSWindow at status level that AppKit draws into. the difference is this one
covers the whole screen and animates continuously off a background leapd
stream instead of only appearing for the radial menu.

threading: leapd is read on a background thread running its own asyncio loop,
which drops the newest hand snapshot into a lock-guarded HandState. the main
thread runs an NSTimer at ~60fps that reads that snapshot, spawns new ripples,
ages out old ones, and redraws. all AppKit work stays on the main thread.
"""

from __future__ import annotations

import asyncio
import logging
import math
import threading
import time

from orvix.leap_client import (
    LeapConnectionError,
    fingertips_for_hand,
    stream_latest_frames,
)

logger = logging.getLogger("orvix.handviz")

try:
    import AppKit
    import objc
    from objc import python_method

    _APPKIT_OK = True
except Exception:  # noqa: BLE001 - PyObjC/AppKit missing: nothing to draw into
    _APPKIT_OK = False


# the slice of Leap space (mm) we treat as "over the desktop". x is left/right
# from the sensor's centre, y is height above it. matches the config default
# calibration box so the ripples land roughly where your hand is pointing,
# without needing calibration to have been run.
_BOX_X_MIN, _BOX_X_MAX = -180.0, 180.0
_BOX_Y_MIN, _BOX_Y_MAX = 120.0, 420.0

# leap finger type ids
_THUMB, _INDEX, _MIDDLE, _RING, _PINKY = 0, 1, 2, 3, 4


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


class HandState:
    """
    thread-safe latest-hand snapshot handed from the leapd reader thread to the
    main-thread render timer. holds everything in Leap mm; mapping to screen
    coords happens on the render side against the live view size.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.present = False
        self.palm = (0.0, 0.0, 0.0)  # x, y, z mm
        self.palm_speed = 0.0  # mm/s magnitude, from leapd palmVelocity
        self.tips: dict[int, tuple[float, float, float]] = {}
        self.pinch = 0.0
        self.grab = 0.0
        # a monotonically increasing counter the render side can watch to know
        # whether a fresh frame landed since it last looked (so pinch edges
        # aren't missed or double-counted).
        self.seq = 0
        self.error: str | None = None

    def update(
        self,
        present: bool,
        palm: tuple[float, float, float],
        palm_speed: float,
        tips: dict[int, tuple[float, float, float]],
        pinch: float,
        grab: float,
    ) -> None:
        with self._lock:
            self.present = present
            self.palm = palm
            self.palm_speed = palm_speed
            self.tips = tips
            self.pinch = pinch
            self.grab = grab
            self.seq += 1

    def set_error(self, msg: str) -> None:
        with self._lock:
            self.error = msg

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "present": self.present,
                "palm": self.palm,
                "palm_speed": self.palm_speed,
                "tips": dict(self.tips),
                "pinch": self.pinch,
                "grab": self.grab,
                "seq": self.seq,
                "error": self.error,
            }


def _run_reader(state: HandState, stop: threading.Event) -> None:
    """
    background thread: own asyncio loop, stream newest frames from leapd, and
    push each into the shared HandState. only ever touches HandState, never
    AppKit, so it's safe off the main thread.
    """
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    async def pump() -> None:
        try:
            async for frame in stream_latest_frames():
                if stop.is_set():
                    return
                hands = frame.get("hands") or []
                if not hands:
                    state.update(False, (0.0, 0.0, 0.0), 0.0, {}, 0.0, 0.0)
                    continue
                hand = hands[0]
                palm = tuple(hand.get("palmPosition", (0.0, 0.0, 0.0)))
                vel = hand.get("palmVelocity", (0.0, 0.0, 0.0))
                speed = math.sqrt(sum(c * c for c in vel))
                tips = fingertips_for_hand(frame, hand)
                pinch = float(hand.get("pinchStrength", 0.0) or 0.0)
                grab = float(hand.get("grabStrength", 0.0) or 0.0)
                state.update(True, palm, speed, tips, pinch, grab)
        except LeapConnectionError as exc:
            state.set_error(str(exc))
        except Exception as exc:  # noqa: BLE001 - surface, don't crash the app
            state.set_error(f"leapd stream stopped: {exc}")
        else:
            # the for loop only falls through to here if stream_latest_frames
            # ended on its own with no exception, i.e. leapd closed the
            # websocket cleanly mid-session -- a user-requested stop always
            # hits the `return` above instead, which skips this else clause.
            # without this the view would just freeze on the last frame with
            # no indication anything went wrong, same bug main.py/gui.py had.
            state.set_error("lost connection to leapd mid-session")

    try:
        loop.run_until_complete(pump())
    finally:
        # a real user-requested stop hits the `return` above while
        # stream_latest_frames' background reader task and its leapd
        # websocket are still alive underneath it -- closing the loop
        # without letting them unwind first leaks the connection, same
        # bug gui.py's own pipeline teardown already had to fix.
        try:
            pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            loop.run_until_complete(loop.shutdown_asyncgens())
        except Exception:  # noqa: BLE001 - a messy teardown must not take the app down
            logger.debug("hand visualizer reader teardown was not clean", exc_info=True)
        finally:
            loop.close()


class Ripple:
    """one expanding, fading ring. all in the view's Cocoa coord space (px)."""

    __slots__ = ("cx", "cy", "t0", "life", "r0", "r1", "w0", "rgb", "peak")

    def __init__(self, cx, cy, life, r0, r1, w0, rgb, peak=0.7):
        self.cx = cx
        self.cy = cy
        self.t0 = time.monotonic()
        self.life = life
        self.r0 = r0
        self.r1 = r1
        self.w0 = w0
        self.rgb = rgb
        self.peak = peak

    def alive(self, now: float) -> bool:
        return (now - self.t0) < self.life


if _APPKIT_OK:

    def _ease_out(t: float) -> float:
        return 1.0 - (1.0 - t) * (1.0 - t)

    class _FieldView(AppKit.NSView):
        """draws the palm glow, finger filaments, and the live ripple list."""

        def initWithFrame_(self, frame):
            self = objc.super(_FieldView, self).initWithFrame_(frame)
            if self is None:
                return None
            self._ripples = []
            self._palm = None  # (x, y) cocoa px or None
            self._tips = {}  # finger type -> (x, y) cocoa px
            self._openness = 1.0  # 0 fist .. 1 open, drives palm glow size
            return self

        @python_method
        def set_scene(self, ripples, palm, tips, openness):
            self._ripples = ripples
            self._palm = palm
            self._tips = tips
            self._openness = openness

        def isFlipped(self):
            return False  # Cocoa default: y grows up

        def drawRect_(self, _rect):
            try:
                self._draw()
            except Exception:  # noqa: BLE001 - never let a draw error escape into Cocoa
                logger.debug("handviz draw failed", exc_info=True)

        @python_method
        def _draw(self):
            now = time.monotonic()

            # faint filaments from palm to each fingertip: the "field" lattice
            if self._palm is not None and self._tips:
                px, py = self._palm
                for (tx, ty) in self._tips.values():
                    line = AppKit.NSBezierPath.bezierPath()
                    line.moveToPoint_((px, py))
                    line.lineToPoint_((tx, ty))
                    line.setLineWidth_(1.4)
                    AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(
                        0.45, 0.85, 1.0, 0.18
                    ).set()
                    line.stroke()

            # soft palm bloom: a few stacked translucent discs fake a glow
            if self._palm is not None:
                px, py = self._palm
                base = 26.0 + 34.0 * self._openness
                for k in range(5):
                    r = base * (1.0 + k * 0.55)
                    a = 0.16 * (1.0 - k / 5.0)
                    disc = AppKit.NSBezierPath.bezierPathWithOvalInRect_(
                        ((px - r, py - r), (2 * r, 2 * r))
                    )
                    AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(
                        0.35, 0.80, 1.0, a
                    ).set()
                    disc.fill()

            # bright fingertip nodes
            for (tx, ty) in self._tips.values():
                r = 6.0
                dot = AppKit.NSBezierPath.bezierPathWithOvalInRect_(
                    ((tx - r, ty - r), (2 * r, 2 * r))
                )
                AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(
                    0.75, 0.95, 1.0, 0.85
                ).set()
                dot.fill()

            # the ripples themselves, oldest first so newer ones sit on top
            for rp in self._ripples:
                age = now - rp.t0
                if age < 0:
                    continue
                frac = _clamp(age / rp.life, 0.0, 1.0)
                radius = rp.r0 + (rp.r1 - rp.r0) * _ease_out(frac)
                alpha = rp.peak * (1.0 - frac)
                if alpha <= 0.01:
                    continue
                width = max(0.6, rp.w0 * (1.0 - 0.7 * frac))
                ring = AppKit.NSBezierPath.bezierPath()
                ring.appendBezierPathWithArcWithCenter_radius_startAngle_endAngle_(
                    (rp.cx, rp.cy), radius, 0.0, 360.0
                )
                ring.setLineWidth_(width)
                r, g, b = rp.rgb
                AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(
                    r, g, b, alpha
                ).set()
                ring.stroke()


class FieldController:
    """
    owns the full-screen window + view, the ripple list, and the emission
    logic that turns hand snapshots into new ripples each tick. main-thread
    only. safe no-op without AppKit so imports don't blow up headless.
    """

    def __init__(self, state: HandState) -> None:
        self._state = state
        self._window = None
        self._view = None
        self._ripples: list = []
        self._last_palm_emit = 0.0
        self._last_tip_emit = 0.0
        self._pinched = False  # edge tracking for shockwaves
        self._last_seq = -1

    @property
    def available(self) -> bool:
        return _APPKIT_OK

    def _ensure_window(self) -> None:
        if self._window is not None:
            return
        screen = AppKit.NSScreen.mainScreen()
        frame = screen.frame()
        window = AppKit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            frame, AppKit.NSWindowStyleMaskBorderless, AppKit.NSBackingStoreBuffered, False
        )
        window.setOpaque_(False)
        window.setBackgroundColor_(AppKit.NSColor.clearColor())
        window.setLevel_(AppKit.NSStatusWindowLevel)
        window.setIgnoresMouseEvents_(True)  # click-through
        window.setCollectionBehavior_(
            AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces
            | AppKit.NSWindowCollectionBehaviorStationary
        )
        view = _FieldView.alloc().initWithFrame_(((0, 0), frame.size))
        window.setContentView_(view)
        window.orderFrontRegardless()
        self._window = window
        self._view = view

    @python_method
    def _map_to_view(self, x_mm: float, y_mm: float):
        """Leap (x,y) mm -> Cocoa px in the full-screen view (bottom-left origin)."""
        size = self._view.frame().size
        tx = (x_mm - _BOX_X_MIN) / (_BOX_X_MAX - _BOX_X_MIN)
        ty = (y_mm - _BOX_Y_MIN) / (_BOX_Y_MAX - _BOX_Y_MIN)
        # higher hand (bigger leap y) -> higher on screen -> bigger cocoa y,
        # so no inversion here, unlike the cursor mapper's Quartz coords.
        return _clamp(tx, 0.0, 1.0) * size.width, _clamp(ty, 0.0, 1.0) * size.height

    def tick(self) -> None:
        """one animation frame: pull hand state, spawn ripples, age, redraw."""
        if not _APPKIT_OK:
            return
        try:
            self._ensure_window()
            self._tick_inner()
        except Exception:  # noqa: BLE001 - visual only, keep the timer alive
            logger.debug("handviz tick failed", exc_info=True)

    @python_method
    def _tick_inner(self) -> None:
        now = time.monotonic()
        snap = self._state.snapshot()

        palm_view = None
        tips_view = {}
        openness = 1.0

        if snap["present"]:
            px_mm, py_mm, _pz = snap["palm"]
            palm_view = self._map_to_view(px_mm, py_mm)
            for ftype, (tx_mm, ty_mm, _tz) in snap["tips"].items():
                tips_view[ftype] = self._map_to_view(tx_mm, ty_mm)

            grab = snap["grab"]
            openness = _clamp(1.0 - grab, 0.0, 1.0)
            speed = snap["palm_speed"]

            # palm keeps emitting rings; faster hand + more open -> brighter,
            # bigger, more frequent rings so motion reads as energy.
            interval = 0.16 - 0.09 * _clamp(speed / 600.0, 0.0, 1.0)
            if now - self._last_palm_emit >= interval:
                self._last_palm_emit = now
                intensity = 0.35 + 0.45 * _clamp(speed / 500.0, 0.0, 1.0)
                self._ripples.append(
                    Ripple(
                        palm_view[0], palm_view[1],
                        life=1.1,
                        r0=18.0,
                        r1=90.0 + 120.0 * openness,
                        w0=3.0,
                        rgb=(0.30, 0.75, 1.0),
                        peak=intensity,
                    )
                )

            # fingertips leave small quick pulses on a slower cadence
            if now - self._last_tip_emit >= 0.22:
                self._last_tip_emit = now
                for (tx, ty) in tips_view.values():
                    self._ripples.append(
                        Ripple(
                            tx, ty,
                            life=0.7,
                            r0=4.0,
                            r1=34.0,
                            w0=2.0,
                            rgb=(0.6, 0.95, 1.0),
                            peak=0.5,
                        )
                    )

            # pinch edge -> one bright fast shockwave from the palm
            pinched_now = snap["pinch"] >= 0.7
            if pinched_now and not self._pinched:
                self._ripples.append(
                    Ripple(
                        palm_view[0], palm_view[1],
                        life=0.55,
                        r0=10.0,
                        r1=280.0,
                        w0=6.0,
                        rgb=(1.0, 0.85, 0.5),
                        peak=0.95,
                    )
                )
            self._pinched = pinched_now
        else:
            self._pinched = False

        # drop dead ripples, cap the list so a long session can't grow unbounded
        self._ripples = [r for r in self._ripples if r.alive(now)]
        if len(self._ripples) > 200:
            self._ripples = self._ripples[-200:]

        self._view.set_scene(self._ripples, palm_view, tips_view, openness)
        self._view.setNeedsDisplay_(True)


def run() -> int:
    """
    open the visualizer and block until the app quits (Cmd-Q, or Ctrl-C in the
    launching terminal). returns a process exit code.
    """
    if not _APPKIT_OK:
        print("AppKit / PyObjC not available, can't run the visualizer.")
        return 1

    state = HandState()
    stop = threading.Event()
    reader = threading.Thread(target=_run_reader, args=(state, stop), daemon=True)
    reader.start()

    app = AppKit.NSApplication.sharedApplication()
    app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)

    controller = FieldController(state)
    warned_error = {"shown": False}

    def tick(_timer):
        snap_err = state.snapshot()["error"]
        if snap_err and not warned_error["shown"]:
            warned_error["shown"] = True
            print(f"\n{snap_err}\n(is leapd running? see docs/SETUP.md step 3)")
        controller.tick()

    timer = AppKit.NSTimer.scheduledTimerWithTimeInterval_repeats_block_(
        1.0 / 60.0, True, tick
    )
    AppKit.NSRunLoop.currentRunLoop().addTimer_forMode_(
        timer, AppKit.NSRunLoopCommonModes
    )

    print("orvix hand visualizer running. move your hand over the sensor.")
    print("Ctrl-C here (or Cmd-Q) to quit.")
    try:
        app.run()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    raise SystemExit(run())
