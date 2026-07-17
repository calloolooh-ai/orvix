"""
calibration.py

interactive cli flow: asks you to hold your hand at the screen corners and
center, records the Leap-space extents it sees, and writes them into the
config so coord_mapper.py knows how to map your actual comfortable hand
range onto your actual screen size.

not implemented yet, this is a scaffold stub.
"""

# TODO: prompt user through top-left, top-right, bottom-left, bottom-right, center
# TODO: average a few frames at each point to reduce noise in the recorded sample
# TODO: write resulting calibration box into config.py's settings file
