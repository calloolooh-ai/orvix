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

# where the user's personal config lives. gitignored on purpose, since it'll
# have your specific hand-range calibration in it, not something to commit.
DEFAULT_CONFIG_PATH = Path.home() / ".orvix" / "config.yaml"


@dataclasses.dataclass
class CalibrationBox:
    """
    the Leap-space (millimeter) box that maps to your full screen.

    these are rough factory defaults, a comfortable hand range above the
    sensor for someone sitting at a desk. run `python -m orvix.calibration`
    to replace these with numbers measured from your actual hand/desk setup,
    it'll make tracking feel a lot less janky.
    """

    x_min: float = -120.0
    x_max: float = 120.0
    # leap's y axis is height above the device, not screen-style top-to-bottom,
    # so a bigger y means your hand is higher up
    y_min: float = 100.0
    y_max: float = 300.0
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

    # pinchStrength above this counts as a pinch-click. leapd reports this
    # as a 0-1 float, already computed for us, no need to derive it from
    # fingertip distances ourselves
    pinch_threshold: float = 0.75
    # once pinched, strength has to drop below this to count as released.
    # keeping this lower than pinch_threshold gives us hysteresis so a pinch
    # sitting right at the boundary doesn't rapid-fire click/unclick
    pinch_release_threshold: float = 0.5

    grab_threshold: float = 0.85
    grab_release_threshold: float = 0.6

    # what the pinch and grab gestures actually do to the mouse. one of
    # "click" (down/drag/up -> mouse_down/drag_to/mouse_up) or "scroll"
    # (drag/scroll frames drive the scroll wheel via palm velocity) or
    # "disabled" (gesture is tracked but produces no mouse action).
    # defaults match orvix's original v1 behavior: pinch clicks/drags,
    # grab scrolls. swappable so e.g. someone who scrolls a lot more than
    # they drag can put scroll on the easier-to-hold pinch instead.
    pinch_action: str = "click"
    grab_action: str = "scroll"

    # One Euro Filter params, see coord_mapper.py for what these actually do.
    # these are the values from the original paper's pointer-tracking example,
    # decent starting point, hand-tune once you're moving a real cursor around
    one_euro_min_cutoff: float = 1.0
    one_euro_beta: float = 0.007

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
