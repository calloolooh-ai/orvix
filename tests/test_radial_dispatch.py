"""
tests for the radial menu's integration seam: the shortcut table, the
keyboard layer on the dry-run controller, and main._fire_radial turning a
fired wedge into a keystroke.
"""

from orvix.config import Settings
from orvix.main import _fire_radial
from orvix.radial_menu import RadialMenu
from orvix.shortcuts import DEFAULT_RADIAL_ACTIONS, RADIAL_SHORTCUTS


def test_every_default_wedge_except_close_has_a_shortcut():
    for action in DEFAULT_RADIAL_ACTIONS:
        if action == "close":
            assert action not in RADIAL_SHORTCUTS
        else:
            assert action in RADIAL_SHORTCUTS, f"{action} has no keystroke"


class FakeMouse:
    def __init__(self):
        self.keys: list[tuple[int, tuple[str, ...]]] = []

    def key_shortcut(self, keycode, mods=()):
        self.keys.append((keycode, mods))


class IdentityMapper:
    """palm x,y straight through as screen px, so tests can place the pointer."""

    def __init__(self):
        self.resets = 0

    def map_to_screen(self, palm, now):
        return (int(palm[0]), int(palm[1]))

    def reset(self):
        self.resets += 1


def hand_at(x, y, pinch=0.0):
    # palm y is the screen-vertical here because IdentityMapper uses x,y
    return {"palmPosition": [x, y, 0.0], "pinchStrength": pinch}


def test_fire_radial_posts_the_wedge_keystroke_on_pinch():
    settings = Settings()
    mouse = FakeMouse()
    mapper = IdentityMapper()
    menu = RadialMenu(settings.radial_actions, dead_zone_px=55.0, dwell_seconds=0.6)

    center = (500.0, 500.0)
    menu.open(center, now=0.0)

    # wedge 4 ("copy") sits straight down from center; point there and pinch
    below = (500, 500 + 120)
    _fire_radial(menu, hand_at(*below, pinch=0.0), mapper, mouse, settings, now=0.0)
    _fire_radial(
        menu, hand_at(*below, pinch=settings.pinch_threshold + 0.1), mapper, mouse, settings, now=0.05
    )

    assert RADIAL_SHORTCUTS["copy"].keycode in [k for k, _ in mouse.keys]
    assert not menu.is_open
    assert mapper.resets >= 1  # mapper re-synced on close


def test_fire_radial_cancels_and_resets_when_hand_lost():
    settings = Settings()
    mouse = FakeMouse()
    mapper = IdentityMapper()
    menu = RadialMenu(settings.radial_actions)
    menu.open((10.0, 10.0), now=0.0)

    _fire_radial(menu, None, mapper, mouse, settings, now=0.0)
    assert not menu.is_open
    assert mouse.keys == []
    assert mapper.resets == 1


def test_fire_radial_dwell_fires_without_a_pinch():
    settings = Settings()
    mouse = FakeMouse()
    mapper = IdentityMapper()
    menu = RadialMenu(settings.radial_actions, dwell_seconds=0.6)
    menu.open((500.0, 500.0), now=0.0)

    up = (500, 500 - 120)  # wedge 0 = "mission_control"
    _fire_radial(menu, hand_at(*up), mapper, mouse, settings, now=0.0)
    _fire_radial(menu, hand_at(*up), mapper, mouse, settings, now=0.3)
    _fire_radial(menu, hand_at(*up), mapper, mouse, settings, now=0.7)

    assert RADIAL_SHORTCUTS["mission_control"].keycode in [k for k, _ in mouse.keys]
