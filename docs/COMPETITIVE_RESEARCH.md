# Competitive Research: Hand/Gesture-Based Cursor Control Projects

Research pass comparing orvix against other Leap Motion projects, webcam-based (MediaPipe/OpenCV) virtual mouse projects, and accessibility-focused hands-free cursor tools. Goal: find features other projects have that orvix doesn't, and confirm where orvix is already ahead.

## 1. Summary / TL;DR

Most Leap-Motion-specific mouse-control repos on GitHub are small, single-gesture demos (move + click, sometimes scroll) with no calibration flow, no cursor modes, and no anti-drift handling — orvix is meaningfully more built-out than any Leap Motion project found. The interesting competition is actually the webcam/MediaPipe ecosystem, which is far more active in 2026 and has explored things orvix hasn't: voice command integration, adaptive cursor acceleration with drift correction, target-aware "magnetic" click assistance, and multi-hand/two-handed gesture vocabularies for system-level actions (brightness/volume) beyond what orvix maps. None of these projects combine calibration UX, multiple cursor-mapping modes, and a menu-bar GUI the way orvix does — that combination looks close to unique.

## 2. Projects surveyed

### Leap Motion specific

| Project | Link | Tech | Key features |
|---|---|---|---|
| Control-Mouse-through-Gesture-using-Leap-Motion | [github.com/JeevanDrave/...](https://github.com/JeevanDrave/Control-Mouse-through-Gesture-using-Leap-Motion) | Leap Motion SDK | Cursor move, index-finger-tap left click, circular-motion scroll wheel. No drag, no right click, no calibration. |
| LeapMotionMouse | [github.com/nikitasrivatsan/LeapMotionMouse](https://github.com/nikitasrivatsan/LeapMotionMouse) | Leap Motion SDK, Java | Basic gesture-to-mouse mapping, no extended gesture set. |
| leapgim (legacy) | [github.com/Zeukkari/leapgim-legacy](https://github.com/Zeukkari/leapgim-legacy) | Leap Motion SDK | Custom gesture definition/mapping framework for mouse *and* keyboard actions — a prototyping platform for testing arbitrary gesture reliability, not a finished cursor-control app. |
| leap-gestures | [github.com/cevaris/leap-gestures](https://github.com/cevaris/leap-gestures) | Leap Motion SDK | Custom gesture recognizer library, no full cursor pipeline. |
| leap-motion-gesture-control | [github.com/tjeffree/leap-motion-gesture-control](https://github.com/tjeffree/leap-motion-gesture-control) | Leap Motion SDK | System tray app for handling gestures, minimal scope. |
| LEAP-OSXGestureController | [github.com/clwillingham/LEAP-OSXGestureController](https://github.com/clwillingham/LEAP-OSXGestureController) | Leap Motion SDK, macOS | Switch Spaces, Mission Control, Expose via gestures — no cursor/mouse control at all, purely a shortcut launcher (closest thing to orvix's radial menu concept, but no visual menu, no dwell, no configurability). |
| leapmouse (itch.io) | [sne9x.itch.io/leapmouse](https://sne9x.itch.io/leapmouse) | Leap Motion SDK, Windows | Downloadable basic mouse-control tool, no public feature docs beyond basic control. |
| Official Ultraleap/Leap Motion desktop software | [leapmotiontechnology.com/product/desktop](https://www.leapmotiontechnology.com/product/desktop), [docs.ultraleap.com](https://docs.ultraleap.com/hand-tracking/getting-started.html) | Ultraleap SDK (Orion/Gemini tracking) | Modern hardware (Leap Motion Controller 2) tracks 200fps, 150° FOV, up to 27 hand elements including occluded joints. Software modes: High Performance, UI Input Mode (optimized for extended-arm UI interaction), Low Power Mode. Positioned around an app "Gallery" (games, art, VR demos) — does **not** ship first-party OS-level mouse control as a product feature; that's left to third parties/demos. |

### Webcam / MediaPipe-based virtual mouse projects

| Project | Link | Tech | Key features |
|---|---|---|---|
| AI-Virtual-Mouse-Controller | [github.com/Yousef-Elbeyaly/...](https://github.com/Yousef-Elbeyaly/AI-Virtual-Mouse-Controller) | MediaPipe + OpenCV | Cursor via hand gesture over webcam, basic click. |
| Virtual-Mouse (whitehatboy005) | [github.com/whitehatboy005/Virtual-Mouse](https://github.com/whitehatboy005/Virtual-Mouse) | MediaPipe + OpenCV | Move, left/right click, drag, scroll — comparable core gesture set to orvix but no cursor modes or radial menu. |
| Virtual_Mouse (aadityasp) | [github.com/aadityasp/Virtual_Mouse](https://github.com/aadityasp/Virtual_Mouse) | MediaPipe + OpenCV | Live visualization of all 21 3D hand landmarks overlaid on the camera feed while controlling the cursor (a debug/skeleton view baked into the *live* control session, not a separate mode like orvix's `orvix hand`). |
| Hand-Gesture-Mouse-Scroll-Controller | [github.com/SrishtiS-git/...](https://github.com/SrishtiS-git/Hand-Gesture-Mouse-Scroll-Controller-using-MediaPipe) | MediaPipe + PyAutoGUI | Click, scroll, and window-minimize gesture. |
| hand-gesture-virtual-mouse (maanjk) | [github.com/maanjk/hand-gesture-virtual-mouse](https://github.com/maanjk/hand-gesture-virtual-mouse) | OpenCV + MediaPipe | Move/click/drag/scroll, CPU-only, Windows-targeted. |
| handTrack | [github.com/small-cactus/handTrack](https://github.com/small-cactus/handTrack) | OpenCV + MediaPipe + PyAutoGUI | Absolute hand-to-screen mapping (camera input maps directly to screen coordinates — comparable to orvix's "absolute" cursor mode but without a calibration step). Dynamic mouse-step generation to compensate for variable camera frame rate. Multithreaded for latency. Refined click detection using both finger-contact *and* hand-size change together to cut false positives. Auto-disables tracking when no hand is visible and deliberately tracks only one hand to avoid interference from bystanders. |
| Gesture-Controlled-Virtual-Mouse (Viral-Doshi) | [github.com/Viral-Doshi/...](https://github.com/Viral-Doshi/Gesture-Controlled-Virtual-Mouse) | MediaPipe + OpenCV + voice ("Proton" assistant) | Cursor at index/middle fingertip midpoint. Left/right/double-click. Scroll speed *proportional to pinch distance* (analog, not binary). Multi-item selection gesture. Volume/brightness control where the **rate of change is proportional to pinch distance**, not a discrete step or twist-to-set like orvix's volume knob. Voice assistant layered on top: launch/stop gesture recognition by voice, web search, maps navigation, file/directory browsing, clipboard copy/paste, sleep/wake, all via speech. |

### Accessibility-focused hands-free cursor control

| Project | Link | Tech | Key features |
|---|---|---|---|
| CursorViaCam | [github.com/Deepender25/CursorViaCam](https://github.com/Deepender25/CursorViaCam) | Webcam head-tracking (not hand) | Adaptive cursor acceleration (analyzes current movement velocity to scale speed in real time, distinct from orvix's fixed per-mode speed curves). Automated **drift correction** for cursor creep over a session. Windows-only **target-aware button sticking**: cursor gets a temporary "magnetic" pull toward clickable UI elements as it nears them, to help precision. Optional on-screen colored ring around the cursor for visual feedback. Full profile system: named, saved/loadable JSON profiles bundling smoothing, padding, and (for head/blink tracking) blink thresholds. |
| Camera Mouse / face-and-gesture accessibility research | [ScienceDirect: Mouse Cursor Control Based on Hand Gesture](https://www.sciencedirect.com/science/article/pii/S2212017316001389), [uMouse](http://larryo.org/work/information/umouse/index.html), [arXiv: head+voice control prototype](https://arxiv.org/pdf/1109.1454) | Webcam, face/eye/head tracking | Long-running academic/accessibility line of work; common pattern is combining an alternate input channel (blink, dwell, voice) as the "click" trigger since users often can't perform a physical pinch/tap. Doesn't map onto Leap Motion directly but is relevant prior art for dwell-click tuning and alternate-modality clicking. |

## 3. Feature gap analysis — things others have that orvix doesn't

| Feature | Seen in | Feasibility/relevance for orvix |
|---|---|---|
| **Voice command layer** (start/stop tracking, app launching, web search, clipboard ops) | Gesture-Controlled-Virtual-Mouse ("Proton") | Feasible as an optional, separate input channel — macOS has built-in speech APIs. Low priority: orvix's whole pitch is hands-only, no-keyboard control; voice is a different value prop, and Ultraleap/Apple already do this well natively. Worth considering only for a narrow "pause/resume" or "orvix, calibrate" trigger, not a full assistant. |
| **Analog (proportional) scroll/volume rate**, i.e. rate scales continuously with pinch/gesture distance rather than being a fixed step or discrete twist amount | Gesture-Controlled-Virtual-Mouse | Directly applicable — orvix's grab-scroll and fist-twist-volume are presumably closer to a mapped delta already, but explicitly tuning scroll/volume *rate* (not just position) to gesture magnitude, with a min/max clamp, is a small, high-value tweak. |
| **Adaptive cursor acceleration based on live velocity** (small movements = fine control, fast movements = big jumps), independent of the fixed cursor-mode curve | CursorViaCam | Relevant to orvix's `relative` mode specifically — right now speed scaling is presumably a static curve. An acceleration curve that adapts to the *current* motion, not just instantaneous speed, could reduce the trackpad-style precision/speed tradeoff. Medium priority, moderate implementation complexity (need careful tuning to avoid feeling twitchy given Leap's noisier depth tracking vs. a trackpad). |
| **Automated drift correction over a session** (cursor doesn't creep over time even with tracking noise) | CursorViaCam | Orvix already solves the *acute* drift problem (freezing cursor on pinch to stop click-time slide), but doesn't appear to address *slow* drift accumulation over a longer session in relative/tilt mode. Worth checking with real usage data before building — may not actually be a problem for orvix's filtering. |
| **Target-aware "magnetic" cursor snapping near clickable UI elements** | CursorViaCam (Windows-only there) | Interesting but hard on macOS — requires accessibility-tree introspection to know where clickable targets are (AXUIElement APIs), which is a much bigger lift than anything else on this list and platform/app-dependent. Low priority: high effort, uncertain payoff, and works against orvix's "precision via freeze-on-pinch" approach which already targets the same problem differently. |
| **Visual on-screen cursor highlight ring for feedback** | CursorViaCam | Very cheap to add and orvix already has the "last gesture event live" menu bar display plus two dedicated visualizer modes — an optional subtle highlight ring around the real cursor (not a separate viz mode, just a toggle during normal use) would be a small polish win, especially for demoing/screen-recording orvix. |
| **Named, saved/loadable config profiles** (not just one active config) | CursorViaCam | Orvix has one `~/.orvix/config.yaml`. Multiple named profiles (e.g. "gaming," "precision work," "demo") that can be swapped from the menu bar would be a nice quality-of-life feature and is low effort since config is already YAML-based. |
| **Live skeleton/landmark overlay directly during normal cursor-control use** (not a separate non-interactive mode) | Virtual_Mouse (aadityasp) | Orvix already has `orvix hand` but it's a separate, non-interactive full-screen visualizer. A lightweight opt-in overlay (small corner HUD) while actually driving the cursor, for debugging/tuning gestures live, could be useful — lower priority since the dedicated visualizer already covers most of this need. |
| **Auto-disable tracking / single-hand-only enforcement to reject bystander interference** | handTrack | Orvix's "drop hand near sensor to park" solves the disengage case for the *user's own* hand, but doesn't explicitly guard against a Leap Motion picking up a second hand/bystander in frame. Given Leap's narrow FOV and cone-shaped sensing area this is a much smaller real-world risk than for an open webcam, so priority is low, but a defensive "ignore secondary hand" rule costs little to add. |
| **Discrete "gallery"/multi-app ecosystem of built demos** | Official Ultraleap Leap Motion product | Not relevant to orvix's scope — orvix is a focused OS-control tool, not an app platform. Explicitly out of scope. |
| **Frame-rate-adaptive mouse step generation** to smooth out variable input rates | handTrack | Possibly already handled by orvix's One Euro Filter (which is designed for exactly this kind of jitter/latency tradeoff), but worth a note in `orvix profile` output to confirm frame-rate variance is covered, since handTrack treats it as a distinct problem from filtering. |

## 4. Where orvix is already ahead / differentiated

- **Cursor-freeze-on-pinch-start anti-drift trick**: none of the surveyed projects (Leap or webcam-based) mention solving the "cursor slides as your hand shifts while pinching" problem at the *gesture-recognition* level the way orvix does; CursorViaCam's drift correction is a different problem (slow session-long creep), and target-snapping (its other precision fix) requires OS accessibility APIs orvix doesn't need.
- **Three distinct, user-selectable cursor-mapping modes** (relative/tilt/absolute), each with different calibration requirements and tradeoffs clearly documented. No other project surveyed offers more than one mapping mode — most hard-code one approach (usually closest to orvix's "absolute").
- **Visual, on-screen radial menu** fired by a drawn gesture, with dwell-or-pinch selection and live self-drawing — the closest analog found (LEAP-OSXGestureController) does Mission-Control/Expose shortcuts but with no visual menu, no dwell option, and a fixed, non-remappable action set.
- **Calibration UX with live coverage feedback** (ascii box in terminal, HUD overlay in GUI) — none of the surveyed projects show live calibration progress; most either skip calibration entirely (relative/proportional mapping) or do a silent one-shot calibration.
- **Menu bar GUI + CLI sharing one `run_live()` pipeline**, with remapping of gestures, dry-run mode, per-gesture toggles, and multi-monitor handling all exposed there — most competing projects are either GUI-less scripts or a single-purpose tray icon (tjeffree's project) with far less configurability.
- **Dedicated performance profiling tool** (`orvix profile`, synthetic, no hardware needed) reporting One Euro Filter beta tradeoffs and per-frame CPU cost against a 100fps budget — no comparable tooling found in any surveyed project; most don't discuss latency/CPU budget at all.
- **Test suite with CI**, scoped to pure logic with mocked hardware boundaries — most of the surveyed hobby projects have no tests at all.
- **Multi-monitor-aware mapping** via `CGGetActiveDisplayList` — not mentioned in any surveyed project; most either don't address multi-monitor or implicitly assume single-display.

## 5. Suggested next features to consider, prioritized

this list is from the original research pass; see `docs/FEATURE_PLANS.md` for what's actually shipped since then. items 1, 2, 3, 5, and 6 below are all done now — kept here for the original reasoning/context, not as an open TODO list.

1. **Proportional scroll/volume rate** tied to gesture magnitude (grab depth / twist angle), with min/max clamps — small change, directly inspired by Gesture-Controlled-Virtual-Mouse's proportional pinch-to-brightness/volume, likely improves feel immediately. **DONE**, see `docs/FEATURE_PLANS.md` item 1.
2. **Named config profiles**, swappable from the menu bar (e.g. `~/.orvix/profiles/*.yaml`) — low effort given the existing YAML config, clear quality-of-life win (e.g. a "demo/recording" profile vs a "daily use" profile). **DONE**, see `docs/FEATURE_PLANS.md` item 2.
3. **Optional on-screen cursor highlight ring toggle** for normal use (not just the dedicated visualizers) — cheap, useful for screen recordings/demos and for users learning the gesture set. **DONE**, see `docs/FEATURE_PLANS.md` item 3.
4. **Adaptive velocity-based acceleration curve for relative mode** — moderate effort, needs real tuning/testing against Leap's tracking noise; validate with `orvix profile`-style synthetic benchmarking before shipping. Still open.
5. **Defensive single-hand-only enforcement** (ignore a second hand entering the Leap's field) — cheap insurance against edge cases, lower priority given Leap's narrow sensing cone makes bystander interference unlikely compared to an open webcam setup. **DONE**, see `docs/FEATURE_PLANS.md` item 5.
6. **Investigate session-long drift** in relative/tilt mode with real usage logs before building anything — CursorViaCam's correction targets a problem that may or may not actually exist for orvix given the One Euro Filter is already in the pipeline; don't build speculatively. **DONE (investigated, no correction needed)**, see `docs/FEATURE_PLANS.md` item 6.
7. **Target-aware click assistance** — deprioritized. High implementation cost (macOS Accessibility API integration, per-app variability) for a problem orvix already addresses differently (freeze-on-pinch). Revisit only if freeze-on-pinch turns out insufficient for small UI targets. Still open/deprioritized.
8. **Voice trigger for pause/resume or calibrate** — optional/low priority, keep scope narrow (a couple of trigger phrases, not a full assistant) if pursued at all, since it cuts against orvix's "hands only, nothing else" positioning. Still open/deprioritized.

## References

- https://github.com/JeevanDrave/Control-Mouse-through-Gesture-using-Leap-Motion
- https://github.com/nikitasrivatsan/LeapMotionMouse
- https://github.com/Zeukkari/leapgim-legacy
- https://github.com/cevaris/leap-gestures
- https://github.com/tjeffree/leap-motion-gesture-control
- https://github.com/clwillingham/LEAP-OSXGestureController
- https://sne9x.itch.io/leapmouse
- https://www.leapmotiontechnology.com/product/desktop
- https://docs.ultraleap.com/hand-tracking/getting-started.html
- https://github.com/Yousef-Elbeyaly/AI-Virtual-Mouse-Controller
- https://github.com/whitehatboy005/Virtual-Mouse
- https://github.com/aadityasp/Virtual_Mouse
- https://github.com/SrishtiS-git/Hand-Gesture-Mouse-Scroll-Controller-using-MediaPipe
- https://github.com/maanjk/hand-gesture-virtual-mouse
- https://github.com/small-cactus/handTrack
- https://github.com/Viral-Doshi/Gesture-Controlled-Virtual-Mouse
- https://github.com/Deepender25/CursorViaCam
- https://www.sciencedirect.com/science/article/pii/S2212017316001389
- http://larryo.org/work/information/umouse/index.html
- https://arxiv.org/pdf/1109.1454
