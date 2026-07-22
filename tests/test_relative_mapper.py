"""
tests for relative (trackpad-style) cursor mapping. the things worth
pinning down here are the ones that would be really annoying in practice:
the cursor jumping when your hand reappears, and the cursor wandering off
on its own while your hand sits still.
"""

import random

from orvix.config import Settings
from orvix.coord_mapper import CoordMapper, RelativeCoordMapper, make_mapper

FPS = 75.0
DT = 1.0 / FPS


def drive(mapper, points, t0=0.0):
    """feed a series of (x, y) mm positions one frame apart, return cursor positions."""
    out = []
    t = t0
    for x, y in points:
        t += DT
        out.append(mapper.map_to_screen((x, y, 0.0), t))
    return out


def test_make_mapper_honours_cursor_mode():
    assert isinstance(make_mapper(Settings(cursor_mode="relative"), 1920, 1080), RelativeCoordMapper)
    assert isinstance(make_mapper(Settings(cursor_mode="absolute"), 1920, 1080), CoordMapper)


def test_starts_centred_and_first_frame_doesnt_move():
    m = RelativeCoordMapper(1920, 1080, Settings())
    (x, y) = drive(m, [(0.0, 200.0)])[0]
    assert (x, y) == (960, 540)


def test_moving_right_moves_cursor_right():
    m = RelativeCoordMapper(1920, 1080, Settings())
    pts = [(float(i), 200.0) for i in range(40)]
    out = drive(m, pts)
    assert out[-1][0] > out[0][0]


def test_leap_y_up_maps_to_screen_y_up():
    # leap y increases upward, screen y increases downward, so raising your
    # hand has to make the pixel y smaller or the whole thing is inverted
    m = RelativeCoordMapper(1920, 1080, Settings())
    pts = [(0.0, 200.0 + i) for i in range(40)]
    out = drive(m, pts)
    assert out[-1][1] < out[0][1]


def test_faster_movement_covers_more_screen_per_mm():
    """the whole point of the accel curve: same distance, more px when fast."""
    settings = Settings()

    slow = RelativeCoordMapper(1920, 1080, settings)
    # 30mm covered slowly (0.5mm per frame)
    slow_out = drive(slow, [(i * 0.5, 200.0) for i in range(61)])
    slow_px = slow_out[-1][0] - slow_out[0][0]

    fast = RelativeCoordMapper(1920, 1080, settings)
    # same 30mm covered quickly (5mm per frame)
    fast_out = drive(fast, [(i * 5.0, 200.0) for i in range(7)])
    fast_px = fast_out[-1][0] - fast_out[0][0]

    assert fast_px > slow_px


def test_cursor_stays_on_screen():
    m = RelativeCoordMapper(1920, 1080, Settings())
    out = drive(m, [(i * 20.0, 200.0 - i * 10.0) for i in range(200)])
    for x, y in out:
        assert 0 <= x <= 1920
        assert 0 <= y <= 1080


def test_hand_reappearing_elsewhere_doesnt_jump_the_cursor():
    """
    the bug this guards: hand leaves at x=0, comes back at x=200mm. without
    reset() that 200mm becomes one enormous delta and the cursor teleports.
    """
    m = RelativeCoordMapper(1920, 1080, Settings())
    drive(m, [(float(i), 200.0) for i in range(20)])
    before = m.map_to_screen((19.0, 200.0, 0.0), 100.0)

    m.reset()  # main.py does this on HAND_LOST

    after = m.map_to_screen((500.0, 400.0, 0.0), 101.0)
    assert after == before


def test_cursor_doesnt_drift_while_hand_is_held_still():
    """
    relative mode differences the position, so sensor noise would push the
    cursor around on its own if we differenced unfiltered values. the hand
    is stationary here, only noise moves.
    """
    random.seed(11)
    m = RelativeCoordMapper(1920, 1080, Settings())
    pts = [(random.gauss(0, 1.0), 200.0 + random.gauss(0, 1.0)) for _ in range(400)]
    out = drive(m, pts)

    start = out[10]
    drift = max(
        max(abs(x - start[0]) for x, _ in out[10:]),
        max(abs(y - start[1]) for _, y in out[10:]),
    )
    # a few px of wander is fine, a cursor that walks across the screen isn't
    assert drift < 40, f"cursor drifted {drift}px with a still hand"


def test_cursor_doesnt_drift_over_a_long_session():
    """
    the short version above only runs ~5s of frames, too brief to catch a
    slow compounding bias (e.g. from asymmetric rounding or a filter that
    doesn't actually converge to zero-mean). this drives 20x as many frames
    (~5.3 minutes at 75fps) with the same zero-mean noise: if differencing
    the filtered signal introduced any systematic per-frame bias, drift here
    would grow roughly linearly with frame count and blow well past the
    short test's bound. it doesn't, which pins down that relative mode's
    filter-then-difference approach has no built-in session-length drift of
    its own -- any real-world drift has to come from actual Leap sensor bias,
    not from this math.
    """
    random.seed(11)
    m = RelativeCoordMapper(1920, 1080, Settings())
    pts = [(random.gauss(0, 1.0), 200.0 + random.gauss(0, 1.0)) for _ in range(8000)]
    out = drive(m, pts)

    start = out[10]
    drift = max(
        max(abs(x - start[0]) for x, _ in out[10:]),
        max(abs(y - start[1]) for _, y in out[10:]),
    )
    assert drift < 40, f"cursor drifted {drift}px over a long still-handed session"


# -- multi-monitor: a screen_origin that isn't (0, 0), e.g. a desktop bounding
# box for two side-by-side displays where the second one sits to the right --


def test_starts_centred_on_the_offset_desktop_not_at_global_zero():
    # a 3840x1080 bounding box starting at x=0 (main display) is the simple
    # case already covered above; here the *box itself* is offset, as if the
    # union of displays started at x=500 for some arrangement
    m = RelativeCoordMapper(1920, 1080, Settings(), screen_origin=(500.0, 0.0))
    (x, y) = drive(m, [(0.0, 200.0)])[0]
    assert (x, y) == (500 + 960, 540)


def test_cursor_stays_within_offset_bounds():
    m = RelativeCoordMapper(1920, 1080, Settings(), screen_origin=(-1920.0, 0.0))
    out = drive(m, [(i * 20.0, 200.0 - i * 10.0) for i in range(200)])
    for x, y in out:
        assert -1920 <= x <= 0
        assert 0 <= y <= 1080


def test_make_mapper_threads_screen_origin_through():
    m = make_mapper(Settings(cursor_mode="relative"), 1920, 1080, screen_origin=(500.0, 0.0))
    assert isinstance(m, RelativeCoordMapper)
    (x, y) = drive(m, [(0.0, 200.0)])[0]
    assert (x, y) == (500 + 960, 540)
