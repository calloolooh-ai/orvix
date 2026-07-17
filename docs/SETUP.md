# orvix setup guide

this is the annoying part. your LM-010 is old hardware and modern macOS
doesn't make it easy. do this stuff before you try to run any orvix code,
otherwise you'll just be debugging phantom python bugs that are actually
driver problems.

## why this hardware needs old software

the original Leap Motion Controller (LM-010) only reliably works with
Ultraleap's legacy **V2 tracking software** (the `leapd` daemon + SDK
v2.3.1). their newer Gemini (V5) software is built for the newer hardware
(Leap Motion Controller 2 / Stereo IR 170). some people online claim Gemini
still works with the old controller but that's not the confirmed/supported
path, so we're going with V2. if V2 turns out to be a dead end we can
revisit, but start here.

## step 1: install the Leap V2 SDK

download SDK v2.3.1 for macOS from Ultraleap's developer site (or the
archived developer portal if the main site has moved it). run the installer
pkg. this installs `leapd`, the background tracking service, plus a control
panel/visualizer app you can use to sanity check tracking without writing
any code.

## step 2: fix the UVCAssistant conflict

this is the step that trips everyone up on Monterey and later. macOS treats
the Leap Motion Controller as a generic UVC webcam and its built-in
`UVCAssistant` process grabs the device before `leapd` gets a chance to. when
this happens `leapd` runs but never sees any hand data, no errors, just
nothing.

fix: kill/block `UVCAssistant` before starting `leapd`. quick version:

```bash
# find it
ps aux | grep UVCAssistant

# kill it (it may respawn, that's macOS being annoying, kill it again
# or find a launchd-based way to keep it from grabbing the device)
sudo kill -9 <pid>
```

you may need to redo this after every reboot or every time you unplug and
replug the device, depending on which workaround ends up sticking on your
system. if `sudo kill` alone doesn't hold, look into disabling the relevant
launchd service so it doesn't respawn and steal the device again.

## step 3: start leapd and confirm it's tracking

start the Leap control panel / visualizer app that came with the SDK
install. plug in the LM-010, hold your hand over it, and confirm the
visualizer actually shows a hand. if it doesn't, go back to step 2, this is
almost always the UVCAssistant thing.

## step 4: confirm the raw websocket stream works

`leapd` exposes tracking data as JSON over a local websocket at:

```
ws://localhost:6437/v6.json
```

before writing any orvix code, confirm this actually works. easiest way is
a raw websocket tool like `websocat`:

```bash
brew install websocat
websocat ws://localhost:6437/v6.json
```

wave your hand over the sensor, you should see a stream of JSON frame
objects scroll by with a `hands` array that's non-empty when your hand is
in view. if you see frames but `hands` is always empty, check your hand is
actually in the sensor's field of view and not blocked by a coiled cable
or something dumb like that.

**save a chunk of this output**, we'll use it later as
`tests/fixtures/sample_frames.json` so orvix's tests can run against real
data without needing the hardware plugged in every time.

## step 5: macOS permissions for mouse control

orvix moves your actual cursor using macOS's Quartz/CoreGraphics APIs. this
needs two permissions granted to whatever process runs python (Terminal,
iTerm, whatever you use):

- **System Preferences -> Security & Privacy -> Privacy -> Accessibility**
- **System Preferences -> Security & Privacy -> Privacy -> Input Monitoring**

grant both. if you only grant one, mouse events might silently do nothing,
no error, no crash, the cursor just doesn't move. this is the single most
confusing failure mode if you don't know to check it.

**important:** after granting these, fully quit and relaunch your terminal
app. don't just re-run the script in the same window, the permission grant
doesn't apply retroactively to an already-running process.

## step 6: python environment

```bash
cd ~/orvix
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

confirm `pip install pyobjc-framework-Quartz` pulled a prebuilt wheel and
didn't try to compile from source. if it tried to build, something's off
with your python/architecture setup (shouldn't happen on Intel Monterey,
but worth a sanity check).

## a note on "focus"

`leapd`'s websocket protocol has its own idea of "focus" that's separate
from normal macOS window focus. if another app has claimed Leap focus, or
if `leapd` denies your background data request, you'll connect fine but get
no frames, and it'll look exactly like a driver problem even though the
driver's fine. orvix's client requests `background: true` on connect to try
to avoid this, but if you're ever stuck with a connection that "works" but
sends nothing, this is worth checking before you start tearing into the
python code.

## troubleshooting checklist

if something's not working, check in this order:

1. is `leapd` actually running? (check Activity Monitor / `ps aux | grep leapd`)
2. does the Leap visualizer app see your hand? if not, it's UVCAssistant, go back to step 2
3. does raw `websocat` output show non-empty `hands` arrays? if not, still a driver/hardware problem, not a python problem
4. is Accessibility AND Input Monitoring both granted, and did you restart the terminal after granting?
5. only after all of the above check out is it worth debugging orvix's python code itself
