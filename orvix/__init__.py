"""
orvix - hand gesture mouse control for macOS using an original Leap Motion Controller.

package layout:
    leap_client.py         - talks to the leapd websocket, yields parsed frame dicts
    gesture_interpreter.py - turns raw hand frames into semantic gesture events
    coord_mapper.py        - maps Leap 3D hand position to screen pixels, with smoothing
    mouse_control.py        - posts real mouse events to macOS via Quartz/CGEvent
    calibration.py          - interactive flow to record your comfortable hand range
    config.py               - loads/saves settings
    main.py                 - wires everything together into the live control loop
"""

__version__ = "0.1.0"
