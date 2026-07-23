"""
handrender.py

a full-screen, click-through visualizer that draws an *accurate* rigged hand
from the leapd skeleton feed: real finger bones (carp -> mcp -> pip -> dip ->
tip), a palm polygon, and the forearm, all shaded by depth so the hand reads
as a solid 3D thing floating over your desktop.

unlike handviz.py (the "energy field" ripple toy), this one renders the actual
tracked skeleton, so it's the thing to reach for when you want to *see the
hand model itself*, e.g. to sanity-check tracking or calibration. like handviz
it opens its own leapd connection and never touches the cursor, so orvix's
mouse pipeline can be off and this still runs as pure eye candy. launch with

    python -m orvix.handrender      (or: orvix hand)

it mirrors handviz's plumbing on purpose: a borderless, translucent,
click-through NSWindow at status level covering the whole screen; a background
thread runs leapd on its own asyncio loop and drops the newest hand snapshot
into a lock-guarded state; a main-thread NSTimer at ~60fps projects those
joints to screen and redraws. all AppKit work stays on the main thread.

projection: this deliberately mirrors coord_mapper.CoordMapper, the same
absolute mapping orvix's real cursor uses, so the rendered hand moves across
the screen exactly like the cursor does -- x_mm/y_mm through your calibration
box (the same one `orvix calibrate` writes) straight onto screen x/y, full
edge to edge. if you've calibrated, this is where your cursor would be *on
the main display*. depth (z_mm) drives no position at all, only a
size/brightness cue, so raising your hand moves the hand up the screen (not
just bigger), matching what people expect after using the mouse-control
pipeline.

note: unlike the real cursor pipeline (which maps across every active
display's combined bounds when multi_monitor is on, see displays.py), this
visualizer's window only ever covers the main display -- it doesn't attempt
the Quartz-to-Cocoa multi-screen coordinate math needed to span multiple
monitors. on a multi-monitor setup with multi_monitor enabled, hand movement
that would land the real cursor on a second screen won't show up here.
"""

from __future__ import annotations

import asyncio
import logging
import threading

from orvix.config import Settings, load_config
from orvix.leap_client import (
    LeapConnectionError,
    stream_latest_frames,
)

logger = logging.getLogger("orvix.handrender")

try:
    import AppKit
    import objc
    from objc import python_method

    _APPKIT_OK = True
except Exception:  # noqa: BLE001 - PyObjC/AppKit missing: nothing to draw into
    _APPKIT_OK = False


# leap finger type ids
_THUMB, _INDEX, _MIDDLE, _RING, _PINKY = 0, 1, 2, 3, 4

# the four joint positions leapd reports per finger, wrist-out to tip. drawing a
# bone between each consecutive pair gives the full finger. the thumb reports an
# identical carp and mcp (it has no separate metacarpal joint in the model), so
# its first "bone" is zero length and simply doesn't draw -- no special-casing.
_JOINT_KEYS = (
    "carpPosition",
    "mcpPosition",
    "pipPosition",
    "dipPosition",
    "tipPosition",
)

def _load_calibration():
    """
    invalid yaml, or valid yaml that isn't a mapping at the top level, makes
    load_config() crash -- the same gap run_live's CLI path, gui.py, and
    calibration.py all already guard against, just one more call site (this
    one hit at import time) that got missed.
    """
    try:
        return load_config().calibration
    except Exception as exc:  # noqa: BLE001 - a bad config file must not crash the visualizer
        logger.warning("failed to load config, falling back to defaults: %s", exc)
        return Settings().calibration


# the same calibration box orvix's real cursor maps against (from
# ~/.orvix/config.yaml if you've run `orvix calibrate`, otherwise config.py's
# defaults). loaded once at import time since it doesn't change mid-session.
_CALIBRATION = _load_calibration()


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _map_range(value: float, in_min: float, in_max: float, out_min: float, out_max: float) -> float:
    """linearly map value from [in_min, in_max] into [out_min, out_max], clamped to the output range."""
    if in_max == in_min:
        return (out_min + out_max) / 2
    t = (value - in_min) / (in_max - in_min)
    t = _clamp(t, 0.0, 1.0)
    return out_min + t * (out_max - out_min)


class HandsState:
    """
    thread-safe snapshot of every currently-tracked hand, handed from the
    leapd reader thread to the main-thread render timer. everything stays in
    leap mm; projection to screen coords happens on the render side against the
    live view size. holds a list of hands rather than just the first so both
    hands render when they're in view.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.hands: list[dict] = []
        # bumps every time a fresh frame lands, so the render side can tell a
        # real "no hands" frame from a stale snapshot it already drew.
        self.seq = 0
        self.error: str | None = None

    def update(self, hands: list[dict]) -> None:
        with self._lock:
            self.hands = hands
            self.seq += 1

    def set_error(self, msg: str) -> None:
        with self._lock:
            self.error = msg

    def snapshot(self) -> dict:
        with self._lock:
            return {"hands": list(self.hands), "seq": self.seq, "error": self.error}


def _parse_frame(frame: dict) -> list[dict]:
    """
    turn a raw leapd frame into a compact per-hand structure the renderer can
    project: palm/wrist/elbow points, pinch/grab, and each finger's joint
    chain. fingers live in the top-level "pointables" list keyed back to the
    hand by handId (see leap_client.fingertips_for_hand for the same join).
    """
    hands: list[dict] = []
    pointables = frame.get("pointables", [])
    for hand in frame.get("hands", []):
        hand_id = hand.get("id")
        fingers: dict[int, dict] = {}
        for p in pointables:
            if p.get("handId") != hand_id:
                continue
            ftype = p.get("type")
            if ftype is None:
                continue
            joints = []
            for key in _JOINT_KEYS:
                pos = p.get(key)
                if pos is not None:
                    joints.append((float(pos[0]), float(pos[1]), float(pos[2])))
            fingers[ftype] = {
                "joints": joints,
                "extended": bool(p.get("extended", False)),
            }
        palm = hand.get("palmPosition", (0.0, 0.0, 0.0))
        hands.append(
            {
                "palm": (float(palm[0]), float(palm[1]), float(palm[2])),
                "wrist": tuple(float(c) for c in hand.get("wrist", palm)),
                "elbow": tuple(float(c) for c in hand.get("elbow", palm)),
                "pinch": float(hand.get("pinchStrength", 0.0) or 0.0),
                "grab": float(hand.get("grabStrength", 0.0) or 0.0),
                "type": hand.get("type", "right"),
                "fingers": fingers,
            }
        )
    return hands


def _run_reader(state: HandsState, stop: threading.Event) -> None:
    """
    background thread: own asyncio loop, stream the newest frame from leapd,
    parse it, and push it into the shared state. only ever touches HandsState,
    never AppKit, so it's safe off the main thread.
    """
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    async def pump() -> None:
        try:
            async for frame in stream_latest_frames():
                if stop.is_set():
                    return
                state.update(_parse_frame(frame))
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
            logger.debug("hand renderer reader teardown was not clean", exc_info=True)
        finally:
            loop.close()


if _APPKIT_OK:

    class _HandView(AppKit.NSView):
        """
        draws whatever projected hands the controller hands it. dumb on purpose:
        all leap->screen projection happens in the controller, the view just
        strokes bones, fills joints, and lays down the palm polygon in the
        Cocoa px coords it's given. one draw pass per tick.
        """

        def initWithFrame_(self, frame):
            self = objc.super(_HandView, self).initWithFrame_(frame)
            if self is None:
                return None
            self._hands = []  # list of projected-hand dicts (see _project_hand)
            return self

        @python_method
        def set_hands(self, hands):
            self._hands = hands

        def isFlipped(self):
            return False  # Cocoa default: y grows up, matches our projection

        def drawRect_(self, _rect):
            try:
                for hand in self._hands:
                    self._draw_hand(hand)
            except Exception:  # noqa: BLE001 - never let a draw error hit Cocoa
                logger.debug("handrender draw failed", exc_info=True)

        @python_method
        def _stroke_capsule(self, p0, p1, width, rgb, alpha):
            path = AppKit.NSBezierPath.bezierPath()
            path.moveToPoint_(p0)
            path.lineToPoint_(p1)
            path.setLineWidth_(width)
            path.setLineCapStyle_(AppKit.NSLineCapStyleRound)
            r, g, b = rgb
            AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(
                r, g, b, alpha
            ).set()
            path.stroke()

        @python_method
        def _fill_disc(self, cx, cy, r, rgb, alpha):
            disc = AppKit.NSBezierPath.bezierPathWithOvalInRect_(
                ((cx - r, cy - r), (2 * r, 2 * r))
            )
            rr, gg, bb = rgb
            AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(
                rr, gg, bb, alpha
            ).set()
            disc.fill()

        @python_method
        def _draw_hand(self, hand):
            tint = hand["tint"]  # (r,g,b) base colour, warmer as you pinch
            scale = hand["scale"]  # px/mm, so line widths track hand size/depth

            # translucent palm slab first, so bones and joints sit on top of it
            poly = hand["palm_poly"]
            if len(poly) >= 3:
                path = AppKit.NSBezierPath.bezierPath()
                path.moveToPoint_(poly[0])
                for pt in poly[1:]:
                    path.lineToPoint_(pt)
                path.closePath()
                r, g, b = tint
                AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(
                    r, g, b, 0.10
                ).set()
                path.fill()

            # faint forearm so the hand feels attached to something
            arm = hand.get("arm")
            if arm is not None:
                self._stroke_capsule(arm[0], arm[1], 10.0 * scale, tint, 0.10)

            # bones: a soft wide glow pass under a bright core pass, brightness
            # shaded by each bone's depth (height above the sensor).
            for (p0, p1, depth) in hand["bones"]:
                bright = _clamp(0.45 + depth, 0.25, 1.0)
                self._stroke_capsule(p0, p1, 11.0 * scale, tint, 0.12 * bright)
                self._stroke_capsule(
                    p0, p1, 5.0 * scale, (0.85, 0.97, 1.0), 0.85 * bright
                )

            # joints: bright round caps, fingertips a touch bigger + whiter
            for (cx, cy, depth, is_tip) in hand["joints"]:
                bright = _clamp(0.5 + depth, 0.3, 1.0)
                r = (5.5 if is_tip else 4.0) * scale
                self._fill_disc(cx, cy, r * 1.8, tint, 0.18 * bright)
                self._fill_disc(cx, cy, r, (0.92, 0.99, 1.0), 0.95 * bright)


class HandController:
    """
    owns the full-screen window + view and the leap->screen projection. each
    tick it reads the latest hand snapshot, projects every joint to Cocoa px,
    and hands the view a ready-to-draw scene. main-thread only; a safe no-op
    without AppKit so headless imports don't blow up.
    """

    def __init__(self, state: HandsState) -> None:
        self._state = state
        self._window = None
        self._view = None

    @property
    def available(self) -> bool:
        return _APPKIT_OK

    def _ensure_window(self) -> None:
        if self._window is not None:
            return
        screen = AppKit.NSScreen.mainScreen()
        frame = screen.frame()
        window = AppKit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            frame,
            AppKit.NSWindowStyleMaskBorderless,
            AppKit.NSBackingStoreBuffered,
            False,
        )
        window.setOpaque_(False)
        window.setBackgroundColor_(AppKit.NSColor.clearColor())
        window.setLevel_(AppKit.NSStatusWindowLevel)
        window.setIgnoresMouseEvents_(True)  # click-through
        window.setCollectionBehavior_(
            AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces
            | AppKit.NSWindowCollectionBehaviorStationary
        )
        view = _HandView.alloc().initWithFrame_(((0, 0), frame.size))
        window.setContentView_(view)
        window.orderFrontRegardless()
        self._window = window
        self._view = view

    @python_method
    def _project_hand(self, hand, size):
        """
        turn one leap-mm hand into a scene dict of Cocoa-px geometry:
        bones (with per-bone depth), joint discs, the palm polygon, the arm,
        plus an overall tint and scale. returns None if the hand has no usable
        finger joints yet (e.g. the frame arrived mid-acquisition).

        every joint is mapped independently through the same calibration box
        as the real cursor (see coord_mapper.CoordMapper), x_mm/y_mm straight
        onto screen x/y -- so the whole hand slides and reaches the same way
        the cursor does, edge to edge, rather than being confined to a
        fixed-size box in the middle of the screen. z_mm (depth) never moves
        anything; it only feeds a size/brightness cue.
        """
        fingers = hand["fingers"]
        if not fingers:
            return None

        cal = _CALIBRATION

        def proj(j):
            x_mm, y_mm, z_mm = j
            sx = _map_range(x_mm, cal.x_min, cal.x_max, 0.0, size.width)
            # leap y (height above sensor) maps straight onto Cocoa y (which
            # already grows upward), so a raised hand lands higher on screen
            # with no inversion needed -- unlike the cursor's Quartz mapping,
            # which is top-down and has to flip this axis. see handviz.py's
            # _map_to_view for the same point made about the ripple visualizer.
            sy = _map_range(y_mm, cal.y_min, cal.y_max, 0.0, size.height)
            # depth cue only, never position: nearer (bigger z) reads brighter
            # and slightly larger. in [-.5, .5]-ish around the box centre.
            zt = _map_range(z_mm, cal.z_min, cal.z_max, 0.0, 1.0)
            depth = zt - 0.5
            return sx, sy, depth

        # overall size cue from the palm's own depth, so a hand pushed toward
        # the sensor reads subtly bigger without this being where it moves to.
        _px, _py, palm_z = hand["palm"]
        palm_zt = _map_range(palm_z, cal.z_min, cal.z_max, 0.0, 1.0)
        scale = _clamp(0.75 + 0.5 * palm_zt, 0.7, 1.3)

        bones = []
        joints = []
        mcp_screen = {}
        for ftype, finger in fingers.items():
            jts = finger["joints"]
            if not jts:
                continue
            projected = [proj(j) for j in jts]
            for i in range(len(projected) - 1):
                (x0, y0, d0) = projected[i]
                (x1, y1, d1) = projected[i + 1]
                bones.append(((x0, y0), (x1, y1), (d0 + d1) * 0.5))
            for i, (x, y, d) in enumerate(projected):
                is_tip = i == len(projected) - 1
                joints.append((x, y, d, is_tip))
            # remember the mcp (knuckle, index 1) for the palm polygon
            if len(projected) >= 2:
                mcp_screen[ftype] = (projected[1][0], projected[1][1])

        # palm polygon: walk the knuckles across the hand, down to the wrist,
        # and back up the thumb side -- a rough but readable palm slab.
        wrist_s = proj(hand["wrist"])
        poly = []
        for ftype in (_INDEX, _MIDDLE, _RING, _PINKY):
            if ftype in mcp_screen:
                poly.append(mcp_screen[ftype])
        poly.append((wrist_s[0], wrist_s[1]))
        if _THUMB in mcp_screen:
            poly.append(mcp_screen[_THUMB])

        # forearm: wrist back to elbow, projected the same way.
        elbow_s = proj(hand["elbow"])
        arm = ((wrist_s[0], wrist_s[1]), (elbow_s[0], elbow_s[1]))

        # tint: cool cyan open, warming toward amber as pinch/grab closes, so
        # gestures read at a glance.
        close = _clamp(max(hand["pinch"], hand["grab"]), 0.0, 1.0)
        tint = (
            0.30 + 0.65 * close,
            0.78 - 0.25 * close,
            1.0 - 0.55 * close,
        )

        return {
            "bones": bones,
            "joints": joints,
            "palm_poly": poly,
            "arm": arm,
            "tint": tint,
            "scale": scale,
        }

    def tick(self) -> None:
        if not _APPKIT_OK:
            return
        try:
            self._ensure_window()
            self._tick_inner()
        except Exception:  # noqa: BLE001 - visual only, keep the timer alive
            logger.debug("handrender tick failed", exc_info=True)

    @python_method
    def _tick_inner(self) -> None:
        snap = self._state.snapshot()
        size = self._view.frame().size
        scenes = []
        for hand in snap["hands"]:
            scene = self._project_hand(hand, size)
            if scene is not None:
                scenes.append(scene)
        self._view.set_hands(scenes)
        self._view.setNeedsDisplay_(True)


def run() -> int:
    """
    open the rendered-hand visualizer and block until the app quits (Cmd-Q, or
    Ctrl-C in the launching terminal). returns a process exit code.
    """
    if not _APPKIT_OK:
        print("AppKit / PyObjC not available, can't run the visualizer.")
        return 1

    state = HandsState()
    stop = threading.Event()
    reader = threading.Thread(target=_run_reader, args=(state, stop), daemon=True)
    reader.start()

    app = AppKit.NSApplication.sharedApplication()
    app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)

    controller = HandController(state)
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

    print("orvix rendered-hand visualizer running. move your hand over the sensor.")
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
