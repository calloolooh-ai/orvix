# orvix

control your mac's mouse with hand gestures using an og Leap Motion Controller (LM-010). no keyboard or trackpad needed, just your hand in the air above the sensor.

## what it does (v1)

- move the cursor by moving your hand
- pinch to click
- pinch and move to drag
- grab to scroll

that's it for now. no custom gesture macros or media key stuff yet, that's future work.

## how it works

your Leap Motion plugs in, the legacy `leapd` tracking service reads the sensor and streams hand data out as JSON over a local websocket. orvix connects to that stream, turns the raw hand data into gestures, maps your hand position onto your screen, and fires real mouse events through macOS's Quartz API.

see `docs/SETUP.md` for the full architecture writeup and setup steps, since getting this old hardware working on modern macOS takes a few extra steps.

## status

work in progress, building this out step by step. driver setup docs first, then the actual python pipeline.

## requirements

- macOS (built and tested on Monterey 12.7.6, Intel)
- an original Leap Motion Controller (LM-010)
- Python 3.9+

## setup

see `docs/SETUP.md` before you try to run anything, there's some driver install and macOS permission stuff you gotta do first or nothing will work.
