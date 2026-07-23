"""
tests for mouse_control.py: QuartzMouseController's event-construction logic
and state tracking, and DryRunMouseController's logging. Quartz itself is
mocked throughout, no real CGEventPost ever happens and no hardware/
permissions are needed.
"""

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from orvix.mouse_control import DryRunMouseController, QuartzMouseController


@pytest.fixture
def quartz():
    with patch("orvix.mouse_control.Quartz") as mock_quartz:
        # give the constants distinct sentinel values so assertions can
        # tell them apart, same as real Quartz would (arbitrary ints)
        mock_quartz.kCGEventMouseMoved = "moved"
        mock_quartz.kCGEventLeftMouseDragged = "dragged"
        mock_quartz.kCGEventLeftMouseDown = "down"
        mock_quartz.kCGEventLeftMouseUp = "up"
        mock_quartz.kCGEventRightMouseDown = "right_down"
        mock_quartz.kCGEventRightMouseUp = "right_up"
        mock_quartz.kCGMouseButtonLeft = "left_button"
        mock_quartz.kCGMouseButtonRight = "right_button"
        mock_quartz.kCGHIDEventTap = "hid_tap"
        mock_quartz.kCGScrollEventUnitLine = "line_unit"
        mock_quartz.kCGEventFlagMaskCommand = 1 << 20
        mock_quartz.kCGEventFlagMaskShift = 1 << 17
        mock_quartz.kCGEventFlagMaskControl = 1 << 18
        mock_quartz.kCGEventFlagMaskAlternate = 1 << 19
        mock_quartz.CGEventCreateMouseEvent = MagicMock(side_effect=lambda *a: ("mouse_event", a))
        mock_quartz.CGEventCreateScrollWheelEvent = MagicMock(side_effect=lambda *a: ("scroll_event", a))
        mock_quartz.CGEventCreateKeyboardEvent = MagicMock(side_effect=lambda *a: ("key_event", a))
        mock_quartz.CGEventCreate = MagicMock(return_value="current_event")
        mock_quartz.CGEventGetLocation = MagicMock(return_value=MagicMock(x=42, y=99))
        mock_quartz.CGEventSetFlags = MagicMock()
        mock_quartz.CGEventPost = MagicMock()
        yield mock_quartz


def test_move_posts_plain_move_when_button_up(quartz):
    controller = QuartzMouseController()
    controller.move(10, 20)
    kind = quartz.CGEventCreateMouseEvent.call_args[0][1]
    assert kind == "moved"
    quartz.CGEventPost.assert_called_once()


def test_move_posts_drag_when_button_down(quartz):
    controller = QuartzMouseController()
    controller.mouse_down()
    quartz.CGEventPost.reset_mock()
    controller.move(10, 20)
    kind = quartz.CGEventCreateMouseEvent.call_args[0][1]
    assert kind == "dragged"


def test_move_skips_duplicate_position(quartz):
    controller = QuartzMouseController()
    controller.move(10, 20)
    quartz.CGEventPost.reset_mock()
    controller.move(10, 20)
    quartz.CGEventPost.assert_not_called()


def test_move_does_not_skip_after_position_changes(quartz):
    controller = QuartzMouseController()
    controller.move(10, 20)
    controller.move(30, 40)
    assert quartz.CGEventPost.call_count == 2


def test_mouse_down_sets_button_down_and_clears_last_pos(quartz):
    controller = QuartzMouseController()
    controller.move(10, 20)
    controller.mouse_down()
    assert controller._button_down is True
    assert controller._last_pos is None
    kind = quartz.CGEventCreateMouseEvent.call_args[0][1]
    assert kind == "down"


def test_mouse_up_clears_button_down_and_last_pos(quartz):
    controller = QuartzMouseController()
    controller.mouse_down()
    controller.move(10, 20)
    controller.mouse_up()
    assert controller._button_down is False
    assert controller._last_pos is None
    kind = quartz.CGEventCreateMouseEvent.call_args[0][1]
    assert kind == "up"


def test_mouse_down_up_forces_next_move_to_post_even_at_same_pixel(quartz):
    # after mouse_down/up resets _last_pos, moving back to the same pixel
    # the hand was already at must still post, since the event type changed
    controller = QuartzMouseController()
    controller.move(10, 20)
    controller.mouse_down()
    quartz.CGEventPost.reset_mock()
    controller.move(10, 20)
    quartz.CGEventPost.assert_called_once()


def test_drag_to_skips_duplicate_position(quartz):
    controller = QuartzMouseController()
    controller.drag_to(5, 5)
    quartz.CGEventPost.reset_mock()
    controller.drag_to(5, 5)
    quartz.CGEventPost.assert_not_called()


def test_scroll_passes_dx_dy_in_quartz_convention(quartz):
    controller = QuartzMouseController()
    controller.scroll(3, 7)
    args = quartz.CGEventCreateScrollWheelEvent.call_args[0]
    # (None, unit, wheelCount=2, dy, dx) per CGEventCreateScrollWheelEvent signature
    assert args[2] == 2
    assert args[3] == 7
    assert args[4] == 3


def test_right_click_posts_down_then_up_at_current_location(quartz):
    controller = QuartzMouseController()
    controller.right_click()
    kinds = [call[0][1] for call in quartz.CGEventCreateMouseEvent.call_args_list]
    assert kinds == ["right_down", "right_up"]
    assert quartz.CGEventPost.call_count == 2


def test_click_posts_down_then_up_without_touching_button_down_state(quartz):
    controller = QuartzMouseController()
    controller.click()
    kinds = [call[0][1] for call in quartz.CGEventCreateMouseEvent.call_args_list]
    assert kinds == ["down", "up"]
    assert controller._button_down is False


def test_key_shortcut_presses_then_releases(quartz):
    controller = QuartzMouseController()
    controller.key_shortcut(36)
    presses = [call[0][2] for call in quartz.CGEventCreateKeyboardEvent.call_args_list]
    assert presses == [True, False]


def test_key_shortcut_applies_modifier_flags_to_both_events(quartz):
    controller = QuartzMouseController()
    controller.key_shortcut(8, mods=("cmd", "shift"))
    expected_flags = quartz.kCGEventFlagMaskCommand | quartz.kCGEventFlagMaskShift
    calls = quartz.CGEventSetFlags.call_args_list
    assert len(calls) == 2
    for call in calls:
        assert call[0][1] == expected_flags


def test_key_shortcut_skips_setflags_with_no_modifiers(quartz):
    controller = QuartzMouseController()
    controller.key_shortcut(8)
    quartz.CGEventSetFlags.assert_not_called()


def test_zoom_sets_command_flag_and_positive_steps_zoom_in(quartz):
    controller = QuartzMouseController()
    controller.zoom(2)
    args = quartz.CGEventCreateScrollWheelEvent.call_args[0]
    assert args[2] == 1  # wheelCount
    assert args[3] == 2  # steps
    quartz.CGEventSetFlags.assert_called_once()
    flags_arg = quartz.CGEventSetFlags.call_args[0][1]
    assert flags_arg == quartz.kCGEventFlagMaskCommand


def test_set_volume_relative_runs_osascript_with_delta(quartz):
    controller = QuartzMouseController()
    with patch("orvix.mouse_control.subprocess.run") as mock_run:
        controller.set_volume_relative(-15)
        script = mock_run.call_args[0][0][2]
        assert "-15" in script
        assert "output volume" in script


def test_set_volume_relative_swallows_oserror(quartz):
    controller = QuartzMouseController()
    with patch("orvix.mouse_control.subprocess.run", side_effect=OSError("no osascript")):
        controller.set_volume_relative(10)  # must not raise


def test_set_volume_relative_has_a_timeout_and_survives_one(quartz):
    controller = QuartzMouseController()
    with patch("orvix.mouse_control.subprocess.run") as mock_run:
        controller.set_volume_relative(5)
        assert mock_run.call_args.kwargs["timeout"] == 2.0

    with patch(
        "orvix.mouse_control.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="osascript", timeout=2.0),
    ):
        controller.set_volume_relative(5)  # must not raise, would hang the dispatch loop otherwise


def test_dry_run_controller_logs_every_action(caplog):
    controller = DryRunMouseController()
    with caplog.at_level("INFO", logger="orvix.mouse_control"):
        controller.move(1, 2)
        controller.mouse_down()
        controller.mouse_up()
        controller.drag_to(3, 4)
        controller.scroll(5, 6)
        controller.right_click()
        controller.key_shortcut(36, mods=("cmd",))
        controller.click()
        controller.zoom(-1)
        controller.set_volume_relative(-20)

    messages = [r.message for r in caplog.records]
    assert any("move to (1, 2)" in m for m in messages)
    assert any("mouse down" in m for m in messages)
    assert any("mouse up" in m for m in messages)
    assert any("drag to (3, 4)" in m for m in messages)
    assert any("scroll (5, 6)" in m for m in messages)
    assert any("right click" in m for m in messages)
    assert any("cmd" in m and "36" in m for m in messages)
    assert any(m.strip().endswith("click") and "right" not in m for m in messages)
    assert any("zoom -1" in m for m in messages)
    assert any("volume -20%" in m for m in messages)
