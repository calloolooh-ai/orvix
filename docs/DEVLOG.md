# devlog

running log of what got done this session. all commits are local only, nothing pushed to main yet.

## profiles + gui

- added named config profiles to config.py (list/save/load/delete), so you can have more than one saved setup instead of just the one config.yaml
- wired those profiles into the menu bar so you can actually switch/save/delete them from the gui
- added a confirm prompt before save-as silently overwrites an existing profile, matching how delete already asks

## cursor ring

- added an optional always-on cursor highlight ring toggle, separate from the dwell countdown ring that already existed
- made the ring flash on a landed click (pinch/grab/right click)
- then found dwell click was skipping the flash completely since it fires through a different code path than the other clicks, fixed that too
- did a full pass checking every other action (radial menu, thumbs up, zoom, volume, pause) to make sure none of those needed the flash either, they dont, already covered by their own feedback

## gesture tuning

- made the fist twist volume knob scale rate with how fast you twist instead of a fixed step every time
- looked into whether cursor drift builds up over a long session in relative/tilt mode, turns out it doesnt, added a long session test to prove it instead of building a fix nobody needed

## bug fixes, mostly config/crash related

- fixed a bunch of ways a bad or hand edited config.yaml could crash orvix silently since the menu bar app has no terminal to show errors in:
  - unknown/typo'd keys in the yaml
  - wrong types (string where a number should be)
  - out of range values (negative durations, percentages over 100)
  - invalid action names like pinch_action or radial_actions
  - fast_speed <= slow_speed causing a divide by zero in the cursor gain math
- fixed calibration being able to double start if you clicked it twice fast, which raced two threads against the same state
- fixed stale gesture state firing a click or thumbs up right after you un-pause, since the pause didnt reset dwell/confirm timers
- fixed the desktop bounds going stale if you plug/unplug a monitor mid session
- gave "first" hand tracking mode identity continuity so a second hand showing up cant hijack the cursor mid use

## cli fixes found by actually running things

- fixed orvix status giving a false "cant reach leapd" error when leapd is fine but no device event ever came in
- fixed orvix calibrate hanging forever with no timeout when theres no leap device plugged in

## test coverage + cleanup

- added test coverage for leap_client's frame parsing helpers, mouse_control.py, and overlay.py, none of which had any tests before
- ran pyflakes across orvix/ and tests/, dropped a handful of unused imports
- updated the readme so it actually matches current behavior (volume rate, cursor ring, profiles)

## verification passes that found nothing (also worth logging)

- ran the full test suite 3x in a row looking for flaky tests, found none
- launched the real menu bar app live and confirmed it starts and shuts down clean
- checked orvix profile's actual numbers still make sense after all the mapper changes, they do
- did a full audit late in the session for any remaining state leak or config validation gaps, came up empty, which is a good sign the easy stuff is done

## radial menu state leaks

traced a flagged-but-never-checked risk in the feature plan and it turned into three bugs in the same spot: opening the radial menu skips the normal per frame update loop, and turns out three different things were relying on that loop running every frame:

- the cursor ring froze in place instead of hiding while the wheel was open
- dwell click and thumbs up confirm timers kept their old timestamps frozen, so closing the wheel could fire a click or confirm you never actually did
- a pinch or grab that was mid hold when the wheel opened had the same problem, could fire a phantom drag or leave the mouse button stuck down on close

fixed all three, then went and checked pause/resume and hand drop for the same class of bug on purpose, both already handled it right.

## more opt-in shortcuts

- added spotlight, force quit, and lock screen as new named shortcuts you can assign to the radial menu or thumbs up, all opt-in so nobody's existing setup changes
- updated the readme since it only mentioned the original 7 wedges

## more bug fixes from re-reading old modules

- fixed onboarding not detecting first run correctly if you saved a profile without ever touching config.yaml, would keep showing the welcome nag forever
- fixed collect_range and collect_neutral_tilt in calibration.py hanging forever if the leap device disappears mid sweep, same bug wait_for_hand already got fixed for earlier, just at two more call sites in the same file
- fixed handrender's docstring claiming multi monitor support it doesnt actually have

## ux polish

- calibration used to just sit there with no feedback for up to 30 seconds while waiting for a hand, looked exactly like a hang, added a status message
- same problem existed on all 5 of the menu bar's pipeline restart actions (cursor mode, multi monitor, extra gestures, dwell, profile load), each blocks up to 2 seconds with zero feedback, added a restarting status to all of them

## code cleanup

- cleaned up a stray import and inconsistent blank lines in main.py from a bunch of patches getting layered in over time
- pulled the pipeline restart code that got copy pasted into all 5 gui.py menu handlers into one shared method

## more verification, nothing found

- did a full sweep for any other "async for" over a leapd stream that could hang the same way calibration did, found nothing else needs fixing, the pattern is closed out now
- reread config.py and extra_gestures.py for redundant logic from being patched so many times, both still hold up clean

## more code quality + final checks

- reread coord_mapper.py and calibration.py for the same kind of redundancy, found some surface-level duplication but decided not to touch it since the "duplicate" code actually differs in subtle edge case handling, would've been a risky refactor for basically nothing
- brought this devlog itself up to date after it went stale for a while
- re-ran the live cli checks (orvix status, orvix cli --dry-run) and the real menu bar app one more time as a regression check after ~30 more cycles of changes, everything still holds up clean
- traced the new shortcuts (spotlight, force quit, lock screen) through the radial menu fire path on purpose, confirmed they get treated exactly like the original 7 wedges, no special casing that could've been missed
- did a full 60 cycle retrospective: all 6 planned features done, 3 bug families fully closed out, codebase is in a solid, well tested state at this point. still finding the occasional small thing but the big stuff is done

## deep maintenance mode (cycles 61-73ish)

genuinely scraping the bottom now, most cycles come back with nothing:

- marked feature plan items 1-4 as done, they'd shipped ages ago but never got the done marker
- found orvix's version string existed in code but was never shown to the user anywhere, added it to the menu bar dropdown
- re-ran pyflakes, ran a real coverage.py pass, checked dead code across every function/config field/constant, checked requirements.txt versions against what's installed, checked for deprecation warnings, checked executable bits on scripts, byte-compiled every module, all clean
- devlog kept going stale between updates since these small checks aren't happening every single cycle anymore, catching it up again here

~73 cycles total, 53ish commits. full history in git log if you want the exact diffs.

## overlay wedge labels went stale (cycle 76)

found overlay.py had its own hardcoded `_LABELS` dict for the radial wheel's wedge text, separate from shortcuts.py's `NAMED_SHORTCUT_LABELS`. it never got updated when spotlight/force_quit/lock_screen were added as opt-in shortcuts a while back, so if you put one of those in `radial_actions` (config.py validates it as a legit wedge name) the wheel would just show the raw id like "spotlight" instead of "Spotlight Search" while every other wedge got a real title. made `_LABELS` derive from `NAMED_SHORTCUT_LABELS` instead of duplicating it, plus a test asserting every valid radial action has a label so this can't silently drift again.

## config sanitization went the rest of the way

a run of a few cycles found `thumbs_up_action`, `preferred_hand`, and `cursor_mode` were the last config fields that could take a bogus value and silently misbehave (never fire, tracking never starts, mode quietly falls back) instead of warning like `pinch_action`/`grab_action`/`radial_actions` already did. all three now get sanitized the same way, with tests. checked the rest of config.py's fields against `_sanitize_settings` after and they're all already covered (numeric ranges clamped, enums validated) -- this vein's actually closed out now.

also added the version number to `orvix status`'s output, it was already showing in the menu bar dropdown but the cli status check was the one place you'd actually go looking for it and it wasn't there.

## stuck mouse button when grab starts mid pinch (cycle 79)

`GestureInterpreter.process_hand()` checked `_grabbing` before `_pinching`, so if a pinch was already held down (`PINCH_DOWN` fired, mouse button physically down) when the hand finished closing into a full fist, grab strength could cross its threshold while pinch strength was still high too, since both curl together as part of a fist. grab took over and returned early without ever firing `PINCH_UP`, leaving the mouse button stuck down for the whole grab-scroll session. same "stuck button" bug family as the radial menu state leak fix, just reachable a different way. fixed by releasing any open pinch before starting a grab, plus a test reproducing the exact event sequence.

## coverage gap: releasing a drag that actually became a drag (cycle 81)

a coverage.py pass found `DRAGGING -> IDLE`'s pinch-release branch had zero test coverage, only the quick-click release path was tested. added a test for releasing after the pinch actually progressed to a full drag.

## orvix --help was cutting off 3 of 7 commands (cycle 83)

`bin/orvix`'s `--help` used `sed -n '3,9p'` to slice the usage comment block out of the script, but the block had grown to 12 lines since that range was written, so `calibrate`, `status`, and `profile` never showed up in the help text. widened the range to `3,12p`.

## more nothing-found cycles (84-86)

reviewed bin/orvix, scripts/build_app.sh, shortcuts.py, overlay.py, displays.py, one_euro_filter.py, README.md's cli section, re-read FEATURE_PLANS.md end to end (all 8 items done or explicitly deferred), checked exception handling in gui.py's PipelineWorker, checked overlay callbacks for main-thread dispatch, scanned for tautological test assertions, re-ran pyflakes, and reviewed the config sanitization chain added over cycles 75-77 for consistency. all clean. genuinely getting hard to find anything new at this point.

## quit not waiting for pipeline shutdown (cycle 90)

every pipeline-restart path in gui.py already called `worker.stop(wait=True)` on purpose, specifically so the background thread finishes closing the leapd websocket before a new pipeline starts. `_quit` called `worker.stop()` with no wait, so on actual app quit the cleanup could get killed mid-flight by process exit instead of closing the connection cleanly. matched it to the restart paths, added a test, `_quit` had zero coverage before this.

## positionless pinch/grab release events were getting dropped (cycle 93)

third stuck-mouse-button bug in this family. `GestureInterpreter.reset()` emits `PINCH_UP`/`GRAB_END` with `palm_position=None` when it force-closes a gesture that was mid-hold, like the radial menu closing on a hand that was still pinching. `main._dispatch()` bailed out on any event with `palm_position=None` before it ever reached the release logic, so the mouse button stayed physically stuck down even though the interpreter's own internal state correctly reset to idle. the original radial-menu-close fix only closed the interpreter's internal state leak, not this one. now positionless `PINCH_UP`/`GRAB_END` still fire their release action when the mapped action is `"click"`, since releasing a button doesn't need a screen position. checked afterward for any other event type that could carry `palm_position=None` the same way, only these two ever do and both are covered now.

## quitting mid-calibration used to just silently drop your sweep (cycle 98)

calibration holds its own leapd stream open on a daemon thread same as the pipeline worker, but unlike the pipeline there's no clean way to cancel a sweep that's already in flight. quitting the app while a calibration sweep was running used to just let the process kill the thread and drop whatever bounds you'd swept so far, zero warning. now `_quit` asks first if a calibration's still running.

## more nothing-found cycles (94-97, 99-101)

checked for other event types that could hit the same `palm_position=None` dispatch gap as cycle 93 (none), reviewed gesture interpreter/hand-lost reset paths, `ExtraGestures.reset_transient` and its call sites, `CircleDetector`'s self-trimming buffer, dwell/hold clicker resets, `mouse_control.py` in full, `coord_mapper.py`'s division-guarded math, `extra_gestures.py`'s detector classes and multi-action dispatch, and the terminal `orvix calibrate` flow's Ctrl-C handling (already correctly prints "cancelled, your old calibration is untouched" since `save_config` only ever runs once at the very end). also looked hard at a two-hand-zoom timing edge case where the primary hand's own pinch can register as a spurious click a frame before the second hand joins the zoom gesture; traced it through and confirmed it's not a stuck-button bug (the release still fires cleanly), just a momentary spurious click-and-hold, and decided a real fix would either regress single-hand click latency or isn't feasible, not worth the risk. all clean otherwise.

## more nothing-found cycles (102-103)

checked shortcuts.py's radial/named shortcut tables, config.py's radial_actions sanitization, and confirmed the wedge angle math in radial_menu.py and overlay.py both derive from `360.0 / len(actions)` dynamically, so hover highlighting stays aligned even after sanitization drops an entry. also reviewed leap_client.py's heartbeat/streaming logic and grepped for stray TODO markers. nothing found either time.

## two more calibration mutual-exclusion gaps (cycles 104-105)

same bug shape as the earlier stuck-button family, just for calibration's own state this time. `_toggle_running` in gui.py could start the live pipeline while a calibration sweep was still running, letting two threads both hold their own leapd stream open and race each other for `self._cal_tracker`/`self.settings.calibration`/`save_config` -- `_calibrate` already refused the reverse direction (starting a calibration while the pipeline is live) but nobody had guarded this side. added the missing guard.

digging into that turned up a second one: calibration finishes by writing to whatever `self.settings` currently points at (`self.settings.calibration = box; save_config(self.settings)`, read live at completion time, not captured when the thread started). loading a profile mid-sweep reassigns `self.settings` to a totally different object with no guard at all, so the calibration result would silently land on the newly loaded profile instead of the one actually being calibrated. fixed the same way, refuse the profile load while `self._calibrating`.

checked profile save-as and delete for the same class of bug afterward -- both are safe, neither reassigns `self.settings` identity, they just read/write it in place or touch the filesystem by name. also checked every other caller of `_restart_pipeline_if_running` (cursor mode, multi-monitor, extra gestures, radial dwell): all mutate settings in place too, so the identity-swap race really was specific to profile load. this bug family looks closed now.

## nothing found (cycle 106) + coverage artifacts weren't gitignored (cycle 109)

cycle 106 double-checked the calibration mutual-exclusion fixes from 104-105 had no more gaps, confirmed clean. cycle 108 checked main.py's argparse-based CLI entry point, config.py's profile name validation (already path-traversal safe), and the `orvix profile` benchmark subcommand -- all clean. cycle 109 noticed `.coverage`/`htmlcov/` were never gitignored, so any coverage.py pass (cycle 81 needed one and found a real gap) left an untracked artifact sitting in the repo. added them to .gitignore.

## dead target_fps field (cycle 113)

`Settings.target_fps` in config.py looked like a real knob for the pipeline's frame rate but nothing ever read `settings.target_fps` anywhere. perf.py has its own completely separate local `target_fps` parameter used only for benchmarking, unrelated to the Settings field. dropped the dead field and cleaned up two spots in FEATURE_PLANS.md that pointed at it as if it controlled the real frame budget. checked every other Settings field for the same kind of dead-config bug afterward (cycle 114), all of them are actually read somewhere, this was the only one.

## two more real bugs found live-tracing run_live (cycles 115-116)

`RadialMenu.open()` takes a `pinching` param specifically so a pinch already held on the opening frame can't get misread as an instant wedge pick, and it's already unit tested that way, but the actual call site in `run_live` never passed the real value, it always defaulted to False. if your hand was still lightly pinched right as the circle gesture finished, the very next `update()` read that as a brand new pinch edge and fired whatever wedge was under the dead zone before you ever saw the wheel. fixed to pass the real pinchStrength check, same threshold `_fire_radial` already uses every frame after that.

separately, `dry_run` turned out to be the one checkbox that didn't restart the running pipeline on change, unlike cursor mode/multi-monitor/extra gestures/dwell. `dry_run` only gets read once, at `worker.start()`, so flipping the checkbox mid-session did nothing to the live worker: you could check "dry run" and still be moving your real cursor, or uncheck it and stay stuck in dry-run. added the same restart-on-change fix as the other start-only settings.

checked every other gui.py checkbox/setter against this exact bug shape afterward (cycle 117): pinch/grab action, thumbs-up, cursor ring, radial toggle, extra gestures, dwell, fist strictness, cursor mode, multi-monitor all correctly either read live every frame (with a comment saying so) or already restart. dry-run was the only omission, and it's fixed now. this bug class looks closed.

## run_live was converting leapd errors to SystemExit too early (cycle 123)

calibration.py's own `run()` has a docstring explaining why the sync entry point, not the coroutine, has to be where `LeapConnectionError` becomes `SystemExit`: raising `SystemExit` while asyncio is still finalizing the leap stream's async generator turns a clean one line error into an unreadable traceback. `run_live` in main.py was doing the conversion itself, inside the coroutine, breaking that same rule. it also had a quieter side effect: gui.py's `PipelineWorker` has its own `except LeapConnectionError` handler meant to show the real connection error, but since run_live always handed it a `SystemExit` instead, that handler could never actually fire, the GUI just fell back to a generic message every time. moved the conversion out to `main()`'s CLI entry point so the CLI still gets the same clean exit(1), but the GUI now sees the real exception. added a regression test pinning run_live's propagation behavior.

checked handrender.py and handviz.py's own `except LeapConnectionError` handlers afterward (cycle 124) since they looked like the same shape -- both call `stream_latest_frames` directly rather than going through run_live, so they were never affected. also confirmed calibration.py's own coroutine never does the SystemExit conversion internally, only its sync `run()` does, so it was following its own documented rule correctly the whole time. this bug was isolated to run_live and looks fully closed now.

## circle detector kept a stale buffer through a two-hand zoom (cycle 126)

`circle.feed()` already gets skipped while a two-hand zoom is active (the `zoom_active` guard), but the buffer itself was never reset, just left frozen with whatever partial sweep it had going into the zoom. a two-hand zoom moves both hands a lot to change the pinch span, so the first point fed back in once zoom lets go is a big jump from wherever the hand was before zoom started. the winding calc treats that jump like any other step in the path, so it could count as real rotation and help trigger the radial menu even though no circle was ever actually drawn. reset the circle buffer for as long as zoom is active, same idea as the hand-loss reset a few lines up in the same loop.

## a few more clean checks, then a broken-config crash in the cli (cycles 128-130)

cycle 128 double checked bin/orvix's `require_leapd` gate on the `gui` command -- looked like it might stop the menu bar from ever showing up if leapd's down, but the real packaged `.app` (see setup.py) launches `orvix/gui.py` directly and never goes through bin/orvix at all, so this only affects terminal/dev use and is fine as is. cycle 129 cross checked readme's radial menu and thumbs-up docs against shortcuts.py's actual tables, version string consistency, and onboarding's welcome text, all still in sync.

cycle 130 found a real one: gui.py already wraps `load_config()`/`load_profile()` so a broken `~/.orvix/config.yaml` (invalid yaml, or valid yaml that isn't a mapping at the top level, like it got hand-edited into a list) falls back to defaults with a warning instead of crashing before the app can even show its menu bar icon. `run_live`'s cli path (`orvix cli`) had nothing catching this at all, same class of gap `_clamp_field`'s own comment already calls out for a bad *individual* field, just one level further up where that per-field guard never gets the chance to run. added the same fallback to the cli path plus a regression test.

## two more stale target_fps references (cycles 126-127)

the cycle 113 dead-field removal left a couple of comments behind that still talked about it like it was real. perf.py's module docstring said `target_fps=100` was an enforced budget on "the real pipeline," which hasn't been true since the field got deleted, and never actually was true even before that since nothing read `settings.target_fps`. gui.py had the same idea baked into a comment: "gesture events can fire at up to target_fps (100/s by default)" as if that were a config knob instead of just leapd's raw streaming rate. reworded both to describe what's actually true: there's no fps cap anywhere in the real pipeline, frames get processed as fast as leapd sends them. double checked the remaining `target_fps`/`100fps` hits in README.md, COMPETITIVE_RESEARCH.md, and calibration.py afterward, those are all legitimately talking about perf.py's own benchmark default or leapd's real hardware rate, not the removed field. this vein looks closed now.

## false alarm: orvix gui is a real subcommand (cycle 131, self-caught)

briefly "fixed" the readme's `orvix gui` references, thinking bin/orvix had no `gui` subcommand and the menu bar only launched via bare `orvix`. wrong -- bin/orvix's case statement explicitly handles `gui` (it's also just what `cmd` defaults to when you pass nothing), so `orvix gui` and bare `orvix` are both real and equivalent. cycle 128's own devlog entry a few cycles earlier had already established this same fact while checking `require_leapd`, so this was avoidable with a closer read. reverted the bad commit before it did any damage. no real bug found here, logging it so the next cycle doesn't repeat the same wrong assumption.

## the broken-config-yaml fallback gap, closed for good (cycles 130, 134-135)

cycle 130's `run_live` cli fix turned out to be one of four call sites, not the last one. `orvix calibrate`'s `_run_async` in calibration.py called `load_config()` with nothing catching it, same crash-before-you-even-see-an-error shape. fixed with the same fallback-to-defaults-plus-warning pattern. then found a fourth: `orvix/handrender.py` called `load_config().calibration` unconditionally at *module import time*, so a broken config would take down `orvix hand` before it drew a single frame -- worse than the other three since it happens before any of the app's own error handling even starts. extracted the load into a `_load_calibration()` helper with the same try/except, which also made it actually testable (PyObjC won't let you `importlib.reload` a module that redefines an NSView subclass, so a bare module-level call couldn't have been tested any other way). grepped every remaining `load_config(` call site afterward and confirmed all four (cli, gui, calibrate, hand) now handle it the same way. this gap looks fully closed.

## silent mid-session leapd drops, closed everywhere (cycles 141-143)

`run_live`'s `async for` loop over `stream_latest_frames()` only ever falls through with no exception in one documented case: leapd closing the websocket cleanly mid-session (see `stream_frames`' own docstring). a real user-requested stop always cancels the task instead and lands in the `CancelledError` branch. gui.py's `PipelineWorker._run_thread` had nothing distinguishing the two -- a clean mid-session drop showed the exact same "stopped" status as the user pressing Stop themselves, with zero indication tracking had actually died. added an `else` clause that only fires when the loop ends on its own, fixed the cli's `main()` entry point the same way (it had the same gap, just no `_run_thread`-style wrapper to have caught it earlier), then found the standalone visualizers (`handviz.py`, `handrender.py`) had their own copies of the same reader loop with the identical gap. all four leapd-stream consumers now warn/error correctly on a real disconnect instead of looking like a normal stop.

while fixing the visualizers' tests, noticed `_run_reader` in both `handviz.py` and `handrender.py` closed their asyncio loop with a bare `loop.close()` after the pump coroutine returned, same shape gui.py's own pipeline teardown already had to fix a while back: a `return` out of the `async for` loop (the user-stop path) doesn't close the generator it's iterating, so `stream_latest_frames`' background reader task and its leapd websocket were still alive when the loop got torn down out from under them -- silently leaking a connection every time the visualizer stopped. gave both the same drain-pending-tasks-then-`shutdown_asyncgens`-then-close sequence gui.py already uses. also fixed a test that produced a harmless but real "coroutine was never awaited" warning caused by the exact bug it was covering. grepped for any other bare `loop.close()` after that and confirmed the other three asyncio.run() call sites (calibration.py, main.py, gui.py's calibration path) already handle this internally, so the bug class is closed everywhere.

## unguarded osascript call could freeze the whole pipeline (cycle 148)

`set_volume_relative` in mouse_control.py runs synchronously right on the gesture dispatch path, same as every other frame's mouse/cursor work, but its `subprocess.run(["osascript", ...])` call had no timeout. if osascript ever blocked -- say on a macOS Automation permission prompt -- the entire real-time pipeline would freeze right along with it, not just that one volume nudge. same "unguarded blocking call on the hot path" class the leapd stream reads and calibration sweeps already got guarded against a long time ago, just one more call site that slipped through. added a 2 second timeout and now catch `TimeoutExpired` alongside the existing `OSError`. checked the rest of mouse_control.py, main.py, extra_gestures.py, gesture_interpreter.py, and shortcuts.py for other subprocess/blocking-I/O calls on the dispatch path afterward -- this was the only one, everything else in the hot path is pure Quartz `CGEventPost` calls with no blocking risk. this bug class looks closed now too.

## orvix cli had no ctrl-c handling (cycle 151)

`calibration.run()` already catches `KeyboardInterrupt` and exits clean with a "cancelled" message and code 130, but `orvix cli`'s `main()` had nothing around `asyncio.run(run_live(...))` -- pressing ctrl-c mid-session dumped a raw traceback instead of the same polished stop message calibrate already had. fixed to match. checked `orvix viz`/`orvix hand` afterward, both already wrap their `app.run()` in the same handling; `orvix status`'s bash probe already degrades gracefully on any nonzero exit; `orvix profile` is a bounded synthetic benchmark with no interactive wait, so it doesn't need the same treatment. this gap is closed everywhere it actually matters.

## radial wheel was caching a stale screen height (cycle 154)

`OverlayController` read `NSScreen.mainScreen()`'s height once in `_ensure_window()`, which only runs the first time the wheel's window gets created, then reused that cached value forever after in `_show()`'s quartz-to-cocoa y flip. if the main display changed or its resolution changed later in a long-running session, the wheel would keep flipping against the old height, silently misplacing itself vertically for the rest of the session. same bug class as `displays.py`'s desktop bounds going stale across a monitor plug/unplug, just never closed here. fixed by reading `mainScreen` fresh on every `_show()` call instead of caching it.

checked `handviz.py`/`handrender.py` afterward for the same pattern (cycle 155) -- both cache a full-screen window's geometry once at creation too, but judged it not worth touching: those are short, standalone diagnostic tools you run for one focused session, not the persistent menu bar app, and a real fix there would mean window recreation/resize logic, not just a re-read. also checked `CalibrationOverlayController` (cycle 156) -- it only uses `mainScreen` once to park the HUD's window position at creation, not as a per-frame value for coordinate math, so a display change mid-sweep would just leave the HUD parked in its original spot during a short bounded calibration session, not the same silent-misplacement bug. left both alone.

## radial wheel's opening center went stale too (cycle 157)

same staleness family as the overlay.py screen height bug, one more spot. `run_live`'s bounds-recheck block already refreshes `screen_width`/`screen_height`/`screen_origin` and calls `mapper.update_screen_bounds` when a monitor gets plugged/unplugged mid session, but `desktop` (the `DesktopBounds` object whose `.center` is where the radial wheel always pops open) only ever got set once at startup and never touched again by that block. after a real bounds change the wheel would keep opening dead center of the old desktop layout instead of the new one. one line fix, `desktop = fresh` right alongside the mapper refresh. didn't add a `run_live`-level regression test since `run_live` still has no test harness (cycles 121/155 already decided against building one from scratch just for this), and the new line is a direct parallel to the already-tested line right above it. cycle 158 reread the whole bounds-recheck block afterward looking for any other variable that might've slipped through the same way, came up clean -- this looks closed now.

## the finally block was quietly erasing error status (cycle 160)

this one had been sitting there the whole time cycles 141-143 were closing the "mid-session leapd drop looks like a normal stop" bug family. `_show_error` already sets its own persistent icon and status text (and pops a one-time alert), but `PipelineWorker._run_thread`'s `finally` block called `self._on_status("stopped")` unconditionally right after every error path -- `LeapConnectionError`, `SystemExit`, a generic crash, and the mid-session drop case cycles 141-143 just added. the instant you dismissed the alert, the menu bar silently reset itself back to a normal-looking idle state, erasing the only lasting trace that anything had gone wrong. same "error looks identical to a stop" bug, just one layer further down than where those cycles were looking. fixed with an `errored` flag that skips the trailing status update on any error path. cycle 161 traced the rest of the error-status lifecycle afterward (restart-after-error correctly clears it via `_on_status("running")` at thread start, `_quit` on an already-errored worker correctly no-ops, and calibration's own error path already gets the right final state for unrelated reasons -- its two `performSelectorOnMainThread` dispatches happen to queue in the right order) and found nothing else needing the same fix.

## competitive research doc went stale too (cycle 163)

same "doc never got updated after shipping" pattern this loop keeps finding, just in a different file this time. `docs/COMPETITIVE_RESEARCH.md`'s "suggested next features" section still listed proportional scroll/volume rate, named config profiles, the cursor highlight ring, single-hand enforcement, and the drift investigation as open TODOs, but `FEATURE_PLANS.md` already has all five marked DONE and they've all been shipped for a while now. annotated each one with DONE and a pointer to `FEATURE_PLANS.md`, left the still-open items (adaptive acceleration curve, target-aware click assistance) and the deprioritized ones (target-snapping, voice trigger) alone.

## dwell_radius_px was never actually pixels (cycle 166)

`_DwellClicker`/`ExtraGestures`'s `radius_px`/`dwell_radius_px` params had a `_px` suffix but always got fed `settings.dwell_click_radius_mm`, a raw unmapped Leap-space mm value, since dwell arms off the raw palm position rather than the mapped cursor position. there was even an inline comment acknowledging the mismatch instead of just fixing the name. renamed to `dwell_radius_mm` everywhere (`extra_gestures.py`, `main.py`, tests) and fixed `HandSignals.hover_point`'s docstring, which made the same mistake calling it a "screen-space cursor point." checked for the same "unit suffix lies about what's actually stored" bug elsewhere in config.py/coord_mapper.py/calibration.py afterward -- radial_dead_zone_px (genuinely mapped screen px), zoom_step_mm/two_hand_pinch_span (genuinely raw mm), volume_step_deg (converted to radians right at the call site), min_hand_height_mm, and coord_mapper's own `x_mm`/`dx_mm` locals all check out, this was an isolated case.

## zoom_step_mm/volume_step_deg could hang the whole dispatch thread (cycle 168)

genuinely dangerous one, not just a cosmetic clamp. `_ZoomDetector` and `_VolumeTwistDetector` in `extra_gestures.py` both drain a residual with `while self._resid >= self._step: ... self._resid -= self._step` (and the mirrored `<=`/`+=` for the negative direction). neither `zoom_step_mm` nor `volume_step_deg` was in any of config.py's clamp tuples, so a hand-edited `config.yaml` setting either to `0` (or negative) sailed straight through `_sanitize_settings` untouched. the moment an actual two-hand zoom or volume-twist gesture fired after that, `self._resid -= 0` never gets `self._resid` back under `self._step`, so the `while` spins forever -- froze the entire gesture dispatch thread with no recovery short of a force-quit. confirmed it with a live repro (a 2-second `SIGALRM` timeout around a fed `_ZoomDetector(0.0)` actually fired) before touching anything. fixed by adding both fields to a new `_POSITIVE_STEP_FIELDS` clamp tuple with a floor of 0.1 -- unlike the seconds fields, 0 itself is the dangerous value here, not just negative, so it needed its own floor above zero instead of reusing the existing nonnegative-seconds pattern. added a regression test and reran the repro to confirm the clamp actually closes the hole end to end through `load_config`.

checked afterward (cycle 169) for any other `while` loop anywhere in the codebase with the same "config-derived condition that can fail to advance" shape -- `circle_detector.py`'s buffer-trim loop is bounded by deque length regardless of its window value, `leap_client.py`'s loops are all await-gated on module constants not config fields, and `calibration.py`'s wait loops are already wall-clock/timeout-bounded from earlier fixes. this bug class is closed everywhere else.

## four field-ordering bugs, none of them crash (cycles 170-173)

a new bug family: several pairs of config fields have a documented "A must stay below/above B" relationship that nothing actually enforced, so a hand-edited (or just fat-fingered) config.yaml could silently break the feature the relationship exists for, no crash, no warning, just a feature that quietly stops working right.

- **pinch/grab release threshold (170):** `pinch_release_threshold`/`grab_release_threshold`'s own docstrings say they need to stay below their trigger threshold or the hysteresis does nothing. swapped or equal, `gesture_interpreter.py`'s DOWN state sees "released" on the very next frame after "started" -- a click or grab that fires but can never hold or drag. clamped release back to threshold minus a small margin instead of picking an arbitrary default, so it still respects how tight the user set the trigger.
- **calibration axis order (171):** `coord_mapper.py`'s `_map_range` only guards the equal-min-max degenerate case, not a swapped one -- an inverted `x_min`/`x_max` (or y/z) just makes `t` come out negative and mirrors that axis's cursor movement with nothing to explain why. swapped min/max back into order rather than picking a default, since both are presumably real measurements just recorded backwards.
- **tilt_full vs tilt_deadzone (172):** `TiltCoordMapper._deflection`'s `span = max(1e-6, tilt_full - tilt_deadzone)` only guards divide-by-zero, not `tilt_deadzone >= tilt_full` -- span floors at 1e-6 and the deflection calc explodes, turning tilt mode's smooth speed ramp into an instant snap to max speed right at the deadzone edge. pushed `tilt_full` above `tilt_deadzone` instead, left deadzone alone since it's tied to the sensor's measured noise floor.
- **pinch_freeze_threshold (173):** `_freezing_for_click` only holds the cursor still while `pinch_state` is IDLE and `pinch_strength >= pinch_freeze_threshold`. if `pinch_freeze_threshold >= pinch_threshold`, the real pinch always fires first and moves state out of IDLE before freeze_threshold is ever reached, so the anti-drift freeze silently never engages. pushed it below `pinch_threshold`, left the `<= 0` opt-out sentinel alone since `_freezing_for_click` already treats that as intentional.

## two more clamp gaps, one of them an actual crash (cycles 175-176)

- **radial_dead_zone_px (175):** never showed up in any clamp tuple at all, unlike its sibling radial fields. the wheel's window is only 460px (`overlay.py`'s `_BOX`) centered on the pointer, so a huge hand-edited value makes `_wedge_at()` never return a hovered wedge -- every pinch dismisses the wheel instead of selecting a wedge, and dwell can never accumulate. a negative value doesn't crash but silently removes the dead zone that exists on purpose. clamped to `[0, 200]`.
- **one_euro_min_cutoff / one_euro_beta (176):** this one's a real crash, not just silent misbehavior. `one_euro_filter.py`'s `_smoothing_factor` computes `r / (r + 1)` where `r = 2*pi*cutoff*t_e` and `cutoff = one_euro_min_cutoff + one_euro_beta * abs(dx_hat)`. a negative enough `min_cutoff` drives `r` to exactly `-1` on some frame, a `ZeroDivisionError` that crashes the whole gesture dispatch thread, confirmed by direct calculation before touching anything. floored `min_cutoff` at `0.01` (not just non-negative, since exactly `0` freezes the cursor in place forever at rest) and `beta` at `0.0`, which alone guarantees `cutoff` can never dip back under the floored `min_cutoff`.

swept the rest of the Settings fields afterward (`min_hand_height_mm`, `relative_min/max_gain`, `tilt_center_x/z`, `tilt_max_speed`, `grab_fist_max_extended`, `radial_open_sweep_deg`/`min_radius_mm`, `volume_rate_slow/fast_deg_s`, `dwell_click_radius_mm`) -- none of them have the same crash-risk shape, worst case is degraded feel not a raised exception, so left them alone. also re-checked coord_mapper.py/extra_gestures.py/circle_detector.py/gesture_interpreter.py for any other config-derived division that could land on a singular value the same way `one_euro_min_cutoff` did -- everything else is already guarded (`_map_range`'s equal-min-max guard, `_gain`/`_deflection`'s `1e-6` floors, `map_to_screen`'s `dt <= 0` check, `scaled_volume_percent`'s early return, `_DwellClicker`'s `dwell <= 0` return, `CircleDetector`'s `min_points` being a hardcoded constructor default never exposed through config). this bug class is closed too.

cycle 173 also did an exhaustive re-read of every Settings field looking for more of these and ruled out two false leads: `relative_slow_speed`/`relative_fast_speed` and `volume_rate_slow_deg_s`/`volume_rate_fast_deg_s` both already have an explicit early-return guard at their one call site if misordered, so they were already safe by construction, not actually missing anything. this vein looks genuinely mined out now.

## tilt mode's neutral read only ever happened in the terminal flow

different flavor of the recurring "cli and gui quietly do different things" bug family. `calibration.py`'s terminal flow (`_run_async`) does the sweep, then always follows up with `collect_neutral_tilt` to measure where your hand actually rests flat and store it as `tilt_center_x`/`tilt_center_z` -- the docstring is blunt about why, a real hand measured x=-0.165 holding "flat," so without centering that in, tilt mode creeps sideways on its own forever. but `calibration.calibrate()`, the shared pure-logic function both flows are supposed to funnel through, never touched tilt at all -- that step was only ever wired into the terminal script directly. `gui.py`'s `_run_calibration` calls `calibration.calibrate()` and nothing else, so anyone who calibrates through the menu bar (which is most people, since that's the default `orvix` command) never gets tilt_center_x/z set. they'd sit at the 0.0/0.0 default forever, no error, no warning, tilt mode just always drifts a little for those users, and there'd be no way to tell from the app that anything was missing.

fixed by adding the same neutral-tilt read to `_run_calibration` right after the sweep, best-effort same as the terminal flow: if it fails (bad read, leap drops between the two calls) the sweep result is still kept and saved rather than throwing the whole calibration away. added a status line ("hold your hand flat for tilt calibration...") so the menu bar doesn't look frozen during the extra few seconds, same reasoning the existing "waiting for your hand" message already covers for the sweep's own dead time. turned out there was no test at all for the success path of `_run_calibration` before this, both existing tests only covered error handling, so added one for the happy path plus one for the "tilt read fails but sweep is still kept" case.

## dwell_click_radius_mm slipped through the same net

cycle 176's clamp sweep listed `dwell_click_radius_mm` as one of the fields checked and cleared, but that sweep was only looking for crash-risk divisions -- it wasn't looking for the silent-misbehavior shape from the field-ordering bugs a few entries up, and this one has that shape. `_DwellClicker.feed` re-arms the hover timer the instant `math.dist(point, anchor) > self._radius`, so a zero or negative `dwell_click_radius_mm` (hand-edited config, or just a typo) means basically any hand tremor counts as drifting off the anchor. the dwell timer resets before it can ever reach `dwell_click_seconds`, so hover-to-click silently stops firing forever, no error, nothing in the logs. too large a value has the opposite problem, tolerating so much drift that a click could fire off movement that was never meant to be a deliberate hold. floored at `0.5` and capped at `500.0`, same shape as `radial_open_min_radius_mm`'s bounds.
