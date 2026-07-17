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

core pipeline is built and working (leap_client -> gesture_interpreter -> coord_mapper -> mouse_control). there's also a menu bar GUI now for running it without a terminal.

## running it

symlink the launcher onto your PATH once:

```
ln -sf "$PWD/bin/orvix" /usr/local/bin/orvix
```

then from anywhere:

```
orvix              # menu bar app
orvix cli          # cli instead, takes any main.py flag (--dry-run, --verbose)
orvix calibrate    # terminal calibration flow
orvix status       # check leapd + device + config, launches nothing
```

`orvix` doesn't start leapd, and doesn't need to: leapd installs as a LaunchDaemon with `KeepAlive=true`, so launchd already keeps it alive at boot. the launcher just checks it's up and tells you how to kick it if it isn't.

whichever terminal you launch from needs Accessibility + Input Monitoring, since macOS ties that permission to the launching app and silently drops the events (no error) if it's missing.

## gui

`orvix` puts an icon in your menu bar. from there you can:

- start/stop the live pipeline
- toggle dry-run (logs intended actions instead of moving the real cursor)
- remap what pinch and grab actually do: Click / Drag, Scroll, or Disabled
- run calibration
- see the last gesture event live

everything still runs through the same `run_live()` in `orvix/main.py` the CLI uses, so CLI and GUI behavior can't drift apart.

`python -m orvix.main` (with `--dry-run`, `--verbose`, `--calibrate`) still works as a plain CLI if you'd rather not use the menu bar app.

## requirements

- macOS (built and tested on Monterey 12.7.6, Intel)
- an original Leap Motion Controller (LM-010)
- Python 3.9+

## setup

see `docs/SETUP.md` before you try to run anything, there's some driver install and macOS permission stuff you gotta do first or nothing will work.
