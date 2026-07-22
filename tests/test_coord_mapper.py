"""
tests for coord_mapper.py: the calibration box -> screen pixel mapping math,
and basic smoothing/clamping behavior. no hardware needed.
"""

from orvix.config import CalibrationBox, Settings
from orvix.coord_mapper import CoordMapper


def make_mapper(screen_width=1000, screen_height=500):
    calibration = CalibrationBox(x_min=-100, x_max=100, y_min=100, y_max=300, z_min=-50, z_max=50)
    settings = Settings()
    return CoordMapper(calibration, screen_width, screen_height, settings)


def test_center_of_calibration_box_maps_to_center_of_screen():
    mapper = make_mapper()
    # first call establishes the filter's baseline, so it returns the raw
    # mapped value with no smoothing applied yet
    x, y = mapper.map_to_screen((0, 200, 0), timestamp=0.0)
    assert x == 500
    assert y == 250


def test_leap_y_axis_is_inverted_for_screen_y():
    mapper = make_mapper()
    # top of the calibration box (max leap y, hand held high) should map
    # near the top of the screen, i.e. small pixel y
    x, y = mapper.map_to_screen((0, 300, 0), timestamp=0.0)
    assert y == 0

    mapper2 = make_mapper()
    x, y = mapper2.map_to_screen((0, 100, 0), timestamp=0.0)
    assert y == 500


def test_position_outside_calibration_box_clamps_to_screen_edge():
    mapper = make_mapper()
    x, y = mapper.map_to_screen((-500, 200, 0), timestamp=0.0)
    assert x == 0  # clamped, not negative or off-screen

    mapper2 = make_mapper()
    x, y = mapper2.map_to_screen((500, 200, 0), timestamp=0.0)
    assert x == 1000  # clamped to screen width, not beyond it


def test_repeated_calls_stay_smoothed_and_bounded():
    mapper = make_mapper()
    mapper.map_to_screen((0, 200, 0), timestamp=0.0)
    # small jump a few ms later, filtered output shouldn't overshoot past
    # the raw target or go negative
    x, y = mapper.map_to_screen((10, 205, 0), timestamp=0.01)
    assert 0 <= x <= 1000
    assert 0 <= y <= 500


# -- multi-monitor: a non-zero screen_origin, as a second display placed
# left of main would produce (negative x) or above main (negative y) --


def make_offset_mapper(screen_width=1000, screen_height=500, screen_origin=(-1000.0, 0.0)):
    calibration = CalibrationBox(x_min=-100, x_max=100, y_min=100, y_max=300, z_min=-50, z_max=50)
    settings = Settings()
    return CoordMapper(calibration, screen_width, screen_height, settings, screen_origin=screen_origin)


def test_origin_shifts_center_of_calibration_box_into_second_display():
    # a display bounding box that starts at x=-1000 (a monitor to the left of
    # main): the centre of the calibration box should land at the centre of
    # *that* box, i.e. -1000 + 500 = -500, not back at global x=500
    mapper = make_offset_mapper()
    x, y = mapper.map_to_screen((0, 200, 0), timestamp=0.0)
    assert x == -500
    assert y == 250


def test_origin_shifts_clamped_edges_too():
    mapper = make_offset_mapper()
    x, _y = mapper.map_to_screen((-500, 200, 0), timestamp=0.0)
    assert x == -1000  # clamped to the offset screen's left edge, not global 0

    mapper2 = make_offset_mapper()
    x, _y = mapper2.map_to_screen((500, 200, 0), timestamp=0.0)
    assert x == 0  # clamped to the offset screen's right edge (-1000 + 1000)


def test_default_origin_is_zero_zero_backward_compatible():
    # no screen_origin passed at all: behavior must match pre-multi-monitor
    # code exactly, since single-display setups shouldn't see any change
    mapper = make_mapper()
    x, y = mapper.map_to_screen((0, 200, 0), timestamp=0.0)
    assert (x, y) == (500, 250)


def test_update_screen_bounds_changes_the_scale_and_clamp():
    mapper = make_mapper(screen_width=1000, screen_height=500)
    mapper.update_screen_bounds(2000, 500, (0.0, 0.0))
    # calibration box center now maps to the center of the new, wider screen
    x, y = mapper.map_to_screen((0, 200, 0), timestamp=0.0)
    assert (x, y) == (1000, 250)
