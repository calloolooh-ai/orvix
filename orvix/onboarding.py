"""
onboarding.py

first-run detection and the welcome copy shown the first time orvix's menu
bar app launches with no config on disk yet.

before this, a first-time user got dropped straight into the menu bar with
default (guessed) calibration and had to independently discover "Calibrate..."
in the menu to get a cursor that actually fits their reach. this module just
answers "is this a first run" and holds the message; gui.py decides what to
do with that (show an alert, offer to jump straight into calibration).

pure logic, no rumps/AppKit here, so "is this a first run" is testable
without a running menu bar app.
"""

from __future__ import annotations

from pathlib import Path

WELCOME_MESSAGE = (
    "orvix moves your mouse with a Leap Motion Controller instead of a "
    "trackpad or mouse.\n\n"
    "before it feels right you need to calibrate: it watches you sweep your "
    "hand around for a few seconds so it learns your actual reach instead of "
    "guessing.\n\n"
    "you can calibrate now, or skip it and use rough defaults for now -- "
    "\"Calibrate...\" is always in this menu whenever you're ready."
)

CALIBRATE_NOW_LABEL = "calibrate now"
SKIP_FOR_NOW_LABEL = "skip for now"


def is_first_run(config_path: Path, profiles_dir: Path | None = None) -> bool:
    """
    true the first time orvix runs on this machine: no ~/.orvix/config.yaml
    yet, meaning calibration has never been saved (config.py falls back to
    guessed defaults when the file is missing, see load_config).

    also checks profiles_dir if given: "Save current as..." writes a named
    profile without ever touching config.yaml (see gui.py's
    _save_profile_as), so a user who saves a profile on their very first
    launch without switching to one would otherwise get nagged with the
    welcome alert again on every later launch, config.yaml still missing
    the whole time. any saved profile is just as much evidence of a prior
    run as config.yaml is.

    not a "have you calibrated recently" heuristic beyond that: once
    you've saved any config or profile at all, even by skipping the
    wizard, this goes false for good. re-onboarding an existing user who
    deliberately skipped would be more annoying than helpful.
    """
    if config_path.exists():
        return False
    if profiles_dir is not None:
        try:
            if any(profiles_dir.iterdir()):
                return False
        except FileNotFoundError:
            pass
    return True
