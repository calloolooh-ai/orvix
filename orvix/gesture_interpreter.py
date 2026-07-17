"""
gesture_interpreter.py

takes raw hand frame dicts (from leap_client) and turns them into semantic
gesture events: POINT_MOVE, PINCH_DOWN, PINCH_DRAG, PINCH_UP, GRAB_SCROLL.

this module is pure logic, no websocket or macOS stuff in here, so it can be
unit tested against recorded fixture frames with no hardware attached.

not implemented yet, this is a scaffold stub.
"""

# TODO: stateful class that tracks pinch/grab state across frames for debounce
# TODO: threshold leapd's own pinchStrength/grabStrength fields, don't
#       recompute pinch geometry ourselves from fingertip positions
# TODO: emit events as simple dataclasses or namedtuples
