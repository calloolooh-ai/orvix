"""
displays.py

works out how big "the screen" is when it might actually be several of them.
Quartz's global coordinate space already spans every active display (main
display's top-left is (0, 0), everything else is placed relative to that,
including negative coordinates for a display positioned above/left of main),
so CGEventPost-ing a mouse move to (3000, 400) already lands correctly on a
second monitor with no extra work.

what's missing is knowing the *bounds* of that combined space, so the coord
mappers can clamp to it and relative/tilt mode can start centred in it,
instead of assuming a single screen starting at (0, 0).

pure logic module except for the one Quartz call to list displays, kept
narrow and mockable so coord_mapper's tests don't need real hardware.
"""

from __future__ import annotations

import dataclasses

import Quartz

# CGGetActiveDisplayList wants a cap on how many displays to return. no real
# Mac setup gets anywhere near this, it's just a sane upper bound for the
# fixed-size buffer Quartz wants.
_MAX_DISPLAYS = 16


@dataclasses.dataclass(frozen=True)
class DesktopBounds:
    """
    a pixel rectangle in Quartz's global coordinate space: origin top-left of
    the main display, y growing downward, x/y allowed to be negative for
    displays placed left of or above main.
    """

    origin_x: float
    origin_y: float
    width: float
    height: float

    @property
    def max_x(self) -> float:
        return self.origin_x + self.width

    @property
    def max_y(self) -> float:
        return self.origin_y + self.height

    @property
    def center(self) -> tuple[float, float]:
        return (self.origin_x + self.width / 2.0, self.origin_y + self.height / 2.0)


def _active_display_ids() -> list[int]:
    """
    every display macOS currently considers active: on, not mirrored-off,
    includes displays that are asleep but still enumerated. wrapped so tests
    can monkeypatch just this and not the CGDisplayBounds calls below.
    """
    err, ids, _count = Quartz.CGGetActiveDisplayList(_MAX_DISPLAYS, None, None)
    if err != 0 or not ids:
        # extremely unlikely (means Quartz itself is unhappy), but fall back
        # to "just the main display" rather than raising out of the render
        # loop over a display enumeration hiccup
        return [Quartz.CGMainDisplayID()]
    return list(ids)


def get_desktop_bounds(multi_monitor: bool = True) -> DesktopBounds:
    """
    the pixel rectangle the cursor is allowed to roam over.

    multi_monitor=True: the union of every active display's bounds, i.e. the
    smallest rectangle containing all of them. gaps are possible (an L-shaped
    arrangement produces a bounding box bigger than the sum of the screens)
    but that matches how a physical mouse already behaves crossing to a
    monitor that isn't top-aligned with the others: it can graze empty space
    for a pixel row rather than snapping.

    multi_monitor=False: just the main display, origin always (0, 0) since
    that's Quartz's coordinate origin by definition.
    """
    if not multi_monitor:
        display_id = Quartz.CGMainDisplayID()
        rect = Quartz.CGDisplayBounds(display_id)
        return DesktopBounds(0.0, 0.0, rect.size.width, rect.size.height)

    ids = _active_display_ids()
    rects = [Quartz.CGDisplayBounds(display_id) for display_id in ids]

    min_x = min(r.origin.x for r in rects)
    min_y = min(r.origin.y for r in rects)
    max_x = max(r.origin.x + r.size.width for r in rects)
    max_y = max(r.origin.y + r.size.height for r in rects)

    return DesktopBounds(min_x, min_y, max_x - min_x, max_y - min_y)
