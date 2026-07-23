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
