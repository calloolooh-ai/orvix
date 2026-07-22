# Feature Plans

Architecture plans for the 8 candidate features from [`COMPETITIVE_RESEARCH.md`](./COMPETITIVE_RESEARCH.md) section 5, in the same priority order. Each plan is grounded in the actual pipeline: `leap_client.py` (websocket -> frame dict) -> `gesture_interpreter.py` (frame -> `GestureEvent`) -> `coord_mapper.py` (palm position -> screen px) -> `mouse_control.py` (Quartz `CGEventPost`), with `extra_gestures.py` running the five side-gestures off raw per-frame signals and `main.py::run_live()` wiring it all together per frame.

---

## 1. Proportional scroll/volume rate tied to gesture magnitude

**Goal:** make grab-scroll speed and fist-twist-volume speed scale continuously with how fast/hard the gesture is being done, instead of firing fixed-size steps.

**Files touched:**
- `orvix/extra_gestures.py` — `_VolumeTwistDetector.feed()` currently accumulates `roll` delta into `self._resid` and fires one `VOLUME_UP`/`VOLUME_DOWN` action per fixed `volume_step_rad` crossed (extra_gestures.py:129-148). Change to emit a magnitude alongside the action, or fire more frequently with a smaller effective step when twist *speed* (rad/s, derivable from `delta / dt` — needs `now` passed into `feed`) is high.
- `orvix/gesture_interpreter.py` — `GRAB_SCROLL` events already carry `palm_velocity` (gesture_interpreter.py:172), so this one is actually mostly wired already; the gap is in how `main.py` turns velocity into scroll units.
- `orvix/main.py` — wherever `GRAB_SCROLL` is dispatched to `mouse.scroll(dx, dy)` and `VOLUME_UP`/`VOLUME_DOWN` to `mouse.set_volume_relative(...)`, change the fixed per-event delta to a value derived from velocity/twist-rate, clamped to a min/max.
- `orvix/config.py` — new tuning fields near `volume_step_percent`/`zoom_step_mm`.

**Data flow / design:** for scroll, `palm_velocity` from `GestureEvent` (already present on `GRAB_SCROLL`) can drive `scroll(dx, dy)` magnitude directly via a mapping function like `coord_mapper._map_range`, clamped to `[scroll_min_units, scroll_max_units]`. For volume, `_VolumeTwistDetector` would need `feed(roll, now)` (adding `now`, mirroring `_DwellClicker`'s signature) to compute twist angular velocity, then scale `set_volume_relative`'s `delta_percent` argument instead of firing a fixed `volume_step_percent` per crossed step.

**Config changes:** add `scroll_min_units: int`, `scroll_max_units: int`, `scroll_slow_speed`/`scroll_fast_speed` (mm/s, mirroring `relative_min_gain`/`relative_max_gain`'s shape in config.py:105-108), and `volume_min_percent`/`volume_max_percent` per twist-rate tier.

**GUI changes:** none required initially — it's a tuning change to existing gestures, not a new toggle. Could add sliders later, but a first cut just changes the fixed-step math.

**Risks / open questions:** grab-scroll's `palm_velocity` includes all 3 axes; need to decide whether horizontal scroll should also scale or stay fixed. Twist velocity from `roll_from_normal` (extra_gestures.py:43-51) is derived from `atan2`, which is noisy near the wrap seam — the existing delta-wrapping logic (extra_gestures.py:138) needs to survive a velocity-based approach without divide-by-near-zero-dt spikes.

**Effort:** S–M.

---

## 2. Named config profiles, swappable from the menu bar

**Goal:** let a user save/name multiple full `Settings` states (e.g. "demo", "precision") and switch between them from the menu bar without hand-editing YAML.

**Files touched:**
- `orvix/config.py` — `load_config`/`save_config` currently hardcode `DEFAULT_CONFIG_PATH = ~/.orvix/config.yaml` (config.py:23, 294-316). Add `list_profiles()`, `load_profile(name)`, `save_profile(settings, name)` that operate on `~/.orvix/profiles/<name>.yaml`, plus an `active_profile` pointer (could be a small `~/.orvix/active_profile.txt` or a key in the main config).
- `orvix/gui.py` — `OrvixApp.__init__` (gui.py:227) builds the menu; needs a new submenu populated from `config.list_profiles()`, with a handler shaped like the existing `_make_mode_setter`/`_make_action_setter` pattern (gui.py:384, 415) that calls `load_profile(name)`, applies it to the running `PipelineWorker`, and restarts the pipeline (mirrors what `_toggle_multi_monitor` (gui.py:403) already does to apply a settings change live).
- `orvix/main.py` — no pipeline changes needed; `run_live()` already accepts a `settings` object (main.py:309), so switching profiles is just "stop, reload settings from the new path, restart" using the existing `PipelineWorker.stop()`/`start()` (gui.py:159, 151).

**Data flow / design:** this is purely a config-loading concern, doesn't touch the frame pipeline at all. The existing `load_config(path)` already accepts an arbitrary `Path` (config.py:294), so profile files just need a naming convention and a directory listing helper; `PipelineWorker.start(settings, dry_run)` (gui.py:151) already takes a fresh `Settings` object per (re)start, so profile-switching is "load a different `Settings`, restart worker."

**Config changes:** new directory `~/.orvix/profiles/`. Each profile file is a full `Settings` dump, same shape `save_config` already produces (config.py:310-316) — no schema change needed, just a new save location.

**GUI changes:** new "Profiles" submenu: list of profile names (checkmark on active, same pattern as `_refresh_action_checkmarks` (gui.py:487)), plus "Save current as..." (needs a text-input prompt — `rumps.Window` supports this) and maybe "Delete profile."

**Risks / open questions:** switching profiles mid-session means restarting `run_live()`, which drops in-flight gesture state (fine, same as toggling multi-monitor already does). Decide whether calibration (`CalibrationBox`) should be per-profile or shared — probably per-profile, since "demo" vs "daily" may run on different desk setups.

**Effort:** S (config plumbing is small; most of the work is the `rumps` menu/prompt UI).

---

## 3. Optional on-screen cursor highlight ring for normal use

**Goal:** an always-available toggle that shows a subtle ring around the real cursor during normal operation, not just during dwell-click countdown.

**Files touched:**
- `orvix/overlay.py` — **this already exists for dwell**: `DwellRingController` (overlay.py:302) and its `_RingView` (overlay.py:258) draw "a thin progress ring, filling clockwise from the top" around the live cursor, driven by `render(progress: float | None)` (overlay.py:314). This is the natural mechanism to extend rather than build new.
- `orvix/main.py` — `on_dwell` callback (main.py:312, 404-411) currently only fires `DwellRingController.render()` from `extras.dwell_progress`. A general-purpose ring would need a second, always-on render path (e.g. a static low-opacity ring at `progress=1.0` or a distinct visual state) alongside the existing dwell-progress one, since they'd otherwise fight over the same window/view.
- `orvix/config.py` — new `cursor_ring_enabled: bool` near the other extra-gesture toggles (config.py:223-227).
- `orvix/gui.py` — new toggle in the same family as `_make_extra_toggle` (gui.py:442).

**Data flow / design:** cheapest approach: give `DwellRingController` a second mode/method (`render_idle_marker()` or a `mode` param on `render()`) that draws a faint always-on ring at the current cursor position when `cursor_ring_enabled` is on and dwell isn't actively counting down, falling back to the existing progress-ring behavior when dwell *is* active. `main.py`'s per-frame loop (main.py:402-411) already knows the cursor position implicitly via `mapper`; it would need to call the ring controller every frame instead of only when `dwell_progress > 0`.

**Config changes:** `cursor_ring_enabled: bool = False` (default off since it's pure polish, matches the report's framing as an optional toggle).

**GUI changes:** one checkbox menu item, e.g. under "More gestures" or a new "Display" section.

**Risks / open questions:** running an always-on `NSView`/overlay window at 100fps target (`target_fps: int = 100`, config.py:278) has a real CPU/compositing cost that the dwell ring only pays transiently today — worth a quick pass through `orvix profile` (perf.py) or at least a manual CPU check before shipping as default-on. Also needs to coexist visually with the radial menu overlay (`OverlayController`, overlay.py:180) without both drawing at once.

**Effort:** S (mechanism already exists; mostly wiring + a lower-cost render path).

---

## 4. Adaptive velocity-based acceleration curve for relative mode

**Goal:** scale relative-mode cursor gain based on live hand velocity, matching what CursorViaCam does for cursor acceleration.

**Important finding:** **this substantially already exists.** `RelativeCoordMapper._gain(speed_mm_s)` (coord_mapper.py:173-185) already computes a linear gain ramp between `relative_min_gain` and `relative_max_gain` based on current frame-to-frame speed (`relative_slow_speed`/`relative_fast_speed`, config.py:105-108), applied fresh every frame in `map_to_screen()` (coord_mapper.py:210-220). So the "adaptive acceleration" gap identified against CursorViaCam is really about curve *shape* and *tuning*, not a missing mechanism.

**Files touched:**
- `orvix/coord_mapper.py` — `RelativeCoordMapper._gain()` (coord_mapper.py:173) is the only function that would change. Currently strictly linear between the two speed thresholds; could add an easing option (e.g. quadratic/exponential ramp) for a more "fine control at rest, big jumps when fast" feel, since CursorViaCam's pitch is specifically about that curve shape, not just clamped linear interpolation.
- `orvix/config.py` — optionally add a `relative_gain_curve: str = "linear"` (`"linear"` | `"quadratic"`) setting if curve shape becomes configurable, or a smoothing window if the complaint is really about *jumpiness in speed estimation* rather than gain shape (speed is currently derived from one frame's delta at coord_mapper.py:215, no smoothing beyond what the One Euro Filter already applies to position).
- `orvix/perf.py` — `orvix profile` already exists to benchmark One Euro Filter beta tradeoffs; extending it to plot px-of-cursor-movement vs. hand-speed for a candidate gain curve would let this be tuned synthetically (matching the report's suggestion to validate via `orvix profile`-style benchmarking) before live testing.

**Data flow / design:** no new data flow — `_gain()` already sits exactly where an adaptive curve would live, fed by `speed = math.hypot(dx_mm, dy_mm) / dt` (coord_mapper.py:215) computed fresh each frame from the (already-filtered) position delta.

**Config changes:** none required for a pure curve-shape tweak; add `relative_gain_curve` only if multiple curve shapes should be user-selectable.

**GUI changes:** none needed unless curve shape becomes a menu option.

**Risks / open questions:** Leap's depth tracking is noisier than a trackpad's optical sensor, so a more aggressive (non-linear) acceleration curve risks amplifying tracking jitter into visible cursor jumps — needs real-hardware testing, not just synthetic `orvix profile` numbers. Given the mechanism already exists and works, this is lower priority than the report implies; mostly worth a tuning pass rather than new engineering.

**Effort:** S (tuning), not a new subsystem.

---

## 5. Defensive single-hand-only enforcement

**Goal:** make sure a bystander's hand entering the Leap's field can't hijack tracking from the user's hand.

**Important finding:** **`pick_hand()` already does most of this.** `leap_client.pick_hand(frame, preferred_hand)` (leap_client.py:222-244) only ever returns a single hand from `frame["hands"]`, and when `preferred_hand` is `"left"` or `"right"` it explicitly refuses to fall back to a different hand if the preferred one isn't present (leap_client.py:227-231, "we still return None rather than silently falling back"). The real gap is narrower than the report framed it: it's specifically the `preferred_hand == "first"` path (leap_client.py:237-238), which returns `hands[0]` unconditionally — if the user's hand drops out of view for one frame while a second hand (bystander, or the user's own other hand) is present, "first" mode could silently swap tracked hands.

**Files touched:**
- `orvix/leap_client.py` — `pick_hand()` is the only function that needs to change. Add hand-identity continuity: Leap's frame hand dicts carry a stable per-hand `id` field from the SDK; track the last-used hand's `id` and prefer continuing to track that `id` if it's still present in `frame["hands"]`, even in `"first"` mode, only falling back to `hands[0]` when the previously-tracked id is truly gone.
- `orvix/gesture_interpreter.py` — no change needed; it already only ever receives one hand dict per call (gesture_interpreter.py:117-121), so it has no visibility into "how many hands were in frame" today, which is fine since the fix belongs in `pick_hand`.

**Data flow / design:** `pick_hand` would need to become stateful (an instance held across frames, or a module-level "last id" passed in/out) rather than the current pure function — a small but real shape change, since right now it's called fresh each frame in `main.py:368` with no memory between calls.

**Config changes:** none strictly required; could add `preferred_hand: "lock_first"` as a new mode distinct from today's `"first"`, defaulting existing users to unchanged behavior.

**GUI changes:** none needed unless exposing the new lock behavior as a menu choice alongside the existing hand-preference setting (if one exists in the menu — not confirmed in `gui.py`'s scan; likely config-only today).

**Risks / open questions:** given the Leap's narrow sensing cone (a pyramid above the small device, not a room-scale webcam FOV), a second hand entering frame at all is a much smaller real-world risk than the report's webcam-project comparison (`handTrack`) implies — this is genuinely low priority. Worth doing cheaply (id-continuity in `pick_hand`) but not worth much design time beyond that.

**Effort:** S.

---

## 6. Investigate session-long drift in relative/tilt mode — **DONE (investigated, no correction needed)**

**Finding (cycle 6):** read `RelativeCoordMapper` end to end (coord_mapper.py:187-225) and confirmed the drift-relevant math directly instead of guessing. The filter-then-difference approach (`one_euro_filter.py`'s `_exponential_smoothing` is a plain convex combination, `a*x + (1-a)*x_prev`) introduces no directional bias — it converges toward the true signal, it doesn't skew it. `self._x`/`self._y` accumulate as floats every frame with no intermediate rounding (only the final `int(round(...))` on return), so there's no compounding rounding error either. The existing `test_cursor_doesnt_drift_while_hand_is_held_still` in `tests/test_relative_mapper.py` already pinned this for a ~5.3s session (400 frames); this cycle added `test_cursor_doesnt_drift_over_a_long_session`, the same zero-mean-noise scenario run for 8000 frames (~1.8 minutes at 75fps, 20x longer) — drift stays under the same 40px bound rather than growing with frame count, which is exactly what you'd see if there were no systematic bias to compound. Both tests pass.

**Conclusion:** orvix's relative-mode math has no built-in session-length drift. Any real-world drift a user reports would come from actual Leap Motion sensor bias (a hardware property), not this code — so building a CursorViaCam-style automatic drift-correction heuristic isn't justified without a real bug report backing it. `TiltCoordMapper` already has its own drift mitigation shipped (not this cycle's work, but confirmed while reading the file): `calibration.py:364-367` derives `tilt_center_x`/`tilt_center_z` from a real calibration sweep and warns if the derived center falls outside `tilt_deadzone`, which is the same problem CursorViaCam's correction targets, just solved via one-time calibration instead of live correction.

**Original speculative plan below, kept for reference — superseded by the finding above:**

**Goal:** determine whether `RelativeCoordMapper`/`TiltCoordMapper` actually accumulate cursor drift over a long session before building any correction mechanism.

**Files touched (investigation only, no shipping code yet):**
- `orvix/coord_mapper.py` — `RelativeCoordMapper.map_to_screen()` (coord_mapper.py:187-225) integrates `dx_mm * gain` into `self._x`/`self._y` every frame; any one-euro-filter residual bias or systematic noise asymmetry would show up as slow creep here, since nothing re-anchors position except `reset()` on hand-loss (coord_mapper.py:227-235). `TiltCoordMapper.map_to_screen_tilt()` (coord_mapper.py:287-320) similarly integrates `deflection * speed * dt` continuously — a mis-centered `tilt_center_x`/`tilt_center_z` (config.py:120-121) would show up as one-directional creep even with the hand "flat."
- `orvix/perf.py` — the existing synthetic profiling tool is the right home for a new investigation: feed a long synthetic sequence of a "held still" hand (small simulated jitter, matching real measured noise — the codebase already has real numbers, e.g. "tilt palm normal noisy about ±0.1 measured while holding still", config.py:125) through `RelativeCoordMapper`/`TiltCoordMapper` for a simulated multi-minute session and log final cursor displacement from start.

**Data flow / design:** this is a measurement task, not a feature. Add a new `orvix profile --drift` (or a standalone script under `scripts/`) that runs `RelativeCoordMapper`/`TiltCoordMapper` against a fixture of "stationary hand with realistic noise" frames for a simulated N minutes and reports net (x, y) displacement. Compare against a threshold (e.g. "more than a few px/minute is worth fixing").

**Config changes:** none — this is diagnostic tooling, not a shipped feature.

**GUI changes:** none.

**Risks / open questions:** the report's own framing already flags this as speculative — "may not actually be a problem for orvix's filtering" given the One Euro Filter is explicitly tuned to avoid exactly this kind of accumulated noise. Don't build a drift-correction mechanism (à la CursorViaCam) until this investigation produces a real number showing it's needed.

**Effort:** S (a synthetic benchmark script, reusing `perf.py`'s existing patterns).

---

## 7. Target-aware click assistance *(speculative — likely skip)*

**Goal (as scoped by CursorViaCam):** give the cursor a temporary "magnetic" pull toward nearby clickable UI elements to help precision, as an alternative/complement to orvix's freeze-on-pinch approach.

**Files touched (sketch only):**
- Would require a wholly new module, e.g. `orvix/target_snapping.py`, using macOS's `AXUIElement` Accessibility API (via `pyobjc`'s `ApplicationServices`/`Accessibility` bindings, not currently a dependency — check `requirements.txt`) to query clickable elements near the cursor from the frontmost app.
- `orvix/coord_mapper.py` — any mapper's final `(x, y)` output would need a post-process "snap toward nearest target within N px" step before being handed to `mouse_control.move()`.
- `orvix/main.py` — would need per-frame AX queries, which are synchronous, cross-process, and app-dependent in latency — a real risk to the 100fps `target_fps` budget (config.py:278) that `orvix profile` currently benchmarks.

**Data flow / design:** `AXUIElementCopyElementAtPosition` (or similar) at roughly the cursor's screen position, per frame or throttled (e.g. every N frames to bound cost), then bias the mapper's output toward the nearest actionable element's bounds if within some `snap_radius_px`.

**Config changes:** `target_snapping_enabled: bool`, `snap_radius_px`, if pursued.

**GUI changes:** a toggle, if pursued.

**Risks / open questions:** this is the one item in the report explicitly flagged as high-cost/low-priority, and the codebase investigation confirms why — it's the only feature here that would need an entirely new API surface (Accessibility tree introspection) with per-app variability and real per-frame latency risk, versus every other item which extends code that already exists. It also duplicates the *problem* orvix already solves differently via `pinch_freeze_threshold` (config.py:142-156). **Recommendation: skip unless real usage shows freeze-on-pinch is insufficient for small UI targets.**

**Effort:** L.

---

## 8. Voice trigger for pause/resume or calibrate *(optional/low priority)*

**Goal (narrow scope only):** a couple of spoken trigger phrases (e.g. "orvix pause", "orvix calibrate") as an alternate input channel, not a full voice assistant.

**Files touched (sketch only):**
- New module, e.g. `orvix/voice_trigger.py`, wrapping macOS's on-device speech recognition (`Speech` framework via `pyobjc`, or `SFSpeechRecognizer` — not currently a dependency).
- `orvix/extra_gestures.py` — `ExtraGestures.paused` (extra_gestures.py:245) and the existing `_HoldToggle`-driven pause mechanism (extra_gestures.py:185-207, wired to `PAUSE_ON`/`PAUSE_OFF` in `observe()`, extra_gestures.py:267-269) is the natural integration point: a voice trigger would just call the same toggle path the palms-out gesture already calls, so `main.py`'s pause handling (main.py:413-414) needs no change at all.
- `orvix/gui.py` — a toggle to enable/disable voice listening, plus a menu bar indicator for "listening" state.

**Data flow / design:** voice recognition would run on its own thread/callback, independent of the Leap frame loop, and just needs to call into the same `ExtraGestures` pause-toggle path (or a new `interpreter.set_paused(bool)` if a cleaner seam is wanted) rather than becoming part of the per-frame gesture pipeline at all — this keeps the frame loop untouched and the two input paths (hand gestures vs. voice) fully decoupled.

**Config changes:** `voice_trigger_enabled: bool = False`, plus configurable trigger phrases if pursued.

**GUI changes:** one toggle, one status indicator.

**Risks / open questions:** the report itself flags this as cutting against orvix's core "hands only, nothing else" positioning — a hands-free trigger for pause/calibrate is arguably useful (e.g. calibrate can't be triggered by a gesture that depends on calibration being right yet), but scope creep risk is real if this becomes a wedge for more voice features later. **Recommendation: only build if pause/calibrate genuinely need a non-gesture escape hatch in practice; keep trigger vocabulary tiny by design.**

**Effort:** M (mostly the speech-framework integration; the pause hookup itself is trivial given the existing toggle path).
