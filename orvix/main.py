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

import Quartz

from orvix import calibration
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

logger = logging.getLogger("orvix.main")

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


def _get_screen_size() -> tuple[int, int]:
    """main display size in pixels, used to scale hand position to screen coords."""
    display_id = Quartz.CGMainDisplayID()
    width = Quartz.CGDisplayPixelsWide(display_id)
    height = Quartz.CGDisplayPixelsHigh(display_id)
    return width, height


def _dispatch(
    event: GestureEvent,
    mapper: Mapper,
    mouse: MouseController,
    settings: Settings,
) -> None:
    """
    turn one gesture event into the corresponding mouse_control call, based
    on what settings.pinch_action / settings.grab_action say that gesture
    should do (see config.py). same event stream, swappable behavior.
    """
    now = time.monotonic()

    if event.type == GestureType.HAND_LOST:
        # nothing to move, freeze the cursor right where it is rather than
        # letting a stale/garbage position jump it somewhere weird.
        # tell the mapper too: in relative mode it has to drop its anchor,
        # or when your hand reappears it'd apply the whole distance your
        # hand travelled while out of view as one jump.
        mapper.reset()
        return

    if event.palm_position is None:
        return

    # tilt mode steers off the angle of your hand, not where it is, so it
    # needs the palm normal rather than the palm position
    if isinstance(mapper, TiltCoordMapper) and event.palm_normal is not None:
        x, y = mapper.map_to_screen_tilt(event.palm_normal, now)
    else:
        x, y = mapper.map_to_screen(event.palm_position, now)

    if event.type == GestureType.POINT_MOVE:
        mouse.move(x, y)
        return

    if event.type == GestureType.RIGHT_CLICK:
        # no move() first, same reasoning as the left click: the cursor was
        # frozen while your fingers closed and it's already on target
        mouse.right_click()
        return

    family, phase = _GESTURE_FAMILY[event.type]
    action = settings.pinch_action if family == "pinch" else settings.grab_action

    if action == "disabled":
        return

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


async def run_live(
    dry_run: bool,
    verbose: bool,
    settings: Settings | None = None,
    on_event: Callable[[GestureEvent], None] | None = None,
) -> None:
    """
    the live control loop. settings and on_event are optional hooks so
    non-CLI callers (the GUI) can supply their own live-reloaded settings
    and get a callback on every gesture event for status display, instead
    of only ever reading config from disk once and logging to stdout.
    """
    settings = settings if settings is not None else load_config()
    screen_width, screen_height = _get_screen_size()
    logger.info("screen size: %dx%d", screen_width, screen_height)

    mapper = make_mapper(settings, screen_width, screen_height)
    interpreter = GestureInterpreter(settings)
    mouse: MouseController = DryRunMouseController() if dry_run else QuartzMouseController()

    logger.info("cursor mode: %s", settings.cursor_mode)
    if dry_run:
        logger.info("running in --dry-run mode, not touching the real cursor")

    try:
        # latest-frame-wins: if a CGEventPost stall puts us behind, skip the
        # frames that piled up rather than replaying stale hand positions
        async for frame in stream_latest_frames():
            hand = pick_hand(frame, settings.preferred_hand)
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

            for event in events:
                if verbose:
                    logger.info("event: %s", event)
                if on_event is not None:
                    on_event(event)
                _dispatch(event, mapper, mouse, settings)
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
