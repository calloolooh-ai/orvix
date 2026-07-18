"""
config.py

loads and saves orvix's settings: calibration box, pinch/grab thresholds,
filter params, target fps, which hand to track.

defaults live here so orvix runs (in a rough, uncalibrated way) even before
you've run calibration.py. once you calibrate, the calibration box gets
written out to a local yaml file that overrides the defaults.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import yaml

from orvix.shortcuts import DEFAULT_RADIAL_ACTIONS as _DEFAULT_RADIAL_ACTIONS

# where the user's personal config lives. gitignored on purpose, since it'll
# have your specific hand-range calibration in it, not something to commit.
DEFAULT_CONFIG_PATH = Path.home() / ".orvix" / "config.yaml"


@dataclasses.dataclass
class CalibrationBox:
    """
    the Leap-space (millimeter) box that maps to your full screen, in
    absolute cursor mode. ignored entirely in relative mode.

    these defaults are a guess at where a hand sits above a desk, and a
    guess is all they can be, since it depends on your chair, desk height
    and where you put the sensor. `orvix calibrate` measures your actual
    reach and is still worth doing, but the defaults should at least be
    usable without it.

    the old defaults (x +-120, y 100-300) were basically the Leap SDK's
    stock InteractionBox, and they were noticeably too low: measured on a
    real person, a hand at rest sits around y=300 and sweeps up past 470,
    so nearly everything clamped to the top edge of the screen and the
    cursor pinned there. these are shifted up and widened to match hands
    where they actually are.
    """

    x_min: float = -150.0
    x_max: float = 150.0
    # leap's y axis is height above the device, not screen-style top-to-bottom,
    # so a bigger y means your hand is higher up
    y_min: float = 150.0
    y_max: float = 400.0
    # z is depth (toward/away from you), we mostly ignore it for 2D cursor
    # mapping right now but keep it around for future gestures (e.g. push to click)
    z_min: float = -80.0
    z_max: float = 80.0


@dataclasses.dataclass
class Settings:
    calibration: CalibrationBox = dataclasses.field(default_factory=CalibrationBox)

    # which hand to track when both are visible. "right", "left", or "first"
    # (whichever leapd reports first in the frame, simplest/fastest option)
    preferred_hand: str = "right"

    # ignore the hand when it's closer to the sensor than this (mm above it).
    # the leap sees through a pyramid that narrows as it approaches the
    # device, so right down near the surface there's barely any field of view
    # left and tracking gets erratic: positions jump, fingers get mixed up,
    # pinch strength flickers. xleapmouse has a cutoff for the same reason.
    # treating it as "no hand" is better than acting on garbage, and it gives
    # you a deliberate way to park: drop your hand to the desk and the cursor
    # stops dead. 0 disables it.
    min_hand_height_mm: float = 60.0

    # how hand movement becomes cursor movement. "absolute" or "relative".
    #
    # absolute: where your hand is inside the calibration box IS where the
    #   cursor is on screen. point at a corner and the cursor is there. needs
    #   calibration to feel right, and the box is a rectangle while the leap's
    #   actual field of view is a pyramid, so when your hand is low you can't
    #   reach the left/right extremes and the screen edges go dead.
    #
    # relative: the cursor moves by how far your hand moved, like a trackpad.
    #   no calibration involved at all (the box is ignored), and no dead edges,
    #   because nothing depends on absolute position. you lose "point at it and
    #   the cursor is there", and you can re-centre by pulling your hand out of
    #   view and bringing it back.
    #
    # tilt: hold your hand still and tilt it, the cursor drifts that way like a
    #   joystick. flat means stop. your hand basically doesn't travel, so it's
    #   by far the least tiring for long sessions and never runs out of room.
    #   PyLeapMouse added a tilt mode because they found it "gives exceptionally
    #   better control than the most obvious point-at-screen method". slowest of
    #   the three for crossing a big screen.
    cursor_mode: str = "relative"

    # -- relative mode tuning, ignored in absolute mode --
    #
    # gain is px of cursor per mm of hand, and it scales with how fast you're
    # moving, same idea as trackpad acceleration: slow means precise, fast
    # means you can cross the screen without dragging your arm across the desk.
    # below relative_slow_speed you get min gain, above relative_fast_speed you
    # get max gain, linear in between.
    relative_min_gain: float = 3.0
    relative_max_gain: float = 18.0
    relative_slow_speed: float = 50.0  # mm/s
    relative_fast_speed: float = 600.0  # mm/s

    # -- tilt mode tuning, ignored in the other modes --
    #
    # where "flat" actually is for you, subtracted before anything else.
    #
    # a joystick needs centring and so does this. nobody's hand rests at a
    # true zero: measured on a real right hand, a comfortable flat palm sits
    # at x=-0.165, which is outside the deadzone below, so an uncentred tilt
    # mode just creeps left forever. your natural roll depends on your hand,
    # your wrist and where the sensor is, so it can't be a fixed default
    # (a left hand rolls the other way). `orvix calibrate` measures it.
    tilt_center_x: float = 0.0
    tilt_center_z: float = 0.0

    # how far you have to tilt (past centre) before the cursor moves at all.
    # the palm normal is noisy (about +-0.1 measured while holding still), so
    # this has to comfortably clear that or the cursor jitters at rest.
    tilt_deadzone: float = 0.15
    # tilt at/above this (as a fraction of fully sideways) gives max speed.
    # keeping it well under 1.0 means you never have to tilt uncomfortably far.
    tilt_full: float = 0.6
    # cursor speed at full deflection, px/sec
    tilt_max_speed: float = 1400.0

    # pinchStrength above this counts as a pinch-click. leapd reports this
    # as a 0-1 float, already computed for us, no need to derive it from
    # fingertip distances ourselves
    pinch_threshold: float = 0.75
    # once pinched, strength has to drop below this to count as released.
    # keeping this lower than pinch_threshold gives us hysteresis so a pinch
    # sitting right at the boundary doesn't rapid-fire click/unclick
    pinch_release_threshold: float = 0.5

    # click stabilisation. as soon as pinch strength crosses this we stop
    # moving the cursor, so the click lands where you aimed.
    #
    # this is THE classic failure of every hand-tracked cursor: closing your
    # fingers drags your whole palm a little, so the cursor slides off the
    # target in the moments before the click registers, and you miss. other
    # projects hit it too and mostly worked around it by hand (one blog's
    # advice is literally to rest your thumb against your middle finger to
    # physically brace the drift).
    #
    # freezing solves it properly: the arming threshold is well below
    # pinch_threshold, so the cursor locks while you're still closing your
    # fingers and the click fires wherever you were pointing when you
    # started. set it to 0 to disable freezing entirely.
    pinch_freeze_threshold: float = 0.3

    # pinch your thumb to your MIDDLE finger instead of your index and you
    # get a right click. leapd's pinchStrength doesn't say which finger you
    # pinched with, so we work it out ourselves: whichever fingertip is
    # closest to the thumb at the moment the pinch registers wins.
    #
    # needs the frame to carry pointables. if it doesn't, this quietly does
    # nothing and every pinch is a left click, which is the old behaviour.
    right_click_on_middle_finger_pinch: bool = True

    grab_threshold: float = 0.85
    grab_release_threshold: float = 0.6
    # require a real closed fist to start a grab, not just a high grabStrength.
    # leapd reports grabStrength high even for a loose partial curl, which
    # made grab fire before the hand was actually closed. when this is on we
    # also check the finger "extended" flags and only start a grab if at most
    # grab_fist_max_extended fingers are still out. defaults to 1 to forgive
    # the thumb, which often reads as extended even in a clenched fist.
    # if the frame carries no finger-extension data we can't verify the fist
    # and fall back to grabStrength alone, so grab still works.
    grab_require_fist: bool = True
    grab_fist_max_extended: int = 1

    # what the pinch and grab gestures actually do to the mouse. one of
    # "click" (down/drag/up -> mouse_down/drag_to/mouse_up) or "scroll"
    # (drag/scroll frames drive the scroll wheel via palm velocity) or
    # "disabled" (gesture is tracked but produces no mouse action).
    # defaults match orvix's original v1 behavior: pinch clicks/drags,
    # grab scrolls. swappable so e.g. someone who scrolls a lot more than
    # they drag can put scroll on the easier-to-hold pinch instead.
    pinch_action: str = "click"
    grab_action: str = "scroll"

    # radial menu (gesture 12): draw a circle to pop up a wheel of actions,
    # then point at a wedge and either pinch or just rest on it (dwell) to
    # fire. the wedges are keyboard shortcuts (see shortcuts.RADIAL_SHORTCUTS)
    # so it needs no extra permissions beyond what key control already uses.
    radial_menu_enabled: bool = True
    # how long (seconds) to rest on a wedge before it fires without a pinch.
    # 0 disables dwell entirely, leaving pinch-to-select only.
    radial_dwell_seconds: float = 0.6
    # radius (screen px) around the wheel centre that selects nothing, so the
    # hand sitting near the middle right after the circle doesn't pick a wedge.
    radial_dead_zone_px: float = 55.0
    # the wedges, clockwise from top. each must be a key in
    # shortcuts.RADIAL_SHORTCUTS, plus "close" which just dismisses.
    radial_actions: list[str] = dataclasses.field(
        default_factory=lambda: list(_DEFAULT_RADIAL_ACTIONS)
    )
    # how round a motion has to be to open the wheel: total swept angle in
    # degrees, and the smallest loop (mm) that counts as deliberate.
    radial_open_sweep_deg: float = 300.0
    radial_open_min_radius_mm: float = 25.0

    # the five extra gestures (see extra_gestures.py). each is independently
    # toggleable; a couple share a hand pose with core gestures so they can be
    # switched off if they get in the way.
    #  - zoom:    pull two pinching hands apart / together
    #  - volume:  twist your wrist while making a fist (coexists with grab
    #             scroll, which reads vertical motion instead of twist)
    #  - dwell:   rest the cursor still to left-click, hands-free
    #  - pause:   hold both open palms out ("stop") to suspend/resume orvix
    #  - confirm: hold a thumbs-up to press Return
    zoom_enabled: bool = True
    fist_twist_volume_enabled: bool = True
    dwell_click_enabled: bool = True
    palms_out_pause_enabled: bool = True
    thumbs_up_confirm_enabled: bool = True
    # tuning
    zoom_step_mm: float = 14.0
    volume_step_deg: float = 12.0
    volume_step_percent: int = 6
    # dwell stillness is judged in Leap palm millimetres, not screen pixels,
    # so we don't have to re-map (and thus re-filter) the cursor position.
    dwell_click_radius_mm: float = 12.0
    dwell_click_seconds: float = 1.5
    pause_hold_seconds: float = 0.6
    confirm_hold_seconds: float = 0.5

    # One Euro Filter params, see one_euro_filter.py for the actual math.
    #
    # the filter's cutoff is min_cutoff + beta * speed, so min_cutoff governs
    # how much it smooths a nearly-still hand (jitter control) and beta
    # governs how fast it stops smoothing once you start moving (lag control).
    # the paper's tuning procedure is exactly that: set min_cutoff for
    # acceptable jitter at rest, then raise beta until the lag goes away.
    #
    # beta was 0.007 (the paper's own demo value) and that turned out to be
    # far too low for a cursor. measured from rest, it took ~107ms before the
    # cursor kept up with a slow deliberate hand movement, which feels like
    # "slow to start, then it catches up". 0.05 cuts that to ~53ms, at a cost
    # of about 1px more wobble at rest (3.2 -> 4.2px, which is nothing on a
    # 3440px wide screen). min_cutoff stays at 1.0 because raising it adds
    # jitter without helping the start transient.
    one_euro_min_cutoff: float = 1.0
    one_euro_beta: float = 0.05

    # how long (seconds) a pinch has to hold before we treat it as a
    # drag-start instead of a plain click, so quick pinch-release taps don't
    # accidentally start a drag.
    # was 0.15 originally, but testing on real hardware showed that's way too
    # tight to actually land a click: a 15s session logged 568 drag frames
    # against only 6 pinches, i.e. nearly every pinch ran long and turned into
    # a drag. 0.3 leaves enough room for a deliberate tap.
    drag_hold_seconds: float = 0.3

    target_fps: int = 100


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> Settings:
    """
    load settings from yaml, falling back to defaults for anything missing
    (or for everything, if the file doesn't exist yet).
    """
    if not path.exists():
        return Settings()

    with path.open("r") as f:
        raw = yaml.safe_load(f) or {}

    calibration_raw = raw.pop("calibration", {})
    calibration = CalibrationBox(**calibration_raw)
    return Settings(calibration=calibration, **raw)


def save_config(settings: Settings, path: Path = DEFAULT_CONFIG_PATH) -> None:
    """write settings out to yaml, creating the parent dir if needed."""
    path.parent.mkdir(parents=True, exist_ok=True)

    data = dataclasses.asdict(settings)
    with path.open("w") as f:
        yaml.safe_dump(data, f, default_flow_style=False, sort_keys=False)
