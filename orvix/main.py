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

import Quartz

from orvix import calibration
from orvix.config import load_config
from orvix.gesture_interpreter import GestureEvent, GestureInterpreter, GestureType
from orvix.coord_mapper import CoordMapper
from orvix.leap_client import LeapConnectionError, pick_hand, stream_frames
from orvix.mouse_control import DryRunMouseController, MouseController, QuartzMouseController

logger = logging.getLogger("orvix.main")


def _get_screen_size() -> tuple[int, int]:
    """main display size in pixels, used to scale hand position to screen coords."""
    display_id = Quartz.CGMainDisplayID()
    width = Quartz.CGDisplayPixelsWide(display_id)
    height = Quartz.CGDisplayPixelsHigh(display_id)
    return width, height


def _dispatch(
    event: GestureEvent,
    mapper: CoordMapper,
    mouse: MouseController,
) -> None:
    """turn one gesture event into the corresponding mouse_control call."""
    now = time.monotonic()

    if event.type == GestureType.HAND_LOST:
        # nothing to move, freeze the cursor right where it is rather than
        # letting a stale/garbage position jump it somewhere weird
        return

    if event.palm_position is None:
        return

    x, y = mapper.map_to_screen(event.palm_position, now)

    if event.type == GestureType.POINT_MOVE:
        mouse.move(x, y)
    elif event.type == GestureType.PINCH_DOWN:
        mouse.move(x, y)
        mouse.mouse_down()
    elif event.type == GestureType.PINCH_DRAG:
        mouse.drag_to(x, y)
    elif event.type == GestureType.PINCH_UP:
        mouse.mouse_up()
    elif event.type == GestureType.GRAB_SCROLL:
        # use palm velocity's y component to drive scroll speed/direction,
        # scaled down since leap velocity is mm/s and scroll wants small
        # integer "line" counts, not raw millimeters
        if event.palm_velocity is not None:
            _, vy, _ = event.palm_velocity
            scroll_amount = int(vy / 20)
            if scroll_amount != 0:
                mouse.scroll(0, scroll_amount)
    # GRAB_START / GRAB_END don't map to a mouse action on their own, they
    # just bracket the GRAB_SCROLL events


async def run_live(dry_run: bool, verbose: bool) -> None:
    settings = load_config()
    screen_width, screen_height = _get_screen_size()
    logger.info("screen size: %dx%d", screen_width, screen_height)

    mapper = CoordMapper(settings.calibration, screen_width, screen_height, settings)
    interpreter = GestureInterpreter(settings)
    mouse: MouseController = DryRunMouseController() if dry_run else QuartzMouseController()

    if dry_run:
        logger.info("running in --dry-run mode, not touching the real cursor")

    try:
        async for frame in stream_frames():
            hand = pick_hand(frame, settings.preferred_hand)
            events = interpreter.process_hand(hand)

            for event in events:
                if verbose:
                    logger.info("event: %s", event)
                _dispatch(event, mapper, mouse)
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
