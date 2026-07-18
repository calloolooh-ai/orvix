# orvix

control your mac's mouse with hand gestures using an og Leap Motion Controller (LM-010). no keyboard or trackpad needed, just your hand in the air above the sensor.

## what it does

- move the cursor by moving your hand
- pinch (thumb + index) to click, hold past 0.3s and it becomes a drag
- pinch thumb + **middle** finger for a right click
- grab (make a fist) to scroll
- drop your hand near the sensor to park: tracking down there is junk anyway, so it's treated as no hand and the cursor stops dead

grab only kicks in on an actual closed fist now, not a loose curl, since leapd reads grabStrength high way before your hand is really shut. you can tune how strict that is from the menu bar.

### extra gestures

on top of the core set, these can each be toggled in the menu bar (under More gestures) or in config:

- **draw a circle** to pop up a radial menu, then point at a wedge and either pinch or just rest on it (dwell) to fire it. the wheel is Mission Control, Maximize, App Switcher, Undo, Copy, Paste, Screenshot, and Close, and it draws itself on screen while it's open.
- **two-hand pinch** and pull apart / push together to zoom
- **make a fist and twist your wrist** like a knob to change volume
- **hold the cursor still** for a beat to left-click, no pinch needed (dwell click)
- **hold both palms out** like a stop sign to pause orvix, do it again to resume
- **hold a thumbs-up** to press Return

the cursor freezes the moment you start closing your fingers, so the click lands where you aimed instead of sliding off as your palm shifts. that drift is the classic hand-tracking-cursor problem and most projects never fix it.

### cursor modes

set `cursor_mode` in `~/.orvix/config.yaml`:

- **relative** (default): trackpad style. cursor moves by however far your hand moved, speed scales with how fast you move. no calibration needed at all, no dead edges. pull your hand away and back to re-centre.
- **tilt**: joystick style. hold your hand still and tilt it, cursor drifts that way, flat means stop. easily the least tiring and can't run out of room, but slowest across a big screen.
- **absolute**: your hand's position in the calibration box *is* the cursor position. point at a corner, cursor's there. needs `orvix calibrate` to feel right, and the leap's field of view is a pyramid while the box is a rectangle, so the screen edges go dead when your hand is low.

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
- set how strict grab is about being a real fist
- turn the radial menu on/off and set its dwell time
- toggle any of the extra gestures (zoom, volume, dwell click, palms-out pause, thumbs-up)
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
