"""
extra_gestures.py

the five gestures beyond the core pinch/grab/point set, kept together here
because they're all the same shape: watch a per-frame signal over time and
occasionally emit an action. pure and testable, no Leap or Cocoa; main.py
computes the raw signals from each frame, feeds them in, and turns the
returned actions into real keystrokes/scrolls.

  - two-hand pinch apart/together  -> zoom in / out
  - fist + twist wrist             -> volume up / down
  - hold the cursor still (dwell)   -> left click
  - both palms out ("stop")         -> pause / resume orvix
  - thumbs-up hold                  -> confirm (Return)

the geometry helpers (is_thumbs_up, is_halt_hand, roll_from_normal) live here
too so the "what pose is this" decisions are unit-testable on their own.
"""

from __future__ import annotations

import dataclasses
import enum
import math

# SDK finger type ids, same as gesture_interpreter
THUMB = 0


class ExtraAction(enum.Enum):
    ZOOM_IN = "zoom_in"
    ZOOM_OUT = "zoom_out"
    VOLUME_UP = "volume_up"
    VOLUME_DOWN = "volume_down"
    DWELL_CLICK = "dwell_click"
    PAUSE_ON = "pause_on"
    PAUSE_OFF = "pause_off"
    CONFIRM = "confirm"


# ---- pose geometry (all pure) ----

def roll_from_normal(palm_normal: tuple[float, float, float]) -> float:
    """
    wrist-twist angle (radians) from the palm normal. palm-down reads ~0;
    twisting your wrist (pronation/supination) rotates the normal in the x/y
    plane, which is what we track for the volume knob. sign follows twist
    direction so accumulating the delta gives a smooth up/down.
    """
    x, y, _z = palm_normal
    return math.atan2(x, -y)


def hand_is_open(extended: set[int] | None) -> bool:
    """most fingers straight. None (no finger data) can't be judged open."""
    return extended is not None and len(extended) >= 4


def _palm_is_vertical(palm_normal: tuple[float, float, float]) -> bool:
    """
    palm facing sideways/forward rather than up or down, i.e. |normal.y| is
    small. separates a raised 'stop' hand or an upright thumbs-up from the
    normal palm-down cursor pose (normal.y ~ -1).
    """
    return abs(palm_normal[1]) < 0.6


def is_halt_hand(extended: set[int] | None, palm_normal: tuple[float, float, float]) -> bool:
    """an open hand held up facing out, one half of the two-hand 'stop'."""
    return hand_is_open(extended) and _palm_is_vertical(palm_normal)


def is_thumbs_up(extended: set[int] | None, palm_normal: tuple[float, float, float]) -> bool:
    """only the thumb extended, hand held upright rather than palm-down."""
    if extended is None:
        return False
    return extended == {THUMB} and _palm_is_vertical(palm_normal)


# ---- per-frame signal bundle main hands to the coordinator ----

@dataclasses.dataclass
class HandSignals:
    # distance (mm) between the two palms while both hands pinch, else None
    two_hand_pinch_span: float | None = None
    # wrist-twist angle (rad) while the primary hand is a fist, else None
    fist_roll_rad: float | None = None
    # screen-space cursor point on a frame where the hand is idly hovering
    # (nothing else happening), else None. drives dwell-click.
    hover_point: tuple[float, float] | None = None
    palms_out: bool = False
    thumbs_up: bool = False


# ---- individual detectors ----

class _ZoomDetector:
    def __init__(self, step_mm: float):
        self._step = step_mm
        self._last: float | None = None
        self._resid = 0.0

    def feed(self, span: float | None) -> list[ExtraAction]:
        if span is None:
            self._last = None
            self._resid = 0.0
            return []
        if self._last is None:
            self._last = span
            return []
        self._resid += span - self._last
        self._last = span
        out: list[ExtraAction] = []
        while self._resid >= self._step:
            out.append(ExtraAction.ZOOM_IN)
            self._resid -= self._step
        while self._resid <= -self._step:
            out.append(ExtraAction.ZOOM_OUT)
            self._resid += self._step
        return out


class _VolumeTwistDetector:
    def __init__(self, step_rad: float):
        self._step = step_rad
        self._last: float | None = None
        self._resid = 0.0

    def feed(self, roll: float | None) -> list[ExtraAction]:
        if roll is None:
            self._last = None
            self._resid = 0.0
            return []
        if self._last is None:
            self._last = roll
            return []
        # wrap so a step across the atan2 seam isn't read as a huge jump
        delta = (roll - self._last + math.pi) % (2 * math.pi) - math.pi
        self._resid += delta
        self._last = roll
        out: list[ExtraAction] = []
        while self._resid >= self._step:
            out.append(ExtraAction.VOLUME_UP)
            self._resid -= self._step
        while self._resid <= -self._step:
            out.append(ExtraAction.VOLUME_DOWN)
            self._resid += self._step
        return out


class _DwellClicker:
    def __init__(self, radius_px: float, dwell_seconds: float):
        self._radius = radius_px
        self._dwell = dwell_seconds
        self._anchor: tuple[float, float] | None = None
        self._since = 0.0
        self._fired = False
        # 0..1 progress of the current settle, for the cursor ring. 0 when not
        # dwelling or once the click has fired.
        self.progress = 0.0

    def feed(self, point: tuple[float, float] | None, now: float) -> list[ExtraAction]:
        if point is None or self._dwell <= 0:
            self._anchor = None
            self.progress = 0.0
            return []
        if self._anchor is None or math.dist(point, self._anchor) > self._radius:
            # first hover, or drifted off; (re)arm the timer here
            self._anchor = point
            self._since = now
            self._fired = False
            self.progress = 0.0
            return []
        if self._fired:
            self.progress = 0.0  # already clicked; ring stays hidden until you move
            return []
        self.progress = min((now - self._since) / self._dwell, 1.0)
        if now - self._since >= self._dwell:
            self._fired = True  # one click per settle; move away to re-arm
            self.progress = 0.0
            return [ExtraAction.DWELL_CLICK]
        return []


class _HoldToggle:
    """
    fires once when a boolean pose has been held continuously for `hold`
    seconds, then won't fire again until the pose drops. shared by the halt
    (pause) and thumbs-up (confirm) gestures.
    """

    def __init__(self, hold_seconds: float):
        self._hold = hold_seconds
        self._since: float | None = None
        self._consumed = False

    def feed(self, active: bool, now: float) -> bool:
        if not active:
            self._since = None
            self._consumed = False
            return False
        if self._since is None:
            self._since = now
        if not self._consumed and now - self._since >= self._hold:
            self._consumed = True
            return True
        return False


class ExtraGestures:
    """
    coordinator holding all five detectors plus the paused state. main calls
    observe() once per frame with the computed signals and executes whatever
    actions come back. while paused, every gesture except the pause toggle is
    swallowed, so the 'stop' pose is a real global off switch.
    """

    def __init__(
        self,
        *,
        zoom_enabled: bool = True,
        volume_enabled: bool = True,
        dwell_enabled: bool = True,
        pause_enabled: bool = True,
        confirm_enabled: bool = True,
        zoom_step_mm: float = 14.0,
        volume_step_deg: float = 12.0,
        dwell_radius_px: float = 18.0,
        dwell_seconds: float = 0.8,
        pause_hold_seconds: float = 0.6,
        confirm_hold_seconds: float = 0.5,
    ):
        self._zoom_on = zoom_enabled
        self._volume_on = volume_enabled
        self._dwell_on = dwell_enabled
        self._pause_on = pause_enabled
        self._confirm_on = confirm_enabled

        self._zoom = _ZoomDetector(zoom_step_mm)
        self._volume = _VolumeTwistDetector(math.radians(volume_step_deg))
        self._dwell = _DwellClicker(dwell_radius_px, dwell_seconds)
        self._pause = _HoldToggle(pause_hold_seconds)
        self._confirm = _HoldToggle(confirm_hold_seconds)

        self.paused = False

    @property
    def dwell_progress(self) -> float:
        """0..1 fill of the dwell-click ring, 0 when off/paused/not settling."""
        if not self._dwell_on or self.paused:
            return 0.0
        return self._dwell.progress

    def reset_transient(self) -> None:
        """
        drop mid-gesture accumulation when the hand leaves view, WITHOUT
        touching the paused state (a dropped hand shouldn't silently un-pause).
        """
        self._zoom.feed(None)
        self._volume.feed(None)
        self._dwell.feed(None, 0.0)

    def observe(self, sig: HandSignals, now: float) -> list[ExtraAction]:
        actions: list[ExtraAction] = []

        # pause toggle runs first and always, so it works even while paused
        if self._pause_on and self._pause.feed(sig.palms_out, now):
            self.paused = not self.paused
            actions.append(ExtraAction.PAUSE_ON if self.paused else ExtraAction.PAUSE_OFF)

        if self.paused:
            return actions  # everything else is off until you resume

        if self._zoom_on:
            actions += self._zoom.feed(sig.two_hand_pinch_span)
        if self._volume_on:
            actions += self._volume.feed(sig.fist_roll_rad)
        if self._dwell_on:
            actions += self._dwell.feed(sig.hover_point, now)
        if self._confirm_on and self._confirm.feed(sig.thumbs_up, now):
            actions.append(ExtraAction.CONFIRM)

        return actions
