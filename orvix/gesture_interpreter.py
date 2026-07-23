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
import math
import time

from orvix.config import Settings


class GestureType(enum.Enum):
    POINT_MOVE = "point_move"
    PINCH_DOWN = "pinch_down"
    PINCH_DRAG = "pinch_drag"
    PINCH_UP = "pinch_up"
    # thumb-to-middle-finger pinch. fires as a complete click on its own
    # (down and up together) rather than a down/up pair, since there's no
    # such thing as a right drag here.
    RIGHT_CLICK = "right_click"
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
    # which way the palm faces. only used by tilt cursor mode, which steers
    # off the angle of your hand rather than where it is.
    palm_normal: tuple[float, float, float] | None = None


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

    # SDK finger type ids
    THUMB = 0
    INDEX = 1
    MIDDLE = 2

    def __init__(self, settings: Settings):
        self._settings = settings
        self._pinch_state = _PinchState.IDLE
        self._pinch_started_at: float | None = None
        self._grabbing = False
        self._right_click_held = False

    @staticmethod
    def _distance(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
        return math.dist(a, b)

    def _is_middle_finger_pinch(self, fingertips: dict[int, tuple[float, float, float]]) -> bool:
        """
        did you pinch with your middle finger rather than your index?

        leapd gives us a single pinchStrength with no clue which finger was
        involved, so compare the thumb's distance to each tip and take the
        nearer. without pointables in the frame we can't tell, so we say no
        and it stays a left click.
        """
        if not self._settings.right_click_on_middle_finger_pinch:
            return False

        thumb = fingertips.get(self.THUMB)
        index = fingertips.get(self.INDEX)
        middle = fingertips.get(self.MIDDLE)
        if thumb is None or index is None or middle is None:
            return False

        return self._distance(thumb, middle) < self._distance(thumb, index)

    def _is_fist(self, extended_fingers: set[int] | None) -> bool:
        """
        is the hand actually clenched into a fist right now?

        grabStrength alone reads high for a loose partial curl, so when
        grab_require_fist is on we also insist that few enough fingers are
        still extended. if the frame gave us no finger-extension data
        (extended_fingers is None) we can't check, and fall back to trusting
        grabStrength so grab doesn't silently stop working.
        """
        if not self._settings.grab_require_fist:
            return True
        if extended_fingers is None:
            return True
        return len(extended_fingers) <= self._settings.grab_fist_max_extended

    def process_hand(
        self,
        hand: dict | None,
        fingertips: dict[int, tuple[float, float, float]] | None = None,
        extended_fingers: set[int] | None = None,
    ) -> list[GestureEvent]:
        """
        feed this one hand dict per frame (or None if the hand isn't
        visible this frame). returns zero or more gesture events, in order,
        for this frame.

        fingertips (finger type -> tip position, from
        leap_client.fingertips_for_hand) is optional and only used to tell a
        middle-finger pinch from an index one for right clicks. everything
        else works without it.

        extended_fingers (the set of finger types currently straight, from
        leap_client.extended_fingers_for_hand, or None if the frame didn't
        report it) is optional and only used to require a real closed fist
        before starting a grab. see Settings.grab_require_fist.
        """
        if hand is None:
            return self._handle_hand_lost()

        palm_position = tuple(hand["palmPosition"])

        # too close to the sensor to trust. the leap's field of view narrows
        # to almost nothing near the surface, so tracking down there jumps
        # around and pinch strength flickers. treat it as no hand rather than
        # acting on nonsense, which also makes "drop your hand to the desk" a
        # deliberate way to park the cursor.
        if (
            self._settings.min_hand_height_mm > 0
            and palm_position[1] < self._settings.min_hand_height_mm
        ):
            return self._handle_hand_lost()

        palm_velocity = tuple(hand.get("palmVelocity", (0.0, 0.0, 0.0)))
        palm_normal = tuple(hand.get("palmNormal", (0.0, -1.0, 0.0)))
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

        if grab_strength >= self._settings.grab_threshold and self._is_fist(extended_fingers):
            if self._pinch_state != _PinchState.IDLE:
                # closing the rest of the way into a fist can carry pinchStrength
                # over threshold too (thumb and index are both curled in), so a
                # pinch can still be DOWN/DRAGGING the instant grab starts. if
                # pinch_action is "click" that pinch already fired a real
                # mouse_down; without releasing it here it stays stuck through
                # the whole grab since nothing else ever emits its PINCH_UP.
                events.append(GestureEvent(GestureType.PINCH_UP, palm_position))
                self._pinch_state = _PinchState.IDLE
                self._pinch_started_at = None
            self._grabbing = True
            events.append(GestureEvent(GestureType.GRAB_START, palm_position))
            return events

        # a middle-finger pinch is a right click, and it takes priority over
        # the normal pinch machine so it can't also fire a left click.
        # only checked from IDLE: once a left pinch/drag is underway, a
        # finger wandering nearer the thumb mustn't hijack it into a right
        # click mid-drag.
        if self._pinch_state == _PinchState.IDLE:
            if pinch_strength >= self._settings.pinch_threshold and self._is_middle_finger_pinch(
                fingertips or {}
            ):
                if not self._right_click_held:
                    self._right_click_held = True
                    events.append(GestureEvent(GestureType.RIGHT_CLICK, palm_position))
                return events
            if self._right_click_held:
                # hold it until you actually let go, so one pinch is one
                # right click rather than a stream of them
                if pinch_strength < self._settings.pinch_release_threshold:
                    self._right_click_held = False
                return events

        events.extend(self._handle_pinch(pinch_strength, palm_position))

        # only emit a plain cursor-move event if nothing else happened this
        # frame, a pinch/drag event already implies "the hand moved here"
        # for anything downstream mapping position to screen coords
        if not events and not self._freezing_for_click(pinch_strength):
            events.append(
                GestureEvent(GestureType.POINT_MOVE, palm_position, palm_normal=palm_normal)
            )

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

    def reset(self) -> list[GestureEvent]:
        """
        force pinch/grab state back to idle, emitting release events for
        anything that was actually held down. callers use this when the
        interpreter resumes after a stretch it wasn't fed at all (e.g. the
        radial menu owning the hand) -- without it, a pinch that was mid-hold
        when the gap started keeps its original _pinch_started_at, and real
        time elapsed during the gap can make the drag-hold check read as
        satisfied the instant we resume, firing a drag the user never held
        for. resetting to idle also means a real mouse-down from before the
        gap gets released here instead of staying stuck on.
        """
        events: list[GestureEvent] = []

        if self._pinch_state != _PinchState.IDLE:
            events.insert(0, GestureEvent(GestureType.PINCH_UP))
            self._pinch_state = _PinchState.IDLE
            self._pinch_started_at = None

        if self._grabbing:
            events.insert(0, GestureEvent(GestureType.GRAB_END))
            self._grabbing = False

        self._right_click_held = False
        return events

    def _handle_hand_lost(self) -> list[GestureEvent]:
        """
        hand dropped out of view. if we were mid-pinch or mid-grab, close
        that out cleanly instead of leaving mouse_control thinking a button
        is still held down forever.
        """
        events = self.reset()
        events.append(GestureEvent(GestureType.HAND_LOST))

        return events
