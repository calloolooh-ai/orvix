"""
gesture_interpreter.py

takes raw hand dicts (already picked out of a frame by leap_client.pick_hand)
and turns them into semantic gesture events: POINT_MOVE, PINCH_DOWN,
PINCH_DRAG, PINCH_UP, GRAB_START, GRAB_SCROLL, GRAB_END, HAND_LOST.

pure logic, no websocket or macOS stuff in here on purpose, so it's fully
unit testable against recorded fixture frames with no hardware attached.
"""

from __future__ import annotations

import dataclasses
import enum
import time

from orvix.config import Settings


class GestureType(enum.Enum):
    POINT_MOVE = "point_move"
    PINCH_DOWN = "pinch_down"
    PINCH_DRAG = "pinch_drag"
    PINCH_UP = "pinch_up"
    GRAB_START = "grab_start"
    GRAB_SCROLL = "grab_scroll"
    GRAB_END = "grab_end"
    HAND_LOST = "hand_lost"


@dataclasses.dataclass
class GestureEvent:
    type: GestureType
    # raw Leap-space [x, y, z] mm palm position, present on every event
    # except HAND_LOST. coord_mapper.py turns this into screen pixels.
    palm_position: tuple[float, float, float] | None = None
    # only set on GRAB_SCROLL, palm velocity we can use to derive scroll speed/direction
    palm_velocity: tuple[float, float, float] | None = None


class _PinchState(enum.Enum):
    IDLE = "idle"
    DOWN = "down"  # pinched, but still within drag_hold_seconds so could still be a tap
    DRAGGING = "dragging"


class GestureInterpreter:
    """
    stateful across frames on purpose: whether a pinch counts as a click vs
    a drag depends on how long it's been held, and we use hysteresis
    (separate press/release thresholds) so a pinch strength sitting right on
    the boundary doesn't rapid-fire events. one instance per tracked hand
    for the lifetime of the session.
    """

    def __init__(self, settings: Settings):
        self._settings = settings
        self._pinch_state = _PinchState.IDLE
        self._pinch_started_at: float | None = None
        self._grabbing = False

    def process_hand(self, hand: dict | None) -> list[GestureEvent]:
        """
        feed this one hand dict per frame (or None if the hand isn't
        visible this frame). returns zero or more gesture events, in order,
        for this frame.
        """
        if hand is None:
            return self._handle_hand_lost()

        palm_position = tuple(hand["palmPosition"])
        palm_velocity = tuple(hand.get("palmVelocity", (0.0, 0.0, 0.0)))
        pinch_strength = hand.get("pinchStrength", 0.0)
        grab_strength = hand.get("grabStrength", 0.0)

        events: list[GestureEvent] = []

        # grab takes priority over pinch handling below: if you're mid-grab
        # (scrolling), we don't also want to be evaluating pinch state, that'd
        # be a confusing double gesture. in practice grabStrength and
        # pinchStrength don't both spike at once for a normal hand pose, but
        # being explicit here avoids relying on that.
        if self._grabbing:
            if grab_strength < self._settings.grab_release_threshold:
                self._grabbing = False
                events.append(GestureEvent(GestureType.GRAB_END, palm_position))
            else:
                events.append(
                    GestureEvent(GestureType.GRAB_SCROLL, palm_position, palm_velocity)
                )
            return events

        if grab_strength >= self._settings.grab_threshold:
            self._grabbing = True
            events.append(GestureEvent(GestureType.GRAB_START, palm_position))
            return events

        events.extend(self._handle_pinch(pinch_strength, palm_position))

        # only emit a plain cursor-move event if nothing else happened this
        # frame, a pinch/drag event already implies "the hand moved here"
        # for anything downstream mapping position to screen coords
        if not events and not self._freezing_for_click(pinch_strength):
            events.append(GestureEvent(GestureType.POINT_MOVE, palm_position))

        return events

    def _freezing_for_click(self, pinch_strength: float) -> bool:
        """
        true once you've started closing your fingers but before the pinch
        actually registers. during that window we hold the cursor still, so
        the small palm drift that closing your hand causes doesn't slide you
        off whatever you were aiming at. see pinch_freeze_threshold.

        only applies from IDLE: mid-drag you obviously still want to move.
        """
        threshold = self._settings.pinch_freeze_threshold
        if threshold <= 0:
            return False
        return self._pinch_state == _PinchState.IDLE and pinch_strength >= threshold

    def _handle_pinch(
        self, pinch_strength: float, palm_position: tuple[float, float, float]
    ) -> list[GestureEvent]:
        events: list[GestureEvent] = []

        if self._pinch_state == _PinchState.IDLE:
            if pinch_strength >= self._settings.pinch_threshold:
                self._pinch_state = _PinchState.DOWN
                self._pinch_started_at = time.monotonic()
                events.append(GestureEvent(GestureType.PINCH_DOWN, palm_position))

        elif self._pinch_state == _PinchState.DOWN:
            if pinch_strength < self._settings.pinch_release_threshold:
                # released before the drag-hold window elapsed, treat as a
                # plain click: PINCH_DOWN was already sent, this closes it out
                self._pinch_state = _PinchState.IDLE
                self._pinch_started_at = None
                events.append(GestureEvent(GestureType.PINCH_UP, palm_position))
            elif (
                self._pinch_started_at is not None
                and time.monotonic() - self._pinch_started_at
                >= self._settings.drag_hold_seconds
            ):
                self._pinch_state = _PinchState.DRAGGING
                events.append(GestureEvent(GestureType.PINCH_DRAG, palm_position))

        elif self._pinch_state == _PinchState.DRAGGING:
            if pinch_strength < self._settings.pinch_release_threshold:
                self._pinch_state = _PinchState.IDLE
                self._pinch_started_at = None
                events.append(GestureEvent(GestureType.PINCH_UP, palm_position))
            else:
                events.append(GestureEvent(GestureType.PINCH_DRAG, palm_position))

        return events

    def _handle_hand_lost(self) -> list[GestureEvent]:
        """
        hand dropped out of view. if we were mid-pinch or mid-grab, close
        that out cleanly instead of leaving mouse_control thinking a button
        is still held down forever.
        """
        events: list[GestureEvent] = [GestureEvent(GestureType.HAND_LOST)]

        if self._pinch_state != _PinchState.IDLE:
            events.insert(0, GestureEvent(GestureType.PINCH_UP))
            self._pinch_state = _PinchState.IDLE
            self._pinch_started_at = None

        if self._grabbing:
            events.insert(0, GestureEvent(GestureType.GRAB_END))
            self._grabbing = False

        return events
