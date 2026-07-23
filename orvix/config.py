"""
config.py

loads and saves orvix's settings: calibration box, pinch/grab thresholds,
filter params, which hand to track.

defaults live here so orvix runs (in a rough, uncalibrated way) even before
you've run calibration.py. once you calibrate, the calibration box gets
written out to a local yaml file that overrides the defaults.
"""

from __future__ import annotations

import dataclasses
import logging
from pathlib import Path

import yaml

from orvix.shortcuts import DEFAULT_RADIAL_ACTIONS as _DEFAULT_RADIAL_ACTIONS
from orvix.shortcuts import NAMED_SHORTCUTS as _NAMED_SHORTCUTS
from orvix.shortcuts import RADIAL_SHORTCUTS as _RADIAL_SHORTCUTS

_VALID_RADIAL_ACTIONS = frozenset({*_RADIAL_SHORTCUTS, "close"})

# same set main.py's pinch/grab dispatch actually understands (see
# _GESTURE_FAMILY handling in main.py) -- anything else is a silent no-op
# there, but gui.py's ACTION_LABELS[...] indexes on it directly and raises
# KeyError, which crashes the (terminal-less) menu bar app on startup or
# profile load. same class of bug as radial_actions below.
_VALID_GESTURE_ACTIONS = frozenset({"click", "scroll", "disabled"})

# leap_client.pick_hand only ever matches "left"/"right" by exact hand.get("type")
# equality, or takes the "first" shortcut path -- anything else means pick_hand
# returns None every single frame, so hand tracking silently never starts.
_VALID_PREFERRED_HANDS = frozenset({"left", "right", "first"})

# coord_mapper.make_mapper only recognizes "relative" and "tilt" explicitly;
# anything else (including a typo) falls through to the absolute CoordMapper
# silently, same silent-misbehavior class as preferred_hand above.
_VALID_CURSOR_MODES = frozenset({"absolute", "relative", "tilt"})

logger = logging.getLogger(__name__)

# where the user's personal config lives. gitignored on purpose, since it'll
# have your specific hand-range calibration in it, not something to commit.
DEFAULT_CONFIG_PATH = Path.home() / ".orvix" / "config.yaml"

# named, saved/loadable configs (e.g. "demo", "precision"), swappable without
# clobbering the one active config.yaml. kept in their own subdir so listing
# profiles never picks up config.yaml itself.
DEFAULT_PROFILES_DIR = Path.home() / ".orvix" / "profiles"

_PROFILE_NAME_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")


def _validate_profile_name(name: str) -> None:
    """
    profile names become filenames (name.yaml), so keep them to a safe,
    unambiguous charset instead of trying to sanitize/escape arbitrary path
    input. rejects "", ".", "..", path separators, etc.
    """
    if not name or not set(name) <= _PROFILE_NAME_CHARS:
        raise ValueError(
            f"invalid profile name {name!r}: use only letters, digits, '-' and '_'"
        )


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
    # 0 disables dwell entirely, leaving pinch-to-select only (the default:
    # dwell-in-the-wheel fired too eagerly right after the opening circle).
    radial_dwell_seconds: float = 0.0
    # radius (screen px) around the wheel centre that selects nothing, so the
    # hand sitting near the middle right after the circle doesn't pick a wedge.
    radial_dead_zone_px: float = 55.0
    # the wedges, clockwise from top. each must be a key in
    # shortcuts.RADIAL_SHORTCUTS, plus "close" which just dismisses.
    radial_actions: list[str] = dataclasses.field(
        default_factory=lambda: list(_DEFAULT_RADIAL_ACTIONS)
    )
    # how round a motion has to be to open the wheel: total swept angle in
    # degrees, and the smallest loop (mm) that counts as deliberate. set well
    # past 360 so a casual curved hand-move can't reach it: you have to go all
    # the way around and then some, on purpose.
    radial_open_sweep_deg: float = 400.0
    radial_open_min_radius_mm: float = 35.0

    # the five extra gestures (see extra_gestures.py). each is independently
    # toggleable; a couple share a hand pose with core gestures so they can be
    # switched off if they get in the way.
    #  - zoom:    pull two pinching hands apart / together
    #  - volume:  twist your wrist while making a fist (coexists with grab
    #             scroll, which reads vertical motion instead of twist)
    #  - dwell:   rest the cursor still to left-click, hands-free
    #  - pause:   hold both open palms out ("stop") to suspend/resume orvix
    #  - confirm: hold a thumbs-up to fire thumbs_up_action (Return by default)
    zoom_enabled: bool = True
    fist_twist_volume_enabled: bool = True
    dwell_click_enabled: bool = True
    palms_out_pause_enabled: bool = True
    thumbs_up_confirm_enabled: bool = True
    # tuning
    zoom_step_mm: float = 14.0
    volume_step_deg: float = 12.0
    # volume_step_percent is the change applied for a slow, deliberate twist
    # (at/below volume_rate_slow_deg_s); it scales up to volume_max_percent
    # for a fast twist (at/above volume_rate_fast_deg_s), so flicking your
    # wrist changes volume by more per step than a lazy twist does instead of
    # every step being the same fixed size. see scaled_volume_percent() in
    # extra_gestures.py.
    volume_step_percent: int = 6
    volume_max_percent: int = 18
    volume_rate_slow_deg_s: float = 30.0
    volume_rate_fast_deg_s: float = 200.0
    # dwell stillness is judged in Leap palm millimetres, not screen pixels,
    # so we don't have to re-map (and thus re-filter) the cursor position.
    dwell_click_radius_mm: float = 12.0
    dwell_click_seconds: float = 1.5
    pause_hold_seconds: float = 0.6
    confirm_hold_seconds: float = 0.5
    # which shortcut a thumbs-up hold fires, a key into
    # shortcuts.NAMED_SHORTCUTS. defaults to Return since that's the
    # original v1 behavior (a literal "confirm"), but it's just another
    # named shortcut now, same table the radial wedges pick from, so it's
    # remappable to anything else in there (e.g. cmd+z if you'd rather
    # thumbs-up mean "undo").
    thumbs_up_action: str = "confirm"

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
    #
    # those numbers were originally a one-off manual measurement; `orvix
    # profile` (see orvix/perf.py) now reproduces the same lag-vs-jitter
    # comparison as a rerunnable report, so re-tuning this doesn't mean
    # re-deriving the methodology from scratch.
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

    # when true, the cursor can travel across every active display, not just
    # the main one: the mapper works over the bounding box of the whole
    # desktop (see displays.py) instead of just the main screen's pixels.
    # absolute mode still calibrates against whatever the calibration box
    # says, it's relative/tilt mode that benefits most since they had no
    # screen-size concept to update in the first place.
    #
    # off by default is wrong for most setups so it defaults on; the escape
    # hatch exists for single-monitor users who'd rather the cursor never
    # drifts toward a display they don't use (e.g. a sleeping/mirrored one
    # macOS still reports as active).
    multi_monitor: bool = True

    # off by default: a small ring around the live cursor at all times, not
    # just during a dwell-click countdown. reuses the same overlay window as
    # dwell click (see DwellRingController in overlay.py), it just always
    # renders a faint baseline ring instead of only appearing at progress > 0,
    # so the countdown animation grows out of the baseline instead of the ring
    # popping in from nothing. mainly useful for demos/screen recordings and
    # for people still learning where the tracked cursor position actually is.
    cursor_ring_enabled: bool = False


# fields a hand-edited config.yaml (or an old profile) could set to a
# nonsensical value with nothing downstream to catch it -- a threshold
# comparison or a percent clamp just quietly does the wrong thing instead of
# raising. (field name) -> (lo, hi) to clamp into, inclusive.
_UNIT_INTERVAL_FIELDS = (
    "pinch_threshold",
    "pinch_release_threshold",
    "pinch_freeze_threshold",
    "grab_threshold",
    "grab_release_threshold",
)
_PERCENT_FIELDS = ("volume_step_percent", "volume_max_percent")
_NONNEGATIVE_SECONDS_FIELDS = (
    "radial_dwell_seconds",
    "dwell_click_seconds",
    "pause_hold_seconds",
    "confirm_hold_seconds",
    "drag_hold_seconds",
)


def _field_default(field: str):
    """the dataclass-declared default for a Settings field, for fallback."""
    for f in dataclasses.fields(Settings):
        if f.name == field:
            return f.default
    raise KeyError(field)  # pragma: no cover - only hit for a typo'd field name above


def _clamp_field(settings: Settings, field: str, lo: float, hi: float) -> None:
    value = getattr(settings, field)
    try:
        clamped = max(lo, min(hi, value))
    except TypeError:
        # not just out-of-range but the wrong type entirely (e.g. a hand-edited
        # config.yaml with `pinch_threshold: "high"`) -- max()/min() would
        # otherwise raise straight out of load_config, and unlike the GUI
        # (which wraps load_config in a try/except, see gui.py's _safe_load_config)
        # the CLI (`orvix cli`) calls it with nothing catching this at all.
        default = _field_default(field)
        logger.warning(
            "config field %r was %r (%s), not a number -- falling back to %r",
            field, value, type(value).__name__, default,
        )
        setattr(settings, field, default)
        return
    if clamped != value:
        logger.warning(
            "config field %r was %r, outside [%s, %s] -- clamping to %r",
            field, value, lo, hi, clamped,
        )
        setattr(settings, field, clamped)


def _sanitize_radial_actions(settings: Settings) -> None:
    """
    drop any wedge name that isn't a real shortcuts.RADIAL_SHORTCUTS entry (or
    "close"), same silent-misbehavior-otherwise reasoning as _clamp_field: an
    unknown action still fires (main.py's RADIAL_SHORTCUTS.get() takes it),
    it just closes the wheel and does nothing, which is confusing to debug.
    worse, RadialMenu(actions=[]) raises outright if every entry gets filtered
    (or the list was already empty), which would otherwise crash pipeline
    startup instead of just clamping like everything else here -- so an empty
    result falls back to the full default wedge set instead.
    """
    actions = settings.radial_actions
    valid = [a for a in actions if a in _VALID_RADIAL_ACTIONS]
    if valid == actions and actions:
        return
    dropped = [a for a in actions if a not in _VALID_RADIAL_ACTIONS]
    if not valid:
        logger.warning(
            "config field 'radial_actions' had no valid entries (dropped %r) -- "
            "falling back to the default wedge set",
            dropped,
        )
        settings.radial_actions = list(_DEFAULT_RADIAL_ACTIONS)
    else:
        logger.warning(
            "config field 'radial_actions' had unknown entries %r -- dropping them",
            dropped,
        )
        settings.radial_actions = valid


def _sanitize_gesture_action(settings: Settings, field: str, default: str) -> None:
    """
    fall back to `default` for a pinch_action/grab_action that isn't one of
    "click"/"scroll"/"disabled" -- main.py's dispatch already no-ops
    gracefully on an unknown value, but gui.py's ACTION_LABELS[...] indexes
    the menu checkmarks with it directly and raises KeyError on anything
    else, which crashes the menu bar app at startup or on a profile load
    before you ever get a terminal to see why.
    """
    value = getattr(settings, field)
    if value in _VALID_GESTURE_ACTIONS:
        return
    logger.warning(
        "config field %r was %r, not a valid action -- falling back to %r",
        field, value, default,
    )
    setattr(settings, field, default)


def _sanitize_thumbs_up_action(settings: Settings) -> None:
    """
    fall back to "confirm" for a thumbs_up_action that isn't a real
    shortcuts.NAMED_SHORTCUTS entry. main.py's NAMED_SHORTCUTS.get() and
    gui.py's NAMED_SHORTCUT_LABELS.get() both already fall back gracefully
    on an unknown value, so this can't crash like the old pinch/grab bug --
    but without this, a typo'd value (hand-edited yaml, or a stale name from
    a since-renamed shortcut) silently does nothing forever with no warning
    anywhere, same silent-misbehavior reasoning as _sanitize_radial_actions.
    """
    value = settings.thumbs_up_action
    if value in _NAMED_SHORTCUTS:
        return
    logger.warning(
        "config field 'thumbs_up_action' was %r, not a known shortcut -- falling back to 'confirm'",
        value,
    )
    settings.thumbs_up_action = "confirm"


def _sanitize_preferred_hand(settings: Settings) -> None:
    """
    fall back to "right" for a preferred_hand that isn't "left"/"right"/"first".
    unlike pinch_action/grab_action, an unknown value here doesn't crash
    anything -- pick_hand's `hand.get("type") == preferred_hand` check just
    never matches, so a hand is never picked and tracking silently never
    starts, with no error anywhere to explain why. same silent-misbehavior
    reasoning as _sanitize_thumbs_up_action.
    """
    value = settings.preferred_hand
    if value in _VALID_PREFERRED_HANDS:
        return
    logger.warning(
        "config field 'preferred_hand' was %r, not 'left'/'right'/'first' -- "
        "falling back to 'right'",
        value,
    )
    settings.preferred_hand = "right"


def _sanitize_cursor_mode(settings: Settings) -> None:
    """
    fall back to "absolute" for a cursor_mode that isn't "absolute"/"relative"/
    "tilt". coord_mapper.make_mapper doesn't crash on an unknown value -- it
    just falls through to the absolute CoordMapper, same as an explicit
    "absolute" -- but that's a silent behavior change (e.g. a typo'd
    "relatve" quietly switches you to a mode that needs calibration) with no
    warning anywhere, same class of bug as preferred_hand above. picking
    "absolute" as the fallback matches what make_mapper already does, so this
    only adds the warning, it doesn't change actual runtime behavior.
    """
    value = settings.cursor_mode
    if value in _VALID_CURSOR_MODES:
        return
    logger.warning(
        "config field 'cursor_mode' was %r, not 'absolute'/'relative'/'tilt' -- "
        "falling back to 'absolute'",
        value,
    )
    settings.cursor_mode = "absolute"


def _sanitize_settings(settings: Settings) -> Settings:
    """
    clamp fields that would otherwise silently misbehave instead of raising --
    e.g. a threshold above 1.0 just never triggers, a negative dwell duration
    fires instantly. only touches values that are actually out of range, so a
    well-formed config round-trips unchanged.
    """
    for field in _UNIT_INTERVAL_FIELDS:
        _clamp_field(settings, field, 0.0, 1.0)
    for field in _PERCENT_FIELDS:
        _clamp_field(settings, field, 0, 100)
    for field in _NONNEGATIVE_SECONDS_FIELDS:
        _clamp_field(settings, field, 0.0, float("inf"))
    _sanitize_radial_actions(settings)
    _sanitize_gesture_action(settings, "pinch_action", "click")
    _sanitize_gesture_action(settings, "grab_action", "scroll")
    _sanitize_thumbs_up_action(settings)
    _sanitize_preferred_hand(settings)
    _sanitize_cursor_mode(settings)
    return settings


def _drop_unknown_keys(raw: dict, cls: type, path: Path) -> dict:
    """
    an unrecognized key (typo'd field name, or a field from a newer/older
    orvix version) crashes cls(**raw) with a TypeError before
    _sanitize_settings ever gets a chance to clamp anything -- the same
    "silent bad state" risk cycles 11/17/18/21 fixed for bad *values*, just
    one step earlier, for bad *keys*. drop what doesn't match and warn,
    rather than taking the whole config (and everything that loads it) down.
    """
    known = {f.name for f in dataclasses.fields(cls)}
    unknown = sorted(set(raw) - known)
    if unknown:
        logger.warning(
            "%s has unrecognized %s field(s) %s, ignoring them",
            path, cls.__name__, unknown,
        )
        raw = {k: v for k, v in raw.items() if k in known}
    return raw


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> Settings:
    """
    load settings from yaml, falling back to defaults for anything missing
    (or for everything, if the file doesn't exist yet). values outside a
    sane range (see _sanitize_settings) are clamped with a warning rather
    than left to misbehave downstream. unrecognized keys are dropped with a
    warning for the same reason.
    """
    if not path.exists():
        return Settings()

    with path.open("r") as f:
        raw = yaml.safe_load(f) or {}

    calibration_raw = _drop_unknown_keys(raw.pop("calibration", {}) or {}, CalibrationBox, path)
    calibration = CalibrationBox(**calibration_raw)
    raw = _drop_unknown_keys(raw, Settings, path)
    return _sanitize_settings(Settings(calibration=calibration, **raw))


def save_config(settings: Settings, path: Path = DEFAULT_CONFIG_PATH) -> None:
    """write settings out to yaml, creating the parent dir if needed."""
    path.parent.mkdir(parents=True, exist_ok=True)

    data = dataclasses.asdict(settings)
    with path.open("w") as f:
        yaml.safe_dump(data, f, default_flow_style=False, sort_keys=False)


def list_profiles(profiles_dir: Path = DEFAULT_PROFILES_DIR) -> list[str]:
    """names of saved profiles (no .yaml suffix), alphabetical. [] if none yet."""
    if not profiles_dir.exists():
        return []
    return sorted(p.stem for p in profiles_dir.glob("*.yaml"))


def save_profile(
    name: str, settings: Settings, profiles_dir: Path = DEFAULT_PROFILES_DIR
) -> Path:
    """save `settings` as a named profile, e.g. for a 'demo' or 'precision' setup."""
    _validate_profile_name(name)
    path = profiles_dir / f"{name}.yaml"
    save_config(settings, path)
    return path


def load_profile(name: str, profiles_dir: Path = DEFAULT_PROFILES_DIR) -> Settings:
    """load a previously saved named profile. raises FileNotFoundError if unknown."""
    _validate_profile_name(name)
    path = profiles_dir / f"{name}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"no profile named {name!r} at {path}")
    return load_config(path)


def delete_profile(name: str, profiles_dir: Path = DEFAULT_PROFILES_DIR) -> None:
    """delete a named profile. raises FileNotFoundError if unknown."""
    _validate_profile_name(name)
    path = profiles_dir / f"{name}.yaml"
    path.unlink()
