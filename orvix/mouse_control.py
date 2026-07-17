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


class QuartzMouseController:
    """real mouse control, posts actual CGEvents to macOS."""

    def __init__(self):
        # remember whether the left button is currently "held" so move()
        # can decide between a plain kCGEventMouseMoved and a
        # kCGEventLeftMouseDragged, dragging needs the latter or macOS
        # won't treat it as a drag
        self._button_down = False

    def move(self, x: int, y: int) -> None:
        event_type = Quartz.kCGEventLeftMouseDragged if self._button_down else Quartz.kCGEventMouseMoved
        event = Quartz.CGEventCreateMouseEvent(
            None, event_type, (x, y), Quartz.kCGMouseButtonLeft
        )
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)

    def mouse_down(self) -> None:
        pos = Quartz.CGEventGetLocation(Quartz.CGEventCreate(None))
        event = Quartz.CGEventCreateMouseEvent(
            None, Quartz.kCGEventLeftMouseDown, (pos.x, pos.y), Quartz.kCGMouseButtonLeft
        )
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)
        self._button_down = True

    def mouse_up(self) -> None:
        pos = Quartz.CGEventGetLocation(Quartz.CGEventCreate(None))
        event = Quartz.CGEventCreateMouseEvent(
            None, Quartz.kCGEventLeftMouseUp, (pos.x, pos.y), Quartz.kCGMouseButtonLeft
        )
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)
        self._button_down = False

    def drag_to(self, x: int, y: int) -> None:
        # same as move() while a button is down, kept as a separate method
        # so callers (gesture dispatch in main.py) can express intent clearly
        event = Quartz.CGEventCreateMouseEvent(
            None, Quartz.kCGEventLeftMouseDragged, (x, y), Quartz.kCGMouseButtonLeft
        )
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)

    def scroll(self, dx: int, dy: int) -> None:
        # unit "line" scrolling, two wheel count args = vertical, horizontal.
        # dy positive = scroll up in Quartz's convention
        event = Quartz.CGEventCreateScrollWheelEvent(
            None, Quartz.kCGScrollEventUnitLine, 2, dy, dx
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
