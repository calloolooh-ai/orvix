"""
overlay.py

the on-screen radial wheel: a borderless, click-through, translucent window
that draws the menu while it's open and follows the live hover/dwell state.
it's purely cosmetic, the gesture works without it, so everything here is
wrapped so a drawing or AppKit hiccup can only cost the visual, never take
down the control pipeline.

all AppKit objects must be touched on the main thread. OverlayController's
methods assume they're already being called there (gui.py marshals the
background pipeline's callbacks onto the main thread before calling render).
"""

from __future__ import annotations

import logging

logger = logging.getLogger("orvix.overlay")

try:
    import AppKit
    import objc
    from objc import python_method

    _APPKIT_OK = True
except Exception:  # noqa: BLE001 - PyObjC/AppKit missing or broken: run without a visual
    _APPKIT_OK = False


# wedge action id -> short label drawn in the hub
_LABELS = {
    "mission_control": "Mission Control",
    "maximize": "Maximize",
    "app_switcher": "App Switcher",
    "undo": "Undo",
    "copy": "Copy",
    "paste": "Paste",
    "screenshot": "Screenshot",
    "close": "Close",
}

_BOX = 460.0  # window / view size in points
_R_OUT = 214.0
_R_IN = 96.0


if _APPKIT_OK:

    class _WheelView(AppKit.NSView):
        """draws the donut of wedges; state is set from python before display."""

        def initWithFrame_(self, frame):
            self = objc.super(_WheelView, self).initWithFrame_(frame)
            if self is None:
                return None
            self._actions = []
            self._hovered = None
            self._progress = 0.0
            return self

        @python_method
        def set_state(self, actions, hovered, progress):
            self._actions = list(actions)
            self._hovered = hovered
            self._progress = float(progress)

        def isFlipped(self):
            return False

        def drawRect_(self, _rect):
            try:
                self._draw()
            except Exception:  # noqa: BLE001 - never let a draw error escape into Cocoa
                logger.debug("overlay draw failed", exc_info=True)

        @python_method
        def _draw(self):
            n = len(self._actions)
            if n == 0:
                return
            cx = cy = _BOX / 2.0
            step = 360.0 / n

            for i, action in enumerate(self._actions):
                # clockwise from top: top is +90 in Cocoa's CCW/y-up angles,
                # so each wedge steps negative
                center_deg = 90.0 - i * step
                a0 = center_deg - step / 2.0 + 1.4
                a1 = center_deg + step / 2.0 - 1.4

                path = AppKit.NSBezierPath.bezierPath()
                path.appendBezierPathWithArcWithCenter_radius_startAngle_endAngle_clockwise_(
                    (cx, cy), _R_OUT, a1, a0, True
                )
                path.appendBezierPathWithArcWithCenter_radius_startAngle_endAngle_clockwise_(
                    (cx, cy), _R_IN, a0, a1, False
                )
                path.closePath()

                if i == self._hovered:
                    if action == "close":
                        AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(
                            0.60, 0.23, 0.20, 0.96
                        ).set()
                    else:
                        AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(
                            0.31, 0.42, 0.54, 0.96
                        ).set()
                else:
                    AppKit.NSColor.colorWithCalibratedWhite_alpha_(0.13, 0.86).set()
                path.fill()

                AppKit.NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.10).set()
                path.setLineWidth_(1.0)
                path.stroke()

                self._draw_label(action, center_deg, cx, cy, i == self._hovered)

            self._draw_dwell(cx, cy, step)
            self._draw_hub(cx, cy)

        @python_method
        def _draw_label(self, action, center_deg, cx, cy, hot):
            import math

            label = _LABELS.get(action, action)
            rad = math.radians(center_deg)
            r = (_R_OUT + _R_IN) / 2.0
            px = cx + r * math.cos(rad)
            py = cy + r * math.sin(rad)
            white = AppKit.NSColor.whiteColor() if hot else AppKit.NSColor.colorWithCalibratedWhite_alpha_(0.85, 1.0)
            attrs = {
                AppKit.NSFontAttributeName: AppKit.NSFont.systemFontOfSize_weight_(
                    12.0, AppKit.NSFontWeightMedium
                ),
                AppKit.NSForegroundColorAttributeName: white,
            }
            text = AppKit.NSString.stringWithString_(label)
            size = text.sizeWithAttributes_(attrs)
            text.drawAtPoint_withAttributes_((px - size.width / 2.0, py - size.height / 2.0), attrs)

        @python_method
        def _draw_dwell(self, cx, cy, step):
            if not self._progress or self._hovered is None:
                return
            center_deg = 90.0 - self._hovered * step
            a0 = center_deg - step / 2.0 + 1.4
            span = (step - 2.8) * self._progress
            arc = AppKit.NSBezierPath.bezierPath()
            arc.setLineWidth_(5.0)
            arc.appendBezierPathWithArcWithCenter_radius_startAngle_endAngle_clockwise_(
                (cx, cy), _R_OUT - 4.0, a0, a0 - span, True
            )
            AppKit.NSColor.whiteColor().set()
            arc.stroke()

        @python_method
        def _draw_hub(self, cx, cy):
            hub = AppKit.NSBezierPath.bezierPathWithOvalInRect_(
                ((cx - _R_IN + 8, cy - _R_IN + 8), (2 * (_R_IN - 8), 2 * (_R_IN - 8)))
            )
            AppKit.NSColor.colorWithCalibratedWhite_alpha_(0.10, 0.92).set()
            hub.fill()

            label = "orvix"
            if self._hovered is not None and 0 <= self._hovered < len(self._actions):
                label = _LABELS.get(self._actions[self._hovered], "")
            attrs = {
                AppKit.NSFontAttributeName: AppKit.NSFont.systemFontOfSize_weight_(
                    15.0, AppKit.NSFontWeightSemibold
                ),
                AppKit.NSForegroundColorAttributeName: AppKit.NSColor.whiteColor(),
            }
            text = AppKit.NSString.stringWithString_(label)
            size = text.sizeWithAttributes_(attrs)
            text.drawAtPoint_withAttributes_((cx - size.width / 2.0, cy - size.height / 2.0), attrs)


class OverlayController:
    """
    shows/updates/hides the wheel window. no-op (but safe to call) when AppKit
    isn't available, so headless/CLI use and tests don't need a display.
    """

    def __init__(self) -> None:
        self._window = None
        self._view = None
        self._screen_height = None
        self._warned = False

    @property
    def available(self) -> bool:
        return _APPKIT_OK

    def render(self, state: dict | None) -> None:
        """state dict to show the wheel, or None to hide it. main-thread only."""
        if not _APPKIT_OK:
            return
        try:
            if state is None:
                self._hide()
            else:
                self._show(state)
        except Exception:  # noqa: BLE001 - visual only, must not disturb the pipeline
            # surface the first failure loudly (once) so a broken overlay is
            # diagnosable; stay quiet after that so we don't spam per frame.
            if not self._warned:
                self._warned = True
                logger.warning("radial overlay failed to draw, running without it", exc_info=True)
            else:
                logger.debug("overlay render failed", exc_info=True)

    def _ensure_window(self) -> None:
        if self._window is not None:
            return
        rect = ((0.0, 0.0), (_BOX, _BOX))
        window = AppKit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            rect, AppKit.NSWindowStyleMaskBorderless, AppKit.NSBackingStoreBuffered, False
        )
        window.setOpaque_(False)
        window.setBackgroundColor_(AppKit.NSColor.clearColor())
        window.setLevel_(AppKit.NSStatusWindowLevel)
        window.setIgnoresMouseEvents_(True)  # click-through
        window.setCollectionBehavior_(
            AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces
            | AppKit.NSWindowCollectionBehaviorStationary
        )
        view = _WheelView.alloc().initWithFrame_(rect)
        window.setContentView_(view)
        self._window = window
        self._view = view
        self._screen_height = AppKit.NSScreen.mainScreen().frame().size.height

    def _show(self, state: dict) -> None:
        self._ensure_window()
        cx, cy = state["center"]
        # mapper hands us Quartz top-left screen coords; Cocoa windows are
        # bottom-left, so flip y against the main screen height.
        cocoa_y = (self._screen_height or 0) - cy
        origin_x = cx - _BOX / 2.0
        origin_y = cocoa_y - _BOX / 2.0
        self._window.setFrameOrigin_((origin_x, origin_y))
        self._view.set_state(state["actions"], state.get("hovered"), state.get("progress", 0.0))
        self._view.setNeedsDisplay_(True)
        self._window.orderFrontRegardless()

    def _hide(self) -> None:
        if self._window is not None:
            self._window.orderOut_(None)


_RING_BOX = 64.0


if _APPKIT_OK:

    class _RingView(AppKit.NSView):
        """a thin progress ring, filling clockwise from the top."""

        def initWithFrame_(self, frame):
            self = objc.super(_RingView, self).initWithFrame_(frame)
            if self is None:
                return None
            self._progress = 0.0
            return self

        @python_method
        def set_progress(self, progress):
            self._progress = max(0.0, min(float(progress), 1.0))

        def drawRect_(self, _rect):
            try:
                self._draw()
            except Exception:  # noqa: BLE001 - never let a draw error escape into Cocoa
                logger.debug("dwell ring draw failed", exc_info=True)

        @python_method
        def _draw(self):
            c = _RING_BOX / 2.0
            r = c - 6.0

            track = AppKit.NSBezierPath.bezierPathWithOvalInRect_(
                ((c - r, c - r), (2 * r, 2 * r))
            )
            track.setLineWidth_(4.0)
            AppKit.NSColor.colorWithCalibratedWhite_alpha_(0.0, 0.28).set()
            track.stroke()

            if self._progress <= 0.0:
                return
            arc = AppKit.NSBezierPath.bezierPath()
            arc.setLineWidth_(4.0)
            arc.setLineCapStyle_(AppKit.NSLineCapStyleRound)
            arc.appendBezierPathWithArcWithCenter_radius_startAngle_endAngle_clockwise_(
                (c, c), r, 90.0, 90.0 - self._progress * 360.0, True
            )
            AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(0.31, 0.42, 0.54, 0.95).set()
            arc.stroke()


class DwellRingController:
    """
    a small ring drawn around the live cursor while the dwell-click gesture is
    counting down. positions itself at the current mouse location each frame,
    so it needs only the progress value. safe no-op without AppKit.
    """

    def __init__(self) -> None:
        self._window = None
        self._view = None
        self._warned = False

    def render(self, progress: float | None) -> None:
        """0..1 to show the ring at the cursor, or None/0 to hide it."""
        if not _APPKIT_OK:
            return
        try:
            if not progress:
                self._hide()
            else:
                self._show(progress)
        except Exception:  # noqa: BLE001 - visual only
            if not self._warned:
                self._warned = True
                logger.warning("dwell ring failed to draw, running without it", exc_info=True)
            else:
                logger.debug("dwell ring render failed", exc_info=True)

    def _ensure_window(self) -> None:
        if self._window is not None:
            return
        rect = ((0.0, 0.0), (_RING_BOX, _RING_BOX))
        window = AppKit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            rect, AppKit.NSWindowStyleMaskBorderless, AppKit.NSBackingStoreBuffered, False
        )
        window.setOpaque_(False)
        window.setBackgroundColor_(AppKit.NSColor.clearColor())
        window.setLevel_(AppKit.NSStatusWindowLevel)
        window.setIgnoresMouseEvents_(True)
        window.setCollectionBehavior_(
            AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces
            | AppKit.NSWindowCollectionBehaviorStationary
        )
        view = _RingView.alloc().initWithFrame_(rect)
        window.setContentView_(view)
        self._window = window
        self._view = view

    def _show(self, progress: float) -> None:
        self._ensure_window()
        # NSEvent.mouseLocation is already in Cocoa bottom-left screen coords,
        # so the ring can follow the cursor with no mapping needed.
        loc = AppKit.NSEvent.mouseLocation()
        self._window.setFrameOrigin_((loc.x - _RING_BOX / 2.0, loc.y - _RING_BOX / 2.0))
        self._view.set_progress(progress)
        self._view.setNeedsDisplay_(True)
        self._window.orderFrontRegardless()

    def _hide(self) -> None:
        if self._window is not None:
            self._window.orderOut_(None)


_HUD_W = 340.0
_HUD_H = 220.0
_HUD_PAD = 16.0  # inset between the window edge and the coverage box


if _APPKIT_OK:

    class _CalibrationHUDView(AppKit.NSView):
        """
        draws the growing coverage rectangle + current-position dot for the
        live calibration sweep. state is a plain dict of the fractions
        orvix.calibration_viz.coverage_rect()/marker_fraction() produce
        (0..1, already reference-scaled), this view just maps them into its
        own pixel box -- same split as the radial wheel, math stays in a
        plain-python module and this only converts fractions to pixels.
        """

        def initWithFrame_(self, frame):
            self = objc.super(_CalibrationHUDView, self).initWithFrame_(frame)
            if self is None:
                return None
            self._rect = None  # (left, bottom, w, h) fractions, or None
            self._marker = None  # (x, y) fractions, or None
            self._fraction = 0.0
            self._n_samples = 0
            return self

        @python_method
        def set_state(self, rect, marker, fraction, n_samples):
            self._rect = rect
            self._marker = marker
            self._fraction = float(fraction)
            self._n_samples = int(n_samples)

        def isFlipped(self):
            return False

        def drawRect_(self, _rect):
            try:
                self._draw()
            except Exception:  # noqa: BLE001 - never let a draw error escape into Cocoa
                logger.debug("calibration HUD draw failed", exc_info=True)

        @python_method
        def _draw(self):
            box_w = _HUD_W - 2 * _HUD_PAD
            box_h = _HUD_H - 2 * _HUD_PAD - 26.0  # leave room for the label row
            box_origin = (_HUD_PAD, _HUD_PAD)

            frame_path = AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                (box_origin, (box_w, box_h)), 8.0, 8.0
            )
            AppKit.NSColor.colorWithCalibratedWhite_alpha_(0.10, 0.9).set()
            frame_path.fill()

            if self._rect is not None:
                left, bottom, w, h = self._rect
                # a sliver so a zero-area single sample still shows *something*
                px_w = max(3.0, w * box_w)
                px_h = max(3.0, h * box_h)
                fill_rect = (
                    (box_origin[0] + left * box_w, box_origin[1] + bottom * box_h),
                    (px_w, px_h),
                )
                fill_path = AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                    fill_rect, 3.0, 3.0
                )
                AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(
                    0.31, 0.55, 0.42, 0.55
                ).set()
                fill_path.fill()

            if self._marker is not None:
                mx, my = self._marker
                cx = box_origin[0] + mx * box_w
                cy = box_origin[1] + my * box_h
                r = 5.0
                dot = AppKit.NSBezierPath.bezierPathWithOvalInRect_(
                    ((cx - r, cy - r), (2 * r, 2 * r))
                )
                AppKit.NSColor.whiteColor().set()
                dot.fill()

            self._draw_label(box_origin, box_h)

        @python_method
        def _draw_label(self, box_origin, box_h):
            label = f"calibrating... {self._fraction * 100:3.0f}%  ({self._n_samples} samples)"
            attrs = {
                AppKit.NSFontAttributeName: AppKit.NSFont.systemFontOfSize_weight_(
                    12.0, AppKit.NSFontWeightMedium
                ),
                AppKit.NSForegroundColorAttributeName: AppKit.NSColor.colorWithCalibratedWhite_alpha_(
                    0.92, 1.0
                ),
            }
            text = AppKit.NSString.stringWithString_(label)
            text.drawAtPoint_withAttributes_((box_origin[0], box_origin[1] + box_h + 8.0), attrs)


class CalibrationOverlayController:
    """
    shows/updates/hides the live calibration HUD, parked near the top of the
    main screen while a sweep is running. same safe-no-op-without-AppKit
    contract as OverlayController: calibration itself works with or without
    this, it's purely the visual.
    """

    def __init__(self) -> None:
        self._window = None
        self._view = None
        self._warned = False

    @property
    def available(self) -> bool:
        return _APPKIT_OK

    def render(self, state: dict | None) -> None:
        """
        state: {"rect": coverage_rect() result, "marker": marker_fraction()
        result, "fraction": 0..1, "n_samples": int}, or None to hide.
        main-thread only, same as OverlayController.
        """
        if not _APPKIT_OK:
            return
        try:
            if state is None:
                self._hide()
            else:
                self._show(state)
        except Exception:  # noqa: BLE001 - visual only, must not disturb calibration
            if not self._warned:
                self._warned = True
                logger.warning("calibration HUD failed to draw, running without it", exc_info=True)
            else:
                logger.debug("calibration HUD render failed", exc_info=True)

    def _ensure_window(self) -> None:
        if self._window is not None:
            return
        rect = ((0.0, 0.0), (_HUD_W, _HUD_H))
        window = AppKit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            rect, AppKit.NSWindowStyleMaskBorderless, AppKit.NSBackingStoreBuffered, False
        )
        window.setOpaque_(False)
        window.setBackgroundColor_(AppKit.NSColor.clearColor())
        window.setLevel_(AppKit.NSStatusWindowLevel)
        window.setIgnoresMouseEvents_(True)  # click-through, same reason as the radial wheel
        window.setCollectionBehavior_(
            AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces
            | AppKit.NSWindowCollectionBehaviorStationary
        )
        view = _CalibrationHUDView.alloc().initWithFrame_(rect)
        window.setContentView_(view)
        self._window = window
        self._view = view

        # parked top-center of the main screen, out of the way of the menu
        # bar itself but somewhere you'll glance while sweeping your hand
        screen = AppKit.NSScreen.mainScreen().frame()
        origin_x = screen.origin.x + (screen.size.width - _HUD_W) / 2.0
        origin_y = screen.origin.y + screen.size.height - _HUD_H - 60.0
        window.setFrameOrigin_((origin_x, origin_y))

    def _show(self, state: dict) -> None:
        self._ensure_window()
        self._view.set_state(
            state.get("rect"), state.get("marker"), state.get("fraction", 0.0), state.get("n_samples", 0)
        )
        self._view.setNeedsDisplay_(True)
        self._window.orderFrontRegardless()

    def _hide(self) -> None:
        if self._window is not None:
            self._window.orderOut_(None)


def _demo() -> None:
    """
    `python -m orvix.overlay` shows the wheel in the middle of the screen and
    sweeps the highlight around it for a few seconds, so you can eyeball the
    overlay without the sensor. purely a visual check.
    """
    if not _APPKIT_OK:
        print("AppKit not available, can't preview the overlay")
        return

    app = AppKit.NSApplication.sharedApplication()
    app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)

    screen = AppKit.NSScreen.mainScreen().frame().size
    center = (screen.width / 2.0, screen.height / 2.0)  # already Cocoa-ish; flip below
    actions = [
        "mission_control", "maximize", "app_switcher", "undo",
        "copy", "paste", "screenshot", "close",
    ]
    controller = OverlayController()
    state = {"i": 0}

    def tick(_timer):
        i = state["i"]
        if i >= len(actions) * 6:
            AppKit.NSApp().terminate_(None)
            return
        hovered = (i // 3) % len(actions)
        progress = (i % 3) / 3.0
        controller.render(
            {"center": (center[0], screen.height - center[1]),  # flip back for render()
             "actions": actions, "hovered": hovered, "progress": progress}
        )
        state["i"] = i + 1

    delegate_timer = AppKit.NSTimer.scheduledTimerWithTimeInterval_repeats_block_(
        0.2, True, tick
    )
    AppKit.NSRunLoop.currentRunLoop().addTimer_forMode_(
        delegate_timer, AppKit.NSRunLoopCommonModes
    )
    print("showing radial overlay preview for a few seconds...")
    app.run()


if __name__ == "__main__":
    _demo()
