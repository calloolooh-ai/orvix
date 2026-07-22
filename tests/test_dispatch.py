"""
tests for main._dispatch, specifically that settings.pinch_action /
settings.grab_action actually change which mouse_control calls a gesture
event triggers. everything else in main.py needs a live leapd connection
or the real Quartz APIs, so it's out of scope for unit tests.
"""

from orvix.config import CalibrationBox, Settings
from orvix.coord_mapper import CoordMapper
from orvix.gesture_interpreter import GestureEvent, GestureType
from orvix.main import _dispatch


class FakeMouse:
    def __init__(self):
        self.calls: list[tuple] = []

    def move(self, x, y):
        self.calls.append(("move", x, y))

    def mouse_down(self):
        self.calls.append(("mouse_down",))

    def mouse_up(self):
        self.calls.append(("mouse_up",))

    def drag_to(self, x, y):
        self.calls.append(("drag_to", x, y))

    def scroll(self, dx, dy):
        self.calls.append(("scroll", dx, dy))

    def right_click(self):
        self.calls.append(("right_click",))


def make_mapper():
    return CoordMapper(CalibrationBox(), 1920, 1080, Settings())


def test_pinch_click_action_is_default():
    settings = Settings()
    mouse = FakeMouse()
    mapper = make_mapper()

    _dispatch(GestureEvent(GestureType.PINCH_DOWN, (0.0, 200.0, 0.0)), mapper, mouse, settings)
    _dispatch(GestureEvent(GestureType.PINCH_DRAG, (0.0, 200.0, 0.0)), mapper, mouse, settings)
    _dispatch(GestureEvent(GestureType.PINCH_UP, (0.0, 200.0, 0.0)), mapper, mouse, settings)

    # no "move" before mouse_down on purpose: the interpreter froze the
    # cursor while the fingers were closing, and moving to the (drifted)
    # palm position now would throw that away. see pinch_freeze_threshold.
    kinds = [call[0] for call in mouse.calls]
    assert kinds == ["mouse_down", "drag_to", "mouse_up"]


def test_pinch_remapped_to_scroll_uses_velocity_instead_of_clicking():
    settings = Settings(pinch_action="scroll")
    mouse = FakeMouse()
    mapper = make_mapper()

    _dispatch(GestureEvent(GestureType.PINCH_DOWN, (0.0, 200.0, 0.0)), mapper, mouse, settings)
    _dispatch(
        GestureEvent(GestureType.PINCH_DRAG, (0.0, 200.0, 0.0), palm_velocity=(0.0, 100.0, 0.0)),
        mapper,
        mouse,
        settings,
    )
    _dispatch(GestureEvent(GestureType.PINCH_UP, (0.0, 200.0, 0.0)), mapper, mouse, settings)

    assert mouse.calls == [("scroll", 0, 5)]


def test_grab_disabled_produces_no_mouse_calls():
    settings = Settings(grab_action="disabled")
    mouse = FakeMouse()
    mapper = make_mapper()

    _dispatch(GestureEvent(GestureType.GRAB_START, (0.0, 200.0, 0.0)), mapper, mouse, settings)
    _dispatch(
        GestureEvent(GestureType.GRAB_SCROLL, (0.0, 200.0, 0.0), palm_velocity=(0.0, 100.0, 0.0)),
        mapper,
        mouse,
        settings,
    )
    _dispatch(GestureEvent(GestureType.GRAB_END, (0.0, 200.0, 0.0)), mapper, mouse, settings)

    assert mouse.calls == []


def test_grab_remapped_to_click_uses_start_and_end_as_down_and_up():
    settings = Settings(grab_action="click")
    mouse = FakeMouse()
    mapper = make_mapper()

    _dispatch(GestureEvent(GestureType.GRAB_START, (0.0, 200.0, 0.0)), mapper, mouse, settings)
    _dispatch(GestureEvent(GestureType.GRAB_SCROLL, (0.0, 200.0, 0.0)), mapper, mouse, settings)
    _dispatch(GestureEvent(GestureType.GRAB_END, (0.0, 200.0, 0.0)), mapper, mouse, settings)

    kinds = [call[0] for call in mouse.calls]
    assert kinds == ["mouse_down", "drag_to", "mouse_up"]


def test_hand_lost_and_missing_position_are_ignored():
    settings = Settings()
    mouse = FakeMouse()
    mapper = make_mapper()

    _dispatch(GestureEvent(GestureType.HAND_LOST), mapper, mouse, settings)
    _dispatch(GestureEvent(GestureType.PINCH_UP, palm_position=None), mapper, mouse, settings)

    assert mouse.calls == []


def test_dispatch_reports_true_only_when_a_click_actually_lands():
    """
    used by run_live to flash the cursor ring: only the moment a click
    lands should count, not the down/drag steps or non-click actions.
    """
    settings = Settings()
    mouse = FakeMouse()
    mapper = make_mapper()

    assert _dispatch(
        GestureEvent(GestureType.PINCH_DOWN, (0.0, 200.0, 0.0)), mapper, mouse, settings
    ) is False
    assert _dispatch(
        GestureEvent(GestureType.PINCH_DRAG, (0.0, 200.0, 0.0)), mapper, mouse, settings
    ) is False
    assert _dispatch(
        GestureEvent(GestureType.PINCH_UP, (0.0, 200.0, 0.0)), mapper, mouse, settings
    ) is True


def test_dispatch_reports_true_on_right_click():
    settings = Settings()
    mouse = FakeMouse()
    mapper = make_mapper()

    assert _dispatch(
        GestureEvent(GestureType.RIGHT_CLICK, (0.0, 200.0, 0.0)), mapper, mouse, settings
    ) is True


def test_dispatch_reports_false_when_pinch_remapped_to_scroll():
    settings = Settings(pinch_action="scroll")
    mouse = FakeMouse()
    mapper = make_mapper()

    _dispatch(GestureEvent(GestureType.PINCH_DOWN, (0.0, 200.0, 0.0)), mapper, mouse, settings)
    assert _dispatch(
        GestureEvent(GestureType.PINCH_UP, (0.0, 200.0, 0.0)), mapper, mouse, settings
    ) is False
