"""
shortcuts.py

the keystrokes the radial menu (and a couple of other gestures) fire, as
plain data: macOS virtual key codes plus modifier names. no Quartz import
here on purpose so the table stays testable and mouse_control remains the
one place that talks to CoreGraphics; it translates the modifier names into
CGEventFlags when it posts the key.

virtual key codes are the standard US-layout ANSI ones from
<HIToolbox/Events.h>. modifier names are "cmd", "shift", "ctrl", "alt".
"""

from __future__ import annotations

import dataclasses

# a handful of the HIToolbox kVK_* codes, named so the table below reads
KEY_RETURN = 36
KEY_TAB = 48
KEY_ESCAPE = 53
KEY_F = 3
KEY_C = 8
KEY_V = 9
KEY_Z = 6
KEY_3 = 20
KEY_UP = 126
KEY_SPACE = 49

MODIFIERS = ("cmd", "shift", "ctrl", "alt")


@dataclasses.dataclass(frozen=True)
class Shortcut:
    keycode: int
    mods: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        bad = [m for m in self.mods if m not in MODIFIERS]
        if bad:
            raise ValueError(f"unknown modifier(s): {bad}; expected any of {MODIFIERS}")


# radial-menu wedge action id -> keystroke. "close" is intentionally absent:
# it dismisses the wheel and posts nothing.
RADIAL_SHORTCUTS: dict[str, Shortcut] = {
    "mission_control": Shortcut(KEY_UP, ("ctrl",)),
    "maximize": Shortcut(KEY_F, ("ctrl", "cmd")),
    "app_switcher": Shortcut(KEY_TAB, ("cmd",)),
    "undo": Shortcut(KEY_Z, ("cmd",)),
    "copy": Shortcut(KEY_C, ("cmd",)),
    "paste": Shortcut(KEY_V, ("cmd",)),
    # cmd+shift+3 is a full-screen capture. deliberately not cmd+shift+4,
    # which drops you into area-selection mode and waits for a drag -- no
    # good for a one-shot gesture-fired shortcut.
    "screenshot": Shortcut(KEY_3, ("cmd", "shift")),
    # not in DEFAULT_RADIAL_ACTIONS -- available as an opt-in wedge or
    # thumbs_up_action without changing the default wheel's layout for
    # existing users.
    "spotlight": Shortcut(KEY_SPACE, ("cmd",)),
}

# the default clockwise-from-top wedge layout, matching the overlay mock.
DEFAULT_RADIAL_ACTIONS: list[str] = [
    "mission_control",
    "maximize",
    "app_switcher",
    "undo",
    "copy",
    "paste",
    "screenshot",
    "close",
]

# thumbs-up (gesture 13) confirms by default; it's just Return with no
# modifiers. kept as its own name (not folded only into NAMED_SHORTCUTS)
# since it's still the hardcoded fallback if a saved thumbs_up_action name
# doesn't resolve (see main.py's _execute_extras).
CONFIRM_SHORTCUT = Shortcut(KEY_RETURN)

# every shortcut orvix knows how to fire, by name: the radial wedges plus
# "confirm". this is the single namespace gesture bindings choose from --
# right now that's just settings.thumbs_up_action, but it exists as one
# shared table (rather than a separate one per bindable gesture) so a new
# bindable gesture in the future has somewhere to point without inventing
# its own list of options.
NAMED_SHORTCUTS: dict[str, Shortcut] = {**RADIAL_SHORTCUTS, "confirm": CONFIRM_SHORTCUT}

# human labels for NAMED_SHORTCUTS, for menus. "confirm" reads as "Return"
# since that's what it actually presses, not what any gesture calls it.
NAMED_SHORTCUT_LABELS: dict[str, str] = {
    "mission_control": "Mission Control",
    "maximize": "Maximize",
    "app_switcher": "App Switcher",
    "undo": "Undo",
    "copy": "Copy",
    "paste": "Paste",
    "screenshot": "Screenshot",
    "spotlight": "Spotlight Search",
    "confirm": "Return",
}
