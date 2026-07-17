"""
tests for the three things borrowed from other projects: tilt (joystick)
cursor mode, the near-sensor dead zone, and right click via a middle finger
pinch. see the comments in config.py for why each exists.
"""

from orvix.config import Settings
from orvix.coord_mapper import TiltCoordMapper, make_mapper
from orvix.gesture_interpreter import GestureInterpreter, GestureType
from orvix.leap_client import fingertips_for_hand

FPS = 75.0
DT = 1.0 / FPS


def tilt_for(mapper, normal, frames=40, t0=0.0):
    t = t0
    out = None
    for _ in range(frames):
        t += DT
        out = mapper.map_to_screen_tilt(normal, t)
    return out


# -- tilt mode --


def test_make_mapper_builds_tilt_mode():
    assert isinstance(make_mapper(Settings(cursor_mode="tilt"), 1920, 1080), TiltCoordMapper)


def test_flat_hand_doesnt_move_the_cursor():
    m = TiltCoordMapper(1920, 1080, Settings())
    # palm down and flat: normal points straight down, no tilt on x or z
    assert tilt_for(m, (0.0, -1.0, 0.0)) == (960, 540)


def test_small_tilt_inside_deadzone_is_ignored():
    s = Settings()
    m = TiltCoordMapper(1920, 1080, s)
    assert tilt_for(m, (s.tilt_deadzone * 0.5, -1.0, 0.0)) == (960, 540)


def test_tilting_right_moves_the_cursor_right():
    m = TiltCoordMapper(1920, 1080, Settings())
    x, _ = tilt_for(m, (0.5, -0.8, 0.0))
    assert x > 960


def test_tilting_left_moves_the_cursor_left():
    m = TiltCoordMapper(1920, 1080, Settings())
    x, _ = tilt_for(m, (-0.5, -0.8, 0.0))
    assert x < 960


def test_more_tilt_moves_faster():
    a = TiltCoordMapper(1920, 1080, Settings())
    b = TiltCoordMapper(1920, 1080, Settings())
    small, _ = tilt_for(a, (0.25, -0.9, 0.0))
    big, _ = tilt_for(b, (0.6, -0.7, 0.0))
    assert big - 960 > small - 960


def test_cursor_stays_on_screen_when_held_at_full_tilt():
    m = TiltCoordMapper(1920, 1080, Settings())
    x, y = tilt_for(m, (1.0, 0.0, 1.0), frames=2000)
    assert 0 <= x <= 1920
    assert 0 <= y <= 1080


def test_a_stall_cant_lurch_the_cursor_across_the_screen():
    """
    dt is capped. after a CGEventPost stall (see stream_latest_frames) we
    can be handed a huge gap, and integrating it raw would fling the cursor.
    """
    m = TiltCoordMapper(1920, 1080, Settings())
    m.map_to_screen_tilt((0.6, -0.7, 0.0), 0.0)
    x, _ = m.map_to_screen_tilt((0.6, -0.7, 0.0), 5.0)  # 5 second jump
    assert x - 960 < 200


# -- dead zone --


def make_hand(x=0.0, y=200.0, z=0.0, pinch=0.0, grab=0.0):
    return {
        "id": 1,
        "palmPosition": [x, y, z],
        "palmVelocity": [0.0, 0.0, 0.0],
        "palmNormal": [0.0, -1.0, 0.0],
        "pinchStrength": pinch,
        "grabStrength": grab,
    }


def test_hand_too_close_to_the_sensor_is_treated_as_no_hand():
    s = Settings()
    interp = GestureInterpreter(s)
    events = interp.process_hand(make_hand(y=s.min_hand_height_mm - 10))
    assert [e.type for e in events] == [GestureType.HAND_LOST]


def test_hand_above_the_dead_zone_tracks_normally():
    s = Settings()
    interp = GestureInterpreter(s)
    events = interp.process_hand(make_hand(y=s.min_hand_height_mm + 10))
    assert events[0].type == GestureType.POINT_MOVE


def test_dead_zone_can_be_disabled():
    interp = GestureInterpreter(Settings(min_hand_height_mm=0.0))
    events = interp.process_hand(make_hand(y=5.0))
    assert events[0].type == GestureType.POINT_MOVE


def test_dropping_into_the_dead_zone_releases_a_held_pinch():
    """otherwise the button stays stuck down forever."""
    interp = GestureInterpreter(Settings())
    interp.process_hand(make_hand(pinch=0.9))  # PINCH_DOWN
    events = interp.process_hand(make_hand(y=5.0, pinch=0.9))
    assert GestureType.PINCH_UP in [e.type for e in events]


# -- right click --


def tips(thumb, index, middle):
    return {0: thumb, 1: index, 2: middle}


def test_middle_finger_pinch_is_a_right_click():
    interp = GestureInterpreter(Settings())
    # thumb sitting on the middle finger, index further off
    events = interp.process_hand(
        make_hand(pinch=0.9),
        tips(thumb=(0.0, 200.0, 0.0), index=(40.0, 200.0, 0.0), middle=(2.0, 200.0, 0.0)),
    )
    assert events[0].type == GestureType.RIGHT_CLICK


def test_index_pinch_is_still_a_left_click():
    interp = GestureInterpreter(Settings())
    events = interp.process_hand(
        make_hand(pinch=0.9),
        tips(thumb=(0.0, 200.0, 0.0), index=(2.0, 200.0, 0.0), middle=(40.0, 200.0, 0.0)),
    )
    assert events[0].type == GestureType.PINCH_DOWN


def test_right_click_fires_once_per_pinch_not_every_frame():
    interp = GestureInterpreter(Settings())
    t = tips(thumb=(0.0, 200.0, 0.0), index=(40.0, 200.0, 0.0), middle=(2.0, 200.0, 0.0))

    first = interp.process_hand(make_hand(pinch=0.9), t)
    assert first[0].type == GestureType.RIGHT_CLICK

    # still pinching, must not repeat
    for _ in range(5):
        assert interp.process_hand(make_hand(pinch=0.9), t) == []

    # release, then pinch again -> a second click
    interp.process_hand(make_hand(pinch=0.1), t)
    again = interp.process_hand(make_hand(pinch=0.9), t)
    assert again[0].type == GestureType.RIGHT_CLICK


def test_without_finger_data_everything_stays_a_left_click():
    """pointables may be absent depending on what leapd negotiated."""
    interp = GestureInterpreter(Settings())
    events = interp.process_hand(make_hand(pinch=0.9), {})
    assert events[0].type == GestureType.PINCH_DOWN


def test_right_click_can_be_disabled():
    interp = GestureInterpreter(Settings(right_click_on_middle_finger_pinch=False))
    events = interp.process_hand(
        make_hand(pinch=0.9),
        tips(thumb=(0.0, 200.0, 0.0), index=(40.0, 200.0, 0.0), middle=(2.0, 200.0, 0.0)),
    )
    assert events[0].type == GestureType.PINCH_DOWN


# -- fingertip extraction --


def test_fingertips_are_matched_to_the_right_hand():
    frame = {
        "pointables": [
            {"handId": 1, "type": 0, "tipPosition": [1.0, 2.0, 3.0]},
            {"handId": 1, "type": 1, "tipPosition": [4.0, 5.0, 6.0]},
            {"handId": 99, "type": 0, "tipPosition": [7.0, 8.0, 9.0]},  # other hand
        ]
    }
    out = fingertips_for_hand(frame, {"id": 1})
    assert out == {0: (1.0, 2.0, 3.0), 1: (4.0, 5.0, 6.0)}


def test_fingertips_copes_with_a_frame_that_has_no_pointables():
    assert fingertips_for_hand({}, {"id": 1}) == {}
