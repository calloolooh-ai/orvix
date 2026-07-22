"""
main.py

entry point. wires leap_client -> gesture_interpreter -> coord_mapper ->
mouse_control together into one asyncio loop.

usage:
    python -m orvix.main                run live, controls your real cursor
    python -m orvix.main --dry-run      logs intended actions, doesn't touch the cursor
    python -m orvix.main --verbose      also logs every gesture event
    python -m orvix.main --calibrate    run calibration instead of live control
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import time
from collections.abc import Callable

from orvix import calibration
from orvix.displays import DesktopBounds, get_desktop_bounds
from orvix.config import Settings, load_config
from orvix.gesture_interpreter import GestureEvent, GestureInterpreter, GestureType
from orvix.coord_mapper import Mapper, TiltCoordMapper, make_mapper
from orvix.leap_client import (
    LeapConnectionError,
    extended_fingers_for_hand,
    fingertips_for_hand,
    pick_hand,
    stream_latest_frames,
)
from orvix.mouse_control import DryRunMouseController, MouseController, QuartzMouseController
from orvix.circle_detector import CircleDetector
from orvix.radial_menu import RadialMenu, RadialOutcome
from orvix.shortcuts import CONFIRM_SHORTCUT, NAMED_SHORTCUTS, RADIAL_SHORTCUTS
from orvix.extra_gestures import (
    ExtraAction,
    ExtraGestures,
    HandSignals,
    is_halt_hand,
    is_thumbs_up,
    roll_from_normal,
    scaled_volume_percent,
)
import math

logger = logging.getLogger("orvix.main")

# progress value fed to the dwell ring when cursor_ring_enabled wants a
# faint always-there highlight but no real dwell countdown is running.
# small enough that the countdown arc is imperceptible, just enough that
# render(progress) treats it as "show" rather than "hide".
_CURSOR_RING_BASELINE = 0.02

# how long the cursor ring flashes to full brightness right after a click
# actually lands, so cursor_ring_enabled gives visible feedback that a click
# registered, not just a dwell countdown / idle highlight.
_CLICK_FLASH_SECONDS = 0.15

# which (family, phase) each gesture event belongs to, used to look up the
# configurable action (settings.pinch_action / settings.grab_action) for it.
# POINT_MOVE and HAND_LOST aren't here, they're handled before this lookup.
_GESTURE_FAMILY: dict[GestureType, tuple[str, str]] = {
    GestureType.PINCH_DOWN: ("pinch", "start"),
    GestureType.PINCH_DRAG: ("pinch", "continue"),
    GestureType.PINCH_UP: ("pinch", "end"),
    GestureType.GRAB_START: ("grab", "start"),
    GestureType.GRAB_SCROLL: ("grab", "continue"),
    GestureType.GRAB_END: ("grab", "end"),
}




def _dispatch(
    event: GestureEvent,
    mapper: Mapper,
    mouse: MouseController,
    settings: Settings,
) -> bool:
    """
    turn one gesture event into the corresponding mouse_control call, based
    on what settings.pinch_action / settings.grab_action say that gesture
    should do (see config.py). same event stream, swappable behavior.

    returns True if this call made a click actually land (a right click, or
    the "end" phase of a pinch/grab whose action is "click"), so callers can
    use it as a "click just happened" signal, e.g. flashing the cursor ring.
    """
    now = time.monotonic()

    if event.type == GestureType.HAND_LOST:
        # nothing to move, freeze the cursor right where it is rather than
        # letting a stale/garbage position jump it somewhere weird.
        # tell the mapper too: in relative mode it has to drop its anchor,
        # or when your hand reappears it'd apply the whole distance your
        # hand travelled while out of view as one jump.
        mapper.reset()
        return False

    if event.palm_position is None:
        return False

    # tilt mode steers off the angle of your hand, not where it is, so it
    # needs the palm normal rather than the palm position
    if isinstance(mapper, TiltCoordMapper) and event.palm_normal is not None:
        x, y = mapper.map_to_screen_tilt(event.palm_normal, now)
    else:
        x, y = mapper.map_to_screen(event.palm_position, now)

    if event.type == GestureType.POINT_MOVE:
        mouse.move(x, y)
        return False

    if event.type == GestureType.RIGHT_CLICK:
        # no move() first, same reasoning as the left click: the cursor was
        # frozen while your fingers closed and it's already on target
        mouse.right_click()
        return True

    family, phase = _GESTURE_FAMILY[event.type]
    action = settings.pinch_action if family == "pinch" else settings.grab_action

    if action == "disabled":
        return False

    if action == "click":
        if phase == "start":
            # deliberately no move() here. the interpreter has been holding
            # the cursor still since you started closing your fingers (see
            # pinch_freeze_threshold), so the cursor is already sitting on
            # what you aimed at. moving to the palm position now would undo
            # exactly the drift correction we just did and put the click
            # wherever your hand slid to.
            mouse.mouse_down()
        elif phase == "continue":
            mouse.drag_to(x, y)
        elif phase == "end":
            mouse.mouse_up()
            return True
    elif action == "scroll":
        # only the "continue" phase carries a meaningful velocity to scroll
        # with, start/end are just no-ops for this action
        if phase == "continue" and event.palm_velocity is not None:
            # use palm velocity's y component to drive scroll speed/direction,
            # scaled down since leap velocity is mm/s and scroll wants small
            # integer "line" counts, not raw millimeters
            _, vy, _ = event.palm_velocity
            scroll_amount = int(vy / 20)
            if scroll_amount != 0:
                mouse.scroll(0, scroll_amount)
    return False


RadialListener = Callable[[dict | None], None]
DwellListener = Callable[[float | None], None]


def _radial_state(radial: RadialMenu, hovered: int | None, progress: float) -> dict:
    return {
        "center": radial.center,
        "actions": radial.actions,
        "hovered": hovered,
        "progress": progress,
    }


def _fire_radial(
    radial: RadialMenu,
    hand: dict | None,
    mapper: Mapper,
    mouse: MouseController,
    settings: Settings,
    now: float,
    on_radial: RadialListener | None = None,
    anchor: tuple[float, float] | None = None,
) -> tuple[float, float] | None:
    """
    drive the open radial menu for one frame from the raw hand, bypassing the
    normal cursor pipeline (while the wheel is up the hand is steering the
    wheel, not the cursor). fires the chosen wedge's keystroke, and resets the
    mapper on close so a relative-mode anchor doesn't lurch the cursor when
    normal control resumes. on_radial, if given, gets the live wheel state for
    the overlay each frame, and None on the frame it closes.

    the wheel is always drawn at screen centre (radial.center), but the raw
    mapper position generally isn't anywhere near there -- it's wherever the
    cursor mapping puts your hand. `anchor` is that raw mapped position from
    the moment the wheel opened; subtracting it out and adding it onto
    radial.center turns "how far the hand moved since opening" into an offset
    from the fixed centre, so pointing at a wedge still feels like pointing,
    just re-based off the middle of the screen. returns the anchor to carry
    into the next frame (None once the wheel's closed, so the caller drops it).
    """
    if hand is None:
        radial.cancel()
        mapper.reset()
        if on_radial is not None:
            on_radial(None)
        return None

    palm = tuple(hand["palmPosition"])
    raw = mapper.map_to_screen(palm, now)
    if anchor is None:
        # shouldn't normally happen (open() always sets one), but guards
        # against a stray call without one rather than pointing from (0, 0)
        anchor = raw
    cx, cy = radial.center
    pointer = (cx + (raw[0] - anchor[0]), cy + (raw[1] - anchor[1]))
    pinching = hand.get("pinchStrength", 0.0) >= settings.pinch_threshold
    upd = radial.update(pointer, pinching, now)

    if upd.outcome == RadialOutcome.FIRED and upd.fired_action:
        shortcut = RADIAL_SHORTCUTS.get(upd.fired_action)
        if shortcut is not None:
            mouse.key_shortcut(shortcut.keycode, shortcut.mods)
        logger.info("radial menu fired: %s", upd.fired_action)

    if upd.outcome != RadialOutcome.NONE:  # FIRED or DISMISSED -> wheel closed
        mapper.reset()
        if on_radial is not None:
            on_radial(None)
        return None

    if on_radial is not None:
        on_radial(_radial_state(radial, upd.hovered_index, upd.dwell_progress))
    return anchor


def _build_extras(settings: Settings) -> ExtraGestures:
    return ExtraGestures(
        zoom_enabled=settings.zoom_enabled,
        volume_enabled=settings.fist_twist_volume_enabled,
        dwell_enabled=settings.dwell_click_enabled,
        pause_enabled=settings.palms_out_pause_enabled,
        confirm_enabled=settings.thumbs_up_confirm_enabled,
        zoom_step_mm=settings.zoom_step_mm,
        volume_step_deg=settings.volume_step_deg,
        dwell_radius_px=settings.dwell_click_radius_mm,  # judged in palm mm
        dwell_seconds=settings.dwell_click_seconds,
        pause_hold_seconds=settings.pause_hold_seconds,
        confirm_hold_seconds=settings.confirm_hold_seconds,
    )


def _compute_signals(
    frame: dict,
    primary: dict | None,
    events: list[GestureEvent],
    settings: Settings,
) -> HandSignals:
    """
    boil a frame down to the handful of signals the extra gestures watch. all
    in raw Leap space (no cursor mapping), so it can't perturb cursor state.
    """
    sig = HandSignals()
    hands = frame.get("hands", [])

    # two-hand zoom: distance between the two palms while both hands pinch
    pinching = [h for h in hands if h.get("pinchStrength", 0.0) >= settings.pinch_threshold]
    if len(pinching) >= 2:
        a = tuple(pinching[0]["palmPosition"])
        b = tuple(pinching[1]["palmPosition"])
        sig.two_hand_pinch_span = math.dist(a, b)

    # both palms out -> "stop": count open, upright hands
    upright_open = 0
    for h in hands:
        ext = extended_fingers_for_hand(frame, h)
        normal = tuple(h.get("palmNormal", (0.0, -1.0, 0.0)))
        if is_halt_hand(ext, normal):
            upright_open += 1
    sig.palms_out = upright_open >= 2

    if primary is not None:
        normal = tuple(primary.get("palmNormal", (0.0, -1.0, 0.0)))
        if primary.get("grabStrength", 0.0) >= settings.grab_threshold:
            sig.fist_roll_rad = roll_from_normal(normal)
        sig.thumbs_up = is_thumbs_up(extended_fingers_for_hand(frame, primary), normal)

    # dwell only arms on a plain idle move frame (no pinch/grab/freeze in
    # play), tracked in palm mm so it doesn't need the cursor mapping
    move = next((e for e in events if e.type == GestureType.POINT_MOVE), None)
    if move is not None and move.palm_position is not None:
        sig.hover_point = (move.palm_position[0], move.palm_position[1])

    return sig


def _bounds_changed(
    current_width: float,
    current_height: float,
    current_origin: tuple[float, float],
    fresh: DesktopBounds,
) -> tuple[int, int, tuple[float, float]] | None:
    """
    compare the mapper's current screen bounds against a freshly-queried
    DesktopBounds. returns the new (width, height, origin) to apply if
    anything changed (a monitor got plugged/unplugged mid-session), or None
    if it's still the same desktop -- pulled out of run_live's loop so the
    comparison logic is testable without a live Quartz/leapd stack.
    """
    if (
        fresh.width != current_width
        or fresh.height != current_height
        or (fresh.origin_x, fresh.origin_y) != current_origin
    ):
        return fresh.width, fresh.height, (fresh.origin_x, fresh.origin_y)
    return None


def _execute_extras(
    actions: list[ExtraAction],
    mouse: MouseController,
    mapper: Mapper,
    settings: Settings,
    extras: ExtraGestures | None = None,
) -> None:
    if extras is None:
        # callers that don't care about twist-rate scaling (e.g. most tests)
        # can omit this; a fresh instance has rate 0, which scaled_volume_percent
        # clamps to volume_step_percent, matching the old fixed-step behavior.
        extras = ExtraGestures()
    for action in actions:
        if action == ExtraAction.ZOOM_IN:
            mouse.zoom(1)
        elif action == ExtraAction.ZOOM_OUT:
            mouse.zoom(-1)
        elif action == ExtraAction.VOLUME_UP:
            pct = scaled_volume_percent(
                extras.volume_twist_rate_deg_s,
                settings.volume_step_percent,
                settings.volume_max_percent,
                settings.volume_rate_slow_deg_s,
                settings.volume_rate_fast_deg_s,
            )
            mouse.set_volume_relative(pct)
        elif action == ExtraAction.VOLUME_DOWN:
            pct = scaled_volume_percent(
                extras.volume_twist_rate_deg_s,
                settings.volume_step_percent,
                settings.volume_max_percent,
                settings.volume_rate_slow_deg_s,
                settings.volume_rate_fast_deg_s,
            )
            mouse.set_volume_relative(-pct)
        elif action == ExtraAction.DWELL_CLICK:
            mouse.click()
        elif action == ExtraAction.CONFIRM:
            # thumbs_up_action is a name into shortcuts.NAMED_SHORTCUTS, same
            # table the radial wedges use, so it's remappable to anything in
            # there. an unrecognised name (e.g. an old/hand-edited config)
            # falls back to the original literal "confirm" behavior rather
            # than silently doing nothing.
            shortcut = NAMED_SHORTCUTS.get(settings.thumbs_up_action, CONFIRM_SHORTCUT)
            mouse.key_shortcut(shortcut.keycode, shortcut.mods)
        elif action in (ExtraAction.PAUSE_ON, ExtraAction.PAUSE_OFF):
            # freeze/unfreeze cleanly: drop the relative anchor so the cursor
            # doesn't jump when control resumes
            mapper.reset()
            logger.info("orvix %s", "paused" if action == ExtraAction.PAUSE_ON else "resumed")


async def run_live(
    dry_run: bool,
    verbose: bool,
    settings: Settings | None = None,
    on_event: Callable[[GestureEvent], None] | None = None,
    on_radial: RadialListener | None = None,
    on_dwell: DwellListener | None = None,
) -> None:
    """
    the live control loop. settings and on_event are optional hooks so
    non-CLI callers (the GUI) can supply their own live-reloaded settings
    and get a callback on every gesture event for status display, instead
    of only ever reading config from disk once and logging to stdout.
    """
    settings = settings if settings is not None else load_config()
    desktop = get_desktop_bounds(settings.multi_monitor)
    screen_width, screen_height = desktop.width, desktop.height
    screen_origin = (desktop.origin_x, desktop.origin_y)
    logger.info(
        "desktop bounds: %dx%d at (%d, %d) [multi_monitor=%s]",
        screen_width, screen_height, screen_origin[0], screen_origin[1], settings.multi_monitor,
    )

    mapper = make_mapper(settings, screen_width, screen_height, screen_origin=screen_origin)
    interpreter = GestureInterpreter(settings)
    mouse: MouseController = DryRunMouseController() if dry_run else QuartzMouseController()

    # gesture 12: circle to open the radial menu, then pinch or dwell a wedge.
    radial = RadialMenu(
        settings.radial_actions,
        dead_zone_px=settings.radial_dead_zone_px,
        dwell_seconds=settings.radial_dwell_seconds,
    )
    circle = CircleDetector(
        sweep_threshold_deg=settings.radial_open_sweep_deg,
        min_radius_mm=settings.radial_open_min_radius_mm,
    )
    # gestures 1/5/8/10/13: zoom, fist-twist volume, dwell-click, palms-out
    # pause, thumbs-up confirm
    extras = _build_extras(settings)

    logger.info("cursor mode: %s", settings.cursor_mode)
    if dry_run:
        logger.info("running in --dry-run mode, not touching the real cursor")

    # whether the dwell ring is currently shown, so we send exactly one "hide"
    # when the countdown ends rather than a None every idle frame
    dwell_shown = False

    # monotonic deadline for the click-flash (see _CLICK_FLASH_SECONDS): 0.0
    # means no flash pending
    flash_until = 0.0

    # the mapper's screen position for the hand at the moment the wheel
    # opened. the wheel is always drawn dead centre now (see the circle-open
    # block below), but wedge selection still needs to feel like pointing:
    # this anchor lets _fire_radial turn "how far the hand has moved since
    # opening" into an offset from that fixed centre, same relative feel as
    # before, just re-based off the middle of the screen instead of wherever
    # the cursor happened to be.
    radial_anchor: tuple[float, float] | None = None

    # get_desktop_bounds is a real Quartz call, cheap but not free -- poll it
    # on a timer rather than every frame. this is what catches a monitor
    # being plugged/unplugged mid-session; the menu's "Use all displays"
    # toggle already forces a full pipeline restart and doesn't need this.
    _BOUNDS_RECHECK_SECONDS = 2.0
    last_bounds_check = time.monotonic()

    # only meaningful for preferred_hand == "first": which hand id we were
    # tracking last frame, so a bystander's hand sorting ahead of it in
    # leapd's list can't silently hijack tracking (see pick_hand's docstring)
    last_hand_id = None

    try:
        # latest-frame-wins: if a CGEventPost stall puts us behind, skip the
        # frames that piled up rather than replaying stale hand positions
        async for frame in stream_latest_frames():
            hand = pick_hand(frame, settings.preferred_hand, last_hand_id)
            last_hand_id = hand.get("id") if hand is not None else None
            now = time.monotonic()

            if now - last_bounds_check >= _BOUNDS_RECHECK_SECONDS:
                last_bounds_check = now
                fresh = get_desktop_bounds(settings.multi_monitor)
                changed = _bounds_changed(screen_width, screen_height, screen_origin, fresh)
                if changed is not None:
                    logger.info(
                        "desktop bounds changed: %dx%d at (%d, %d) -> %dx%d at (%d, %d)",
                        screen_width, screen_height, screen_origin[0], screen_origin[1],
                        changed[0], changed[1], changed[2][0], changed[2][1],
                    )
                    screen_width, screen_height, screen_origin = changed
                    mapper.update_screen_bounds(screen_width, screen_height, screen_origin)

            # while the radial menu is up it owns the hand: point at a wedge
            # and pinch/dwell to pick. skip the normal cursor pipeline so we
            # don't also move the cursor or click underneath the wheel.
            if radial.is_open:
                radial_anchor = _fire_radial(
                    radial, hand, mapper, mouse, settings, now, on_radial, radial_anchor
                )
                continue

            # fingertips are only needed to tell an index pinch from a middle
            # one for right clicks, so don't bother digging them out if the
            # hand's gone
            fingertips = fingertips_for_hand(frame, hand) if hand is not None else None
            # finger-extension flags let the interpreter insist on a real
            # closed fist before starting a grab, rather than firing on a
            # loose partial curl that still reads high grabStrength
            extended_fingers = (
                extended_fingers_for_hand(frame, hand) if hand is not None else None
            )
            events = interpreter.process_hand(hand, fingertips, extended_fingers)

            if hand is None:
                circle.reset()
                extras.reset_transient()

            # extra gestures (zoom/volume/dwell/pause/confirm) run off raw hand
            # signals and can pause everything. do them before cursor dispatch
            # so the "stop" pose can gate this frame.
            signals = _compute_signals(frame, hand, events, settings)
            _execute_extras(extras.observe(signals, now), mouse, mapper, settings, extras)

            # feed the cursor dwell-countdown ring: send progress while it
            # climbs, and one hide when it ends. with cursor_ring_enabled, a
            # tiny baseline progress keeps the ring visible (as a faint
            # always-there highlight) whenever a hand is tracked, so the real
            # dwell countdown grows out of that baseline instead of popping
            # the ring in from nothing. a completed click briefly flashes the
            # ring to full so cursor_ring_enabled gives visible click feedback
            # too, not just a dwell countdown.
            if on_dwell is not None:
                progress = extras.dwell_progress
                if progress > 0.01:
                    on_dwell(progress)
                    dwell_shown = True
                elif settings.cursor_ring_enabled and now < flash_until:
                    on_dwell(1.0)
                    dwell_shown = True
                elif settings.cursor_ring_enabled and hand is not None:
                    on_dwell(_CURSOR_RING_BASELINE)
                    dwell_shown = True
                elif dwell_shown:
                    on_dwell(None)
                    dwell_shown = False

            if extras.paused:
                continue  # suspended: no cursor moves, clicks, or menu opening

            # a two-hand pinch is a zoom, not a click: don't also let the
            # primary hand's pinch drive the mouse this frame
            zoom_active = signals.two_hand_pinch_span is not None

            for event in events:
                if verbose:
                    logger.info("event: %s", event)
                if on_event is not None:
                    on_event(event)
                if not zoom_active:
                    if _dispatch(event, mapper, mouse, settings):
                        flash_until = now + _CLICK_FLASH_SECONDS

            # after normal dispatch, watch for a circle to open the wheel. uses
            # the horizontal palm plane (x, z); a completed loop always pops
            # the menu dead centre on screen, not wherever the cursor happened
            # to be, so it's in the same predictable spot every time and never
            # opens half off-screen near an edge.
            if settings.radial_menu_enabled and hand is not None and not zoom_active:
                palm = hand["palmPosition"]
                if circle.feed(palm[0], palm[2], now):
                    radial_anchor = mapper.map_to_screen(tuple(palm), now)
                    center = desktop.center
                    radial.open(center, now)
                    if on_radial is not None:
                        on_radial(_radial_state(radial, None, 0.0))
    except LeapConnectionError as exc:
        logger.error(str(exc))
        raise SystemExit(1) from exc


def main() -> None:
    parser = argparse.ArgumentParser(description="hand gesture mouse control via Leap Motion")
    parser.add_argument("--dry-run", action="store_true", help="log intended actions instead of moving the real cursor")
    parser.add_argument("--verbose", action="store_true", help="log every gesture event")
    parser.add_argument("--calibrate", action="store_true", help="run the calibration flow instead of live control")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    if args.calibrate:
        calibration.run()
        return

    asyncio.run(run_live(dry_run=args.dry_run, verbose=args.verbose))


if __name__ == "__main__":
    main()
