"""
mouse_control.py

thin wrapper around macOS's Quartz/CoreGraphics CGEvent APIs. this is the
only module that actually touches the real cursor, everything else in the
pipeline just decides what should happen.

needs Accessibility + Input Monitoring permission granted to whatever
process runs python, see docs/SETUP.md step 5. if those aren't granted,
CGEventPost silently does nothing, no exception raised, so if orvix runs
without errors but the cursor never moves, that's almost always the answer.

also defines a DryRunMouseController with the same interface that just logs
what it would've done, so main.py --dry-run can exercise the full pipeline
without risking your actual desktop.
"""

from __future__ import annotations

import logging
from typing import Protocol

import Quartz

logger = logging.getLogger("orvix.mouse_control")


class MouseController(Protocol):
    def move(self, x: int, y: int) -> None: ...
    def mouse_down(self) -> None: ...
    def mouse_up(self) -> None: ...
    def drag_to(self, x: int, y: int) -> None: ...
    def scroll(self, dx: int, dy: int) -> None: ...
    def right_click(self) -> None: ...


class QuartzMouseController:
    """real mouse control, posts actual CGEvents to macOS."""

    def __init__(self):
        # remember whether the left button is currently "held" so move()
        # can decide between a plain kCGEventMouseMoved and a
        # kCGEventLeftMouseDragged, dragging needs the latter or macOS
        # won't treat it as a drag
        self._button_down = False
        # last pixel we actually posted, so we can skip no-op moves. every
        # post is a round trip to the window server and those can stall for
        # a few hundred ms under load, so the cheapest post is the one we
        # never make. a still hand at 75fps would otherwise fire ~75
        # identical events a second for no reason.
        self._last_pos: tuple[int, int] | None = None

    def move(self, x: int, y: int) -> None:
        if self._last_pos == (x, y):
            return
        event_type = Quartz.kCGEventLeftMouseDragged if self._button_down else Quartz.kCGEventMouseMoved
        event = Quartz.CGEventCreateMouseEvent(
            None, event_type, (x, y), Quartz.kCGMouseButtonLeft
        )
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)
        self._last_pos = (x, y)

    def mouse_down(self) -> None:
        pos = Quartz.CGEventGetLocation(Quartz.CGEventCreate(None))
        event = Quartz.CGEventCreateMouseEvent(
            None, Quartz.kCGEventLeftMouseDown, (pos.x, pos.y), Quartz.kCGMouseButtonLeft
        )
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)
        self._button_down = True
        # the next move has to go out even if it's the same pixel, since it
        # changes from a plain move to a drag event and macOS needs to see it
        self._last_pos = None

    def mouse_up(self) -> None:
        pos = Quartz.CGEventGetLocation(Quartz.CGEventCreate(None))
        event = Quartz.CGEventCreateMouseEvent(
            None, Quartz.kCGEventLeftMouseUp, (pos.x, pos.y), Quartz.kCGMouseButtonLeft
        )
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)
        self._button_down = False
        # same reason as mouse_down: drag -> plain move is a real change
        self._last_pos = None

    def drag_to(self, x: int, y: int) -> None:
        # same as move() while a button is down, kept as a separate method
        # so callers (gesture dispatch in main.py) can express intent clearly
        if self._last_pos == (x, y):
            return
        event = Quartz.CGEventCreateMouseEvent(
            None, Quartz.kCGEventLeftMouseDragged, (x, y), Quartz.kCGMouseButtonLeft
        )
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)
        self._last_pos = (x, y)

    def scroll(self, dx: int, dy: int) -> None:
        # unit "line" scrolling, two wheel count args = vertical, horizontal.
        # dy positive = scroll up in Quartz's convention
        event = Quartz.CGEventCreateScrollWheelEvent(
            None, Quartz.kCGScrollEventUnitLine, 2, dy, dx
        )
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)

    def right_click(self) -> None:
        """
        full press+release at the current position. one gesture is one
        click, there's no right-drag, so there's no reason to make callers
        track a down/up pair.
        """
        pos = Quartz.CGEventGetLocation(Quartz.CGEventCreate(None))
        for kind in (Quartz.kCGEventRightMouseDown, Quartz.kCGEventRightMouseUp):
            event = Quartz.CGEventCreateMouseEvent(
                None, kind, (pos.x, pos.y), Quartz.kCGMouseButtonRight
            )
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)


class DryRunMouseController:
    """logs intended mouse actions instead of touching the real cursor, for --dry-run."""

    def move(self, x: int, y: int) -> None:
        logger.info("[dry-run] move to (%d, %d)", x, y)

    def mouse_down(self) -> None:
        logger.info("[dry-run] mouse down")

    def mouse_up(self) -> None:
        logger.info("[dry-run] mouse up")

    def drag_to(self, x: int, y: int) -> None:
        logger.info("[dry-run] drag to (%d, %d)", x, y)

    def scroll(self, dx: int, dy: int) -> None:
        logger.info("[dry-run] scroll (%d, %d)", dx, dy)

    def right_click(self) -> None:
        logger.info("[dry-run] right click")
