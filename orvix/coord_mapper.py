"""
coord_mapper.py

maps a hand's position in Leap's 3D coordinate space (millimeters) into
2D screen pixel coordinates, using a calibrated interaction volume.

also owns the One Euro Filter smoothing pass on the mapped 2D point, so the
cursor doesn't jitter when your hand is nearly still but still stays
responsive on fast moves. see docs/SETUP.md for why One Euro over plain EMA.

pure logic module, no hardware or macOS calls, unit testable on its own.

not implemented yet, this is a scaffold stub.
"""

# TODO: calibration box (min/max x/y/z Leap-space extents) loaded from config
# TODO: linear map from calibration box -> normalized [0,1] -> screen pixels
# TODO: clamp to screen bounds so hand outside the box doesn't fling the cursor offscreen
# TODO: vendor a small One Euro Filter implementation and apply it to the
#       mapped point before returning it
