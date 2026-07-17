"""
mouse_control.py

thin wrapper around macOS's Quartz/CoreGraphics CGEvent APIs. this is the
only module that actually touches the real cursor, everything else in the
pipeline just decides what should happen.

kept separate on purpose so main.py can swap this out for a logging-only
stub when running with --dry-run.

not implemented yet, this is a scaffold stub.
"""

# TODO: move(x, y) using CGEventCreateMouseEvent(kCGEventMouseMoved, ...)
# TODO: mouse_down(button) / mouse_up(button)
# TODO: drag_to(x, y) using kCGEventLeftMouseDragged
# TODO: scroll(dx, dy) using CGEventCreateScrollWheelEvent
# TODO: remember, this requires Accessibility + Input Monitoring permission
#       granted to whatever process runs python, see docs/SETUP.md
