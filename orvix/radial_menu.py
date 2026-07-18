"""
radial_menu.py

the pop-up wheel (gesture 12). once it's open, you pick one of its wedges
by pointing at it and either pinching OR just resting on it (dwell). this
module is the pure selection engine for that: feed it a screen-space pointer
position, whether you're pinching, and the current time each frame, and it
tells you which wedge is highlighted and when one actually fires.

no Leap, no Cocoa, no keyboard here on purpose, same as gesture_interpreter:
the geometry and the pinch-vs-dwell timing are the fiddly bits worth unit
testing against made-up pointer paths with no hardware attached. main.py owns
turning a fired wedge into an actual keystroke, and the overlay just draws
whatever hovered_index we report.

wedges are laid out clockwise from straight up (index 0 = top), matching the
overlay mock, so wedge i sits at angle (-90 + i*45) degrees on screen.
"""

from __future__ import annotations

import dataclasses
import enum
import math


class RadialOutcome(enum.Enum):
    NONE = "none"  # still open, nothing fired this frame (just hovering)
    FIRED = "fired"  # a real wedge was chosen; see fired_index/fired_action
    DISMISSED = "dismissed"  # closed without picking anything


@dataclasses.dataclass
class RadialUpdate:
    outcome: RadialOutcome
    # which wedge the pointer is over right now, or None when it's parked in
    # the centre dead zone. the overlay highlights this one.
    hovered_index: int | None = None
    # only set when outcome is FIRED
    fired_index: int | None = None
    fired_action: str | None = None
    # 0..1 progress of the current dwell on hovered_index, for drawing a
    # fill/ring that creeps around the wedge as you hold on it. 0 while
    # pinch-selecting or parked in the dead zone.
    dwell_progress: float = 0.0


class RadialMenu:
    """
    stateful across frames for as long as the wheel is open. one instance
    reused for the whole session: open() arms it at a screen point, update()
    is called once per frame while it's up, and it closes itself the moment a
    wedge fires or you dismiss it.

    both selection paths are always live, whichever lands first wins:
      - pinch:  on the frame your pinch crosses from open to closed while
                pointing at a wedge, that wedge fires immediately.
      - dwell:  keep the pointer on the same wedge for dwell_seconds and it
                fires on its own, no pinch needed.

    the "close" wedge and a pinch in the centre dead zone both dismiss
    instead of firing, so an accidental circle is cheap to back out of.
    """

    def __init__(
        self,
        actions: list[str],
        *,
        dead_zone_px: float = 55.0,
        dwell_seconds: float = 0.6,
        close_action: str = "close",
    ):
        if not actions:
            raise ValueError("radial menu needs at least one action")
        self._actions = list(actions)
        self._dead_zone_px = dead_zone_px
        self._dwell_seconds = dwell_seconds
        self._close_action = close_action

        self._open = False
        self._center: tuple[float, float] = (0.0, 0.0)
        self._was_pinching = False
        self._dwell_index: int | None = None
        self._dwell_started_at: float | None = None

    @property
    def is_open(self) -> bool:
        return self._open

    @property
    def actions(self) -> list[str]:
        return list(self._actions)

    @property
    def center(self) -> tuple[float, float]:
        return self._center

    def open(self, center: tuple[float, float], now: float, *, pinching: bool = False) -> None:
        """
        pop the wheel up centred on `center` (screen px). `pinching` should be
        the pinch state on the opening frame: the circle gesture is drawn with
        an open hand, but seeding it means a pinch that happens to already be
        held can't be misread as an instant selection on frame one.
        """
        self._open = True
        self._center = center
        self._was_pinching = pinching
        self._dwell_index = None
        self._dwell_started_at = None

    def cancel(self) -> None:
        """close the wheel without firing (hand lost, pipeline stop, etc.)."""
        self._reset()

    def _reset(self) -> None:
        self._open = False
        self._was_pinching = False
        self._dwell_index = None
        self._dwell_started_at = None

    def _wedge_at(self, pointer: tuple[float, float]) -> int | None:
        """
        which wedge the pointer sits in, or None if it's inside the centre
        dead zone. dead zone matters because right after the circle your hand
        is near the middle, and we don't want that to count as pointing at
        wedge 0.
        """
        dx = pointer[0] - self._center[0]
        dy = pointer[1] - self._center[1]
        if math.hypot(dx, dy) < self._dead_zone_px:
            return None

        # screen angle: 0 = +x (right), +90 = down (y grows downward), so
        # straight up is -90. rotate so up becomes 0 and step clockwise.
        angle = math.degrees(math.atan2(dy, dx))
        step = 360.0 / len(self._actions)
        rel = (angle + 90.0) % 360.0
        return int(round(rel / step)) % len(self._actions)

    def update(self, pointer: tuple[float, float], pinching: bool, now: float) -> RadialUpdate:
        """
        advance one frame. call only while is_open. returns what the wheel is
        doing this frame; on FIRED or DISMISSED the wheel has already closed
        itself, so stop calling update() until the next open().
        """
        if not self._open:
            return RadialUpdate(RadialOutcome.NONE)

        hovered = self._wedge_at(pointer)
        pinch_edge = pinching and not self._was_pinching
        self._was_pinching = pinching

        # pinch selection: fires on the closing edge only, so one pinch is one
        # choice rather than a stream while you hold it.
        if pinch_edge:
            if hovered is None:
                self._reset()
                return RadialUpdate(RadialOutcome.DISMISSED)
            return self._fire(hovered)

        # dwell selection: the pointer has to stay on the *same* wedge; moving
        # to another wedge (or into the dead zone) restarts the clock so you
        # can't accumulate dwell by sweeping across several.
        if hovered is None:
            self._dwell_index = None
            self._dwell_started_at = None
            return RadialUpdate(RadialOutcome.NONE, hovered_index=None)

        if hovered != self._dwell_index:
            self._dwell_index = hovered
            self._dwell_started_at = now
            return RadialUpdate(RadialOutcome.NONE, hovered_index=hovered, dwell_progress=0.0)

        # note: don't write `self._dwell_started_at or now`; a start time of
        # exactly 0.0 is falsy and would read as "unset", zeroing the hold.
        started = self._dwell_started_at if self._dwell_started_at is not None else now
        held = now - started
        if self._dwell_seconds > 0 and held >= self._dwell_seconds:
            return self._fire(hovered)

        progress = 0.0 if self._dwell_seconds <= 0 else min(held / self._dwell_seconds, 1.0)
        return RadialUpdate(RadialOutcome.NONE, hovered_index=hovered, dwell_progress=progress)

    def _fire(self, index: int) -> RadialUpdate:
        action = self._actions[index]
        self._reset()
        if action == self._close_action:
            return RadialUpdate(RadialOutcome.DISMISSED)
        return RadialUpdate(
            RadialOutcome.FIRED, hovered_index=index, fired_index=index, fired_action=action
        )
