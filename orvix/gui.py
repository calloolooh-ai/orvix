"""
gui.py

menu bar app (rumps/Cocoa) for controlling orvix without a terminal. lives
in the macOS menu bar, lets you start/stop the live gesture pipeline,
toggle dry-run, see hand/gesture status live, remap what pinch and grab
actually do, and kick off calibration.

usage:
    python -m orvix.gui

the actual gesture pipeline (leap_client -> gesture_interpreter ->
coord_mapper -> mouse_control) is untouched by this file, run_live() in
main.py is the single source of truth for "what happens on a gesture
event". this module is just a thread + a menu wired up to start/stop/
configure that loop, so CLI and GUI can never drift into different
gesture behavior.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from concurrent.futures import CancelledError

import rumps
from Foundation import NSObject

from orvix import __version__, calibration
from orvix.calibration_viz import BoundsTracker, coverage_rect, marker_fraction
from orvix.config import (
    DEFAULT_CONFIG_PATH,
    DEFAULT_PROFILES_DIR,
    Settings,
    delete_profile,
    list_profiles,
    load_config,
    load_profile,
    save_config,
    save_profile,
)
from orvix import onboarding
from orvix.gesture_interpreter import GestureEvent
from orvix.leap_client import LeapConnectionError
from orvix.main import run_live
from orvix.overlay import CalibrationOverlayController, DwellRingController, OverlayController
from orvix.shortcuts import NAMED_SHORTCUT_LABELS

logger = logging.getLogger("orvix.gui")

ICON_IDLE = "✋"  # raised hand
ICON_RUNNING = "\U0001f7e2"  # green circle
ICON_CALIBRATING = "\U0001f7e1"  # yellow circle
ICON_ERROR = "❌"  # cross mark

ACTION_CHOICES = ["click", "scroll", "disabled"]
ACTION_LABELS = {"click": "Click / Drag", "scroll": "Scroll", "disabled": "Disabled"}

CURSOR_MODES = ["relative", "tilt", "absolute"]
CURSOR_MODE_LABELS = {
    "relative": "Relative (trackpad)",
    "tilt": "Tilt (joystick)",
    "absolute": "Absolute (point at it)",
}

# how strict "grab" is about the hand being a real closed fist. each choice
# maps to a (grab_require_fist, grab_fist_max_extended) pair. keys are stable
# ids used for matching the current settings back to a menu item.
FIST_CHOICES = ["off", "strict", "thumb", "loose"]
FIST_LABELS = {
    "off": "Off (any curl grabs)",
    "strict": "Strict (full fist)",
    "thumb": "Forgive thumb",
    "loose": "Loose (2 fingers ok)",
}
FIST_SETTINGS = {
    # id -> (require_fist, max_extended). max_extended is ignored when
    # require_fist is False but kept sane so toggling back on is predictable.
    "off": (False, 1),
    "strict": (True, 0),
    "thumb": (True, 1),
    "loose": (True, 2),
}


# radial-menu dwell durations offered in the menu bar, label -> seconds.
# "Off" leaves pinch-to-select as the only way to pick a wedge.
DWELL_CHOICES = ["Off (pinch only)", "0.4s", "0.6s", "0.9s"]
DWELL_SECONDS = {"Off (pinch only)": 0.0, "0.4s": 0.4, "0.6s": 0.6, "0.9s": 0.9}

# the five extra gestures, as (menu label -> Settings flag) checkboxes.
EXTRA_GESTURE_TOGGLES = [
    ("Two-hand zoom", "zoom_enabled"),
    ("Fist-twist volume", "fist_twist_volume_enabled"),
    ("Dwell click", "dwell_click_enabled"),
    ("Palms-out pause", "palms_out_pause_enabled"),
    ("Thumbs-up confirm", "thumbs_up_confirm_enabled"),
]
_EXTRA_GESTURE_ATTR_BY_LABEL = dict(EXTRA_GESTURE_TOGGLES)


def _dwell_label_for(settings: Settings) -> str:
    for label, secs in DWELL_SECONDS.items():
        if abs(secs - settings.radial_dwell_seconds) < 1e-6:
            return label
    return ""


def _fist_choice_for(settings: Settings) -> str:
    """which FIST_CHOICES id matches the current settings, for the checkmark."""
    if not settings.grab_require_fist:
        return "off"
    for choice in ("strict", "thumb", "loose"):
        if FIST_SETTINGS[choice][1] == settings.grab_fist_max_extended:
            return choice
    # a custom max_extended set outside the GUI: don't force a checkmark onto
    # a choice that doesn't actually match it
    return ""


class _MainThreadInvoker(NSObject):
    """
    defined once at module level since PyObjC registers each NSObject
    subclass with the Objective-C runtime by name; redefining a class with
    the same name on every call would hit "class already registered"
    errors from the runtime.
    """

    def invokeWith_(self, payload):
        fn, args = payload
        fn(*args)


_main_thread_invoker = _MainThreadInvoker.alloc().init()


class PipelineWorker:
    """
    owns a background thread running its own asyncio event loop, so the
    live control loop's blocking websocket recv() never touches rumps'
    main-thread Cocoa run loop. start/stop are safe to call repeatedly and
    from the GUI's main thread; the actual asyncio task is only ever
    touched via call_soon_threadsafe from here.
    """

    def __init__(self, on_event, on_status, on_error, on_radial=None, on_dwell=None):
        self._on_event = on_event
        self._on_status = on_status
        self._on_error = on_error
        self._on_radial = on_radial
        self._on_dwell = on_dwell
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._task: asyncio.Task | None = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, settings: Settings, dry_run: bool) -> None:
        if self.running:
            return
        self._thread = threading.Thread(
            target=self._run_thread, args=(settings, dry_run), daemon=True
        )
        self._thread.start()

    def stop(self, wait: bool = False) -> bool:
        """returns whether the worker is confirmed stopped. with wait=True
        this can be False if the old thread didn't unwind within the
        timeout -- callers that need a clean restart must check this rather
        than assuming start() right after stop() always takes effect, since
        start() is a no-op while the old thread is still alive."""
        if not self.running or self._loop is None or self._task is None:
            return True
        loop, task = self._loop, self._task
        loop.call_soon_threadsafe(task.cancel)
        if wait and self._thread is not None:
            # for restarts: the old thread has to actually be gone before we
            # start a new one, or two pipelines briefly both drive the cursor.
            # the timeout is so a wedged thread can't freeze the menu bar.
            self._thread.join(timeout=2.0)
            return not self._thread.is_alive()
        return not wait

    def _run_thread(self, settings: Settings, dry_run: bool) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        errored = False
        try:
            self._task = loop.create_task(
                run_live(
                    dry_run=dry_run,
                    verbose=False,
                    settings=settings,
                    on_event=self._on_event,
                    on_radial=self._on_radial,
                    on_dwell=self._on_dwell,
                )
            )
            self._on_status("running")
            loop.run_until_complete(self._task)
        except (asyncio.CancelledError, CancelledError):
            pass
        except LeapConnectionError as exc:
            errored = True
            self._on_error(str(exc))
        except SystemExit:
            errored = True
            self._on_error("couldn't connect to leapd, see docs/SETUP.md")
        except Exception as exc:  # noqa: BLE001 - surface anything unexpected to the menu bar rather than dying silently
            errored = True
            logger.exception("live pipeline crashed")
            self._on_error(str(exc))
        else:
            # run_live only returns normally when its stream_latest_frames
            # loop ends on its own -- the one documented case is leapd
            # closing the websocket connection cleanly mid-session (see
            # leap_client.py's stream_frames docstring). a user-requested
            # stop always cancels the task instead and lands in the except
            # above, so reaching here means tracking was silently lost and
            # would otherwise look identical to the user pressing Stop.
            errored = True
            self._on_error("lost connection to leapd mid-session, see docs/SETUP.md")
        finally:
            self._shutdown_loop(loop)
            self._loop = None
            self._task = None
            if not errored:
                # _show_error already sets its own persistent icon/status
                # text; calling _on_status("stopped") right after it would
                # silently overwrite that back to a normal-looking idle
                # state the instant the one-time alert dialog gets
                # dismissed, losing the only lasting trace that anything
                # went wrong. same "error looks identical to a normal stop"
                # bug this file already fixed at the detection layer, just
                # one step further down in the UI.
                self._on_status("stopped")

    @staticmethod
    def _shutdown_loop(loop: asyncio.AbstractEventLoop) -> None:
        """
        let everything unwind before closing the loop.

        cancelling the pipeline task leaves others still alive underneath it
        (the frame reader, the leapd heartbeat, websockets' own keepalive).
        closing the loop out from under them logs "Task was destroyed but it
        is pending" and, worse, skips the cleanup that closes the websocket,
        so every stop/start leaked a connection to leapd.
        """
        try:
            pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            loop.run_until_complete(loop.shutdown_asyncgens())
        except Exception:  # noqa: BLE001 - a messy teardown must not take the app down
            logger.debug("pipeline loop teardown was not clean", exc_info=True)
        finally:
            loop.close()


def _load_startup_config() -> Settings:
    """
    load_config()'s Settings(**raw) crashes on invalid yaml or a stray/typo'd
    key, and with no terminal attached (launched from Finder/LaunchAgent)
    that crash is invisible -- the menu bar icon just never shows up, no
    error, nothing. fall back to defaults and say so instead, so a broken
    ~/.orvix/config.yaml degrades to "orvix runs on defaults" rather than
    "orvix silently refuses to start".
    """
    try:
        return load_config()
    except Exception as exc:  # noqa: BLE001 - a bad config file must not crash orvix before it can show an icon
        logger.warning("failed to load %s, falling back to defaults: %s", DEFAULT_CONFIG_PATH, exc)
        rumps.alert(
            "orvix",
            f"{DEFAULT_CONFIG_PATH} looks broken ({exc}) -- starting with default settings instead.",
        )
        return Settings()


class OrvixApp(rumps.App):
    def __init__(self):
        super().__init__("orvix", title=ICON_IDLE, quit_button=None)

        # captured before anything in this run could save a config, since
        # is_first_run just checks whether config.yaml or any profile
        # exists yet at all
        self._is_first_run = onboarding.is_first_run(DEFAULT_CONFIG_PATH, DEFAULT_PROFILES_DIR)

        self.settings = _load_startup_config()
        self.dry_run = rumps.MenuItem("Dry Run (don't move real cursor)", callback=self._toggle_dry_run)
        self.dry_run.state = False

        self.status_item = rumps.MenuItem("status: stopped")
        self.last_event_item = rumps.MenuItem("last event: -")
        self.start_stop_item = rumps.MenuItem("Start", callback=self._toggle_running)

        self.pinch_menu = rumps.MenuItem("Pinch does...")
        self.grab_menu = rumps.MenuItem("Grab does...")
        for action in ACTION_CHOICES:
            self.pinch_menu.add(
                rumps.MenuItem(ACTION_LABELS[action], callback=self._make_action_setter("pinch", action))
            )
            self.grab_menu.add(
                rumps.MenuItem(ACTION_LABELS[action], callback=self._make_action_setter("grab", action))
            )

        self.mode_menu = rumps.MenuItem("Cursor mode...")
        for mode in CURSOR_MODES:
            self.mode_menu.add(
                rumps.MenuItem(CURSOR_MODE_LABELS[mode], callback=self._make_mode_setter(mode))
            )

        self.fist_menu = rumps.MenuItem("Grab needs a fist...")
        for choice in FIST_CHOICES:
            self.fist_menu.add(
                rumps.MenuItem(FIST_LABELS[choice], callback=self._make_fist_setter(choice))
            )

        # let the cursor roam onto every active display, not just the main
        # one. built once when the pipeline starts (see main.py's
        # get_desktop_bounds call), so flipping this restarts the pipeline
        # like cursor mode does.
        self.multi_monitor_toggle = rumps.MenuItem(
            "Use all displays", callback=self._toggle_multi_monitor
        )
        self.multi_monitor_toggle.state = self.settings.multi_monitor

        # faint always-on ring around the tracked cursor, not just during a
        # dwell-click countdown -- read live in the frame loop like the
        # radial menu toggle, so no pipeline restart needed.
        self.cursor_ring_toggle = rumps.MenuItem(
            "Show cursor ring", callback=self._toggle_cursor_ring
        )
        self.cursor_ring_toggle.state = self.settings.cursor_ring_enabled

        # gesture 12: circle to open the radial menu; pick a wedge by pinch or dwell
        self.radial_toggle = rumps.MenuItem(
            "Radial menu (circle to open)", callback=self._toggle_radial
        )
        self.radial_toggle.state = self.settings.radial_menu_enabled
        self.dwell_menu = rumps.MenuItem("Radial dwell...")
        for label in DWELL_CHOICES:
            self.dwell_menu.add(rumps.MenuItem(label, callback=self._make_dwell_setter(label)))

        self.extras_menu = rumps.MenuItem("More gestures...")
        for label, attr in EXTRA_GESTURE_TOGGLES:
            item = rumps.MenuItem(label, callback=self._make_extra_toggle(attr))
            item.state = bool(getattr(self.settings, attr))
            self.extras_menu.add(item)

        # what a thumbs-up hold actually fires: any named shortcut, same
        # table the radial wedges use, not just the original literal Return.
        self.thumbs_menu = rumps.MenuItem("Thumbs-up does...")
        for name, label in NAMED_SHORTCUT_LABELS.items():
            self.thumbs_menu.add(
                rumps.MenuItem(label, callback=self._make_thumbs_up_setter(name))
            )

        # named, saved snapshots of the whole settings file -- "demo" vs
        # "daily use" vs whatever -- swappable without hand-editing yaml.
        self.profiles_menu = rumps.MenuItem("Profiles...")
        self._rebuild_profiles_menu()

        self._refresh_action_checkmarks()

        self.menu = [
            self.status_item,
            self.last_event_item,
            None,
            self.start_stop_item,
            self.dry_run,
            None,
            self.mode_menu,
            self.pinch_menu,
            self.grab_menu,
            self.fist_menu,
            self.multi_monitor_toggle,
            self.cursor_ring_toggle,
            None,
            self.radial_toggle,
            self.dwell_menu,
            self.extras_menu,
            self.thumbs_menu,
            None,
            self.profiles_menu,
            None,
            rumps.MenuItem("Calibrate...", callback=self._calibrate),
            None,
            rumps.MenuItem(f"orvix v{__version__}"),
            rumps.MenuItem("Quit", callback=self._quit),
        ]

        # the on-screen radial wheel + the cursor dwell ring. safe no-ops if
        # AppKit isn't available.
        self.overlay = OverlayController()
        self.dwell_ring = DwellRingController()
        # live coverage HUD shown while calibrating, see _run_calibration
        self.calibration_overlay = CalibrationOverlayController()

        self.worker = PipelineWorker(
            on_event=self._handle_event,
            on_status=self._handle_status,
            on_error=self._handle_error,
            on_radial=self._handle_radial,
            on_dwell=self._handle_dwell,
        )
        # gesture events can fire as often as leapd streams frames (~100/s
        # on real hardware), way more often than a menu bar label needs
        # redrawing. throttle how often we hop to the main thread for a
        # status update so a fast hand doesn't spam Cocoa with selector
        # calls.
        self._last_event_ui_update = 0.0
        self._last_cal_ui_update = 0.0
        self._last_cal_hud_update = 0.0
        self._event_ui_interval = 0.15

        # set fresh at the start of each calibration run, see _run_calibration
        self._cal_tracker: BoundsTracker | None = None
        self._cal_fraction = 0.0
        self._cal_n_samples = 0
        # guards against a second calibration thread starting while one is
        # already running (e.g. clicking "Calibrate..." twice back to back) --
        # without this, two threads would both write self._cal_tracker/
        # self.settings.calibration and both call save_config concurrently
        self._calibrating = False

        # fires once NSApplication is actually up (rumps sets that up inside
        # run(), not __init__), so an alert shown from here won't race a not-
        # yet-initialized app. see _maybe_show_onboarding.
        rumps.events.before_start.register(self._maybe_show_onboarding)

    # -- menu callbacks (always invoked on the main thread by rumps) --

    def _maybe_show_onboarding(self) -> None:
        """
        first launch only (no ~/.orvix/config.yaml yet): greet, explain
        calibration in one sentence, and offer to jump straight into it
        instead of leaving a new user to discover "Calibrate..." on their
        own with the rough guessed defaults driving the cursor meanwhile.
        """
        if not self._is_first_run:
            return
        choice = rumps.alert(
            "welcome to orvix",
            onboarding.WELCOME_MESSAGE,
            ok=onboarding.CALIBRATE_NOW_LABEL,
            cancel=onboarding.SKIP_FOR_NOW_LABEL,
        )
        if choice == 1:
            self._calibrate(None)
        else:
            # skip for now still needs to leave evidence of this run behind,
            # or is_first_run sees no config.yaml and no profile next launch
            # and shows this exact same alert again -- forever, for anyone
            # who skips and never happens to touch a menu setting or
            # calibrate in between. saving current (default) settings here
            # is what actually makes "skip for now" mean "don't ask again",
            # matching onboarding.is_first_run's docstring.
            save_config(self.settings)

    def _hide_live_overlays(self) -> None:
        # run_live's frame loop only ever renders(None) to hide the radial
        # wheel/dwell ring on its own closing frame -- there's no finally
        # around the loop, so a cancel (Stop, or any setting change that
        # restarts the pipeline) landing while the wheel is open or the ring
        # is mid-countdown skips that frame entirely and leaves the window
        # frozen on screen, click-through and all, until something else
        # happens to draw over it. hide both explicitly wherever we stop.
        self.overlay.render(None)
        self.dwell_ring.render(None)

    def _toggle_running(self, sender: rumps.MenuItem) -> None:
        if self.worker.running:
            self.worker.stop()
            self._hide_live_overlays()
        else:
            if self._calibrating:
                # mirrors the guard _calibrate has against starting live
                # while calibrating: without this, both threads would hold
                # their own leapd stream open and race each other for
                # self._cal_tracker/self.settings.calibration/save_config,
                # same class of problem _calibrate already refuses.
                rumps.alert("orvix", "let calibration finish before starting the live pipeline.")
                return
            self.worker.start(self.settings, dry_run=self.dry_run.state)

    def _toggle_dry_run(self, sender: rumps.MenuItem) -> None:
        sender.state = not sender.state
        self.dry_run.state = sender.state
        # same reason as cursor mode/multi-monitor: dry_run is only read when
        # the pipeline starts (worker.start(..., dry_run=...)), so flipping
        # this while already running would otherwise leave the checkbox
        # claiming one thing (e.g. "dry run is on") while the live worker
        # keeps doing the other (still moving your real cursor).
        self._restart_pipeline_if_running()

    def _make_mode_setter(self, mode: str):
        def _set(sender: rumps.MenuItem) -> None:
            if self.settings.cursor_mode == mode:
                return
            self.settings.cursor_mode = mode
            save_config(self.settings)
            self._refresh_action_checkmarks()

            # the mapper is built when the pipeline starts, so a mode change
            # means nothing until it restarts. do that here rather than
            # leaving the menu showing one mode while a different one is
            # actually driving the cursor.
            # stop(wait=True) blocks this thread up to 2s (see
            # PipelineWorker.stop) with the menu otherwise showing nothing
            # different, which reads as a hang the same way the old
            # calibration wait phase did. _restart_pipeline_if_running says
            # so while it happens.
            self._restart_pipeline_if_running()

        return _set

    def _toggle_multi_monitor(self, sender: rumps.MenuItem) -> None:
        sender.state = not sender.state
        self.settings.multi_monitor = bool(sender.state)
        save_config(self.settings)

        # same reason as cursor mode: desktop bounds are read once when the
        # pipeline starts, so the change is invisible until it restarts.
        self._restart_pipeline_if_running()

    def _make_action_setter(self, family: str, action: str):
        def _set(sender: rumps.MenuItem) -> None:
            if family == "pinch":
                self.settings.pinch_action = action
            else:
                self.settings.grab_action = action
            save_config(self.settings)
            self._refresh_action_checkmarks()

        return _set

    def _make_thumbs_up_setter(self, name: str):
        def _set(sender: rumps.MenuItem) -> None:
            self.settings.thumbs_up_action = name
            save_config(self.settings)
            self._refresh_action_checkmarks()
            # read live in _execute_extras every frame, no restart needed,
            # same as radial_menu_enabled

        return _set

    def _toggle_cursor_ring(self, sender: rumps.MenuItem) -> None:
        sender.state = not sender.state
        self.settings.cursor_ring_enabled = bool(sender.state)
        save_config(self.settings)
        # read live in the frame loop, so no pipeline restart needed

    def _toggle_radial(self, sender: rumps.MenuItem) -> None:
        sender.state = not sender.state
        self.settings.radial_menu_enabled = bool(sender.state)
        save_config(self.settings)
        # read live in the frame loop, so no pipeline restart needed

    def _make_extra_toggle(self, attr: str):
        def _set(sender: rumps.MenuItem) -> None:
            sender.state = not sender.state
            setattr(self.settings, attr, bool(sender.state))
            save_config(self.settings)
            # the extra-gesture set is built at pipeline start, so restart to
            # apply, same as cursor mode and radial dwell.
            self._restart_pipeline_if_running()

        return _set

    def _make_dwell_setter(self, label: str):
        def _set(sender: rumps.MenuItem) -> None:
            secs = DWELL_SECONDS[label]
            if abs(secs - self.settings.radial_dwell_seconds) < 1e-6:
                return
            self.settings.radial_dwell_seconds = secs
            save_config(self.settings)
            self._refresh_action_checkmarks()
            # the RadialMenu is built with its dwell at pipeline start, so a
            # change only lands on restart. do it now rather than leave the
            # menu claiming one dwell while another is live.
            self._restart_pipeline_if_running()

        return _set

    def _make_fist_setter(self, choice: str):
        def _set(sender: rumps.MenuItem) -> None:
            require, max_extended = FIST_SETTINGS[choice]
            self.settings.grab_require_fist = require
            self.settings.grab_fist_max_extended = max_extended
            save_config(self.settings)
            self._refresh_action_checkmarks()
            # this one takes effect on the next frame the interpreter reads,
            # no pipeline restart needed: it's read live from settings inside
            # process_hand rather than baked in at start like the mapper.

        return _set

    def _refresh_action_checkmarks(self) -> None:
        for item in self.pinch_menu.values():
            item.state = ACTION_LABELS.get(self.settings.pinch_action) == item.title
        for item in self.grab_menu.values():
            item.state = ACTION_LABELS.get(self.settings.grab_action) == item.title
        for item in self.mode_menu.values():
            item.state = CURSOR_MODE_LABELS.get(self.settings.cursor_mode) == item.title
        active_fist = _fist_choice_for(self.settings)
        for item in self.fist_menu.values():
            item.state = FIST_LABELS.get(active_fist) == item.title
        active_dwell = _dwell_label_for(self.settings)
        for item in self.dwell_menu.values():
            item.state = active_dwell == item.title
        active_thumbs = NAMED_SHORTCUT_LABELS.get(self.settings.thumbs_up_action)
        for item in self.thumbs_menu.values():
            item.state = active_thumbs == item.title
        self.multi_monitor_toggle.state = self.settings.multi_monitor
        self.cursor_ring_toggle.state = self.settings.cursor_ring_enabled
        self.radial_toggle.state = self.settings.radial_menu_enabled
        for item in self.extras_menu.values():
            attr = _EXTRA_GESTURE_ATTR_BY_LABEL.get(item.title)
            if attr is not None:
                item.state = bool(getattr(self.settings, attr))

    def _rebuild_profiles_menu(self) -> None:
        # rumps only allocates the submenu's backing NSMenu on the first add(),
        # so clear() blows up on a MenuItem that's never had anything added yet
        if self.profiles_menu._menu is not None:
            self.profiles_menu.clear()
        names = list_profiles()
        for name in names:
            self.profiles_menu.add(
                rumps.MenuItem(name, callback=self._make_profile_load_setter(name))
            )
        if names:
            self.profiles_menu.add(rumps.separator)
        self.profiles_menu.add(rumps.MenuItem("Save current as...", callback=self._save_profile_as))
        if names:
            delete_menu = rumps.MenuItem("Delete...")
            for name in names:
                delete_menu.add(
                    rumps.MenuItem(name, callback=self._make_profile_delete_setter(name))
                )
            self.profiles_menu.add(delete_menu)

    def _apply_loaded_settings(self) -> None:
        """settings changed wholesale (profile load): sync every checkmark and
        restart the pipeline if it's running, same as any single setter that
        touches a start-time-only field like cursor mode or multi-monitor."""
        save_config(self.settings)
        self._refresh_action_checkmarks()
        self._restart_pipeline_if_running()

    def _restart_pipeline_if_running(self) -> None:
        """common tail for every setter that only takes effect on pipeline
        start (cursor mode, multi-monitor, extra gestures, radial dwell,
        profile load): restart so the change actually applies instead of the
        menu claiming one thing while a different one is still live."""
        if not self.worker.running:
            return
        dry_run = bool(self.dry_run.state)
        self.status_item.title = "status: restarting..."
        if not self.worker.stop(wait=True):
            # the old thread didn't unwind within the timeout, so start()
            # below would just be a silent no-op (it refuses to run a
            # second pipeline on top of one that's still alive). without
            # this check the menu would sit on "restarting..." forever
            # while the stale pipeline kept running on the old settings.
            self._show_error(
                "the running pipeline didn't stop in time, still using the old settings -- try Stop then Start."
            )
            return
        self._hide_live_overlays()
        self.worker.start(self.settings, dry_run=dry_run)

    def _make_profile_load_setter(self, name: str):
        def _load(sender: rumps.MenuItem) -> None:
            if self._calibrating:
                # calibration finishes by writing to whatever self.settings
                # currently points at (self.settings.calibration = box;
                # save_config(self.settings), read live at completion time,
                # not captured at thread start). swapping self.settings out
                # from under it mid-sweep would silently land the calibration
                # result on the newly loaded profile instead of the one that
                # was actually being calibrated. same class of race
                # _toggle_running/_calibrate already guard against.
                rumps.alert("orvix", "let calibration finish before loading a profile.")
                return
            try:
                new_settings = load_profile(name)
            except FileNotFoundError:
                rumps.alert("orvix", f"profile {name!r} is gone -- refreshing the list.")
                self._rebuild_profiles_menu()
                return
            except Exception as exc:  # noqa: BLE001 - a corrupt profile file must not crash orvix
                logger.warning("failed to load profile %r, keeping current settings: %s", name, exc)
                rumps.alert(
                    "orvix",
                    f"profile {name!r} looks broken ({exc}) -- keeping current settings.",
                )
                return
            self.settings = new_settings
            self._apply_loaded_settings()

        return _load

    def _save_profile_as(self, sender: rumps.MenuItem) -> None:
        response = rumps.Window(
            title="Save profile",
            message="name this profile (letters, digits, - and _ only):",
            default_text="",
        ).run()
        name = response.text.strip() if response.clicked else ""
        if not name:
            return
        if name in list_profiles():
            overwrite = rumps.alert(
                "orvix",
                f"a profile named {name!r} already exists -- overwrite it with the current settings?",
                ok="Overwrite",
                cancel="Cancel",
            )
            if overwrite != 1:
                return
        try:
            save_profile(name, self.settings)
        except ValueError as exc:
            rumps.alert("orvix", str(exc))
            return
        self._rebuild_profiles_menu()

    def _make_profile_delete_setter(self, name: str):
        def _delete(sender: rumps.MenuItem) -> None:
            confirmed = rumps.alert(
                "orvix", f"delete profile {name!r}? this can't be undone.", ok="Delete", cancel="Cancel"
            )
            if confirmed != 1:
                return
            try:
                delete_profile(name)
            except FileNotFoundError:
                pass
            self._rebuild_profiles_menu()

        return _delete

    def _calibrate(self, sender: rumps.MenuItem) -> None:
        if self.worker.running:
            rumps.alert("orvix", "stop the live pipeline before calibrating.")
            return
        if self._calibrating:
            # clicking "Calibrate..." again while a sweep is already in
            # progress would start a second thread racing the first one for
            # self._cal_tracker/self.settings.calibration/save_config
            rumps.alert("orvix", "calibration is already running, let it finish first.")
            return

        # this alert blocks the main thread until you dismiss it, which is
        # what we want: the sweep shouldn't start counting down while you're
        # still reading. the thread starts after you click OK.
        rumps.alert(
            "orvix calibration",
            "this works out your actual reach so the cursor covers your whole screen.\n\n"
            f"when you click OK, spend {calibration.SWEEP_SECONDS:.0f}s sweeping your hand around "
            "the whole area you want to use: left to right, high to low, like you're "
            "wiping a window. keep it over the sensor, palm down.\n\n"
            "no need to hold still or hit exact spots. watch the menu bar for progress.",
        )
        self._calibrating = True
        threading.Thread(target=self._run_calibration, daemon=True).start()

    def _quit(self, sender: rumps.MenuItem) -> None:
        # calibration runs on its own daemon thread and holds its own leapd
        # stream open (see calibration.wait_for_hand/collect_range) -- same
        # leak class as the pipeline worker's, but there's no clean way to
        # cancel an in-flight sweep, so warn instead of silently letting the
        # process exit kill the thread mid-collection and drop the config
        # save you were about to get at the end of it.
        if self._calibrating:
            confirmed = rumps.alert(
                "orvix",
                "calibration is still running -- quitting now abandons it "
                "(and the bounds you've swept so far won't get saved). quit anyway?",
                ok="Quit anyway",
                cancel="Cancel",
            )
            if confirmed != 1:
                return

        # wait=True so the leapd websocket actually gets closed (see
        # PipelineWorker._shutdown_loop) before the process exits and the
        # daemon thread running it gets killed mid cleanup -- same leak this
        # already gets fixed for on every restart path, quitting shouldn't
        # be the one way left to leave a connection open.
        self.worker.stop(wait=True)
        rumps.quit_application()

    # -- worker callbacks (invoked from the background thread, must hop
    #    back to the main thread before touching any rumps/Cocoa objects) --

    def _handle_event(self, event: GestureEvent) -> None:
        now = time.monotonic()
        if now - self._last_event_ui_update < self._event_ui_interval:
            return
        self._last_event_ui_update = now
        self._on_main_thread(self._update_last_event, event)

    def _handle_status(self, status: str) -> None:
        self._on_main_thread(self._update_status, status)

    def _handle_error(self, message: str) -> None:
        self._on_main_thread(self._show_error, message)

    def _handle_radial(self, state) -> None:
        # fired from the pipeline thread; the overlay is Cocoa, so hop to the
        # main thread before drawing. not throttled: the wheel is only up
        # briefly and wants to track the hand smoothly.
        self._on_main_thread(self.overlay.render, state)

    def _handle_dwell(self, progress) -> None:
        # same main-thread hop for the cursor dwell ring
        self._on_main_thread(self.dwell_ring.render, progress)

    @staticmethod
    def _on_main_thread(fn, *args) -> None:
        # rumps/PyObjC's run loop only picks up scheduled calls via
        # NSTimer-backed rumps.Timer or similar; the simplest thread-safe
        # option here is performSelectorOnMainThread via PyObjC directly.
        _main_thread_invoker.performSelectorOnMainThread_withObject_waitUntilDone_(
            "invokeWith:", (fn, args), False
        )

    def _update_status(self, status: str) -> None:
        self.status_item.title = f"status: {status}"
        self.title = ICON_RUNNING if status == "running" else ICON_IDLE
        self.start_stop_item.title = "Stop" if status == "running" else "Start"

    def _update_last_event(self, event: GestureEvent) -> None:
        self.last_event_item.title = f"last event: {event.type.value}"

    def _show_error(self, message: str) -> None:
        self.title = ICON_ERROR
        self.status_item.title = "status: error"
        self.start_stop_item.title = "Start"
        rumps.alert("orvix", message)

    def _run_calibration(self) -> None:
        """
        runs on its own thread (calibration blocks on the leap stream for
        SWEEP_SECONDS, which would freeze the menu bar if it ran on the main
        thread). drives calibration.calibrate(), same code the cli uses, and
        just renders the progress differently.
        """
        self._cal_tracker = BoundsTracker()
        self._cal_fraction = 0.0
        self._cal_n_samples = 0

        # wait_for_hand blocks for up to 30s with no on_progress/on_sample
        # callbacks at all, so without this the menu bar shows nothing
        # changing right after you click OK -- the calibration prompt says
        # "watch the menu bar for progress" but there'd be none to watch
        # until a hand is actually seen. this gives the same "something is
        # happening" feedback the sweep phase gets from _update_calibration_ui.
        self._on_main_thread(self._show_waiting_for_hand)

        try:
            try:
                box = asyncio.run(
                    calibration.calibrate(
                        self.settings,
                        on_progress=self._calibration_progress,
                        on_sample=self._calibration_sample,
                    )
                )
            except calibration.CalibrationError as exc:
                self._on_main_thread(self._end_calibration_ui)
                self._handle_error(str(exc))
                return
            except LeapConnectionError as exc:
                self._on_main_thread(self._end_calibration_ui)
                self._handle_error(str(exc))
                return

            self.settings.calibration = box

            # tilt mode needs a neutral "flat" reading same as the terminal
            # flow does (see calibration.py's _run_async) -- nobody's hand
            # rests at a true zero, so without this tilt_center_x/z just sit
            # at their 0.0 default forever for anyone who only ever
            # calibrates through the menu bar, and tilt mode creeps sideways
            # on its own the whole time. best-effort: a failure here shouldn't
            # lose the sweep we already have, so it's caught and skipped same
            # as the cli does.
            self._on_main_thread(self._show_measuring_tilt)
            try:
                cx, cz = asyncio.run(calibration.collect_neutral_tilt(self.settings.preferred_hand))
            except (calibration.CalibrationError, LeapConnectionError):
                pass
            else:
                self.settings.tilt_center_x = cx
                self.settings.tilt_center_z = cz

            save_config(self.settings)

            self._on_main_thread(self._end_calibration_ui)
        finally:
            # always clear, however this run ended, so "Calibrate..." works
            # again -- see the guard in _calibrate()
            self._calibrating = False
        self._on_main_thread(
            lambda: rumps.alert(
                "orvix",
                "calibration saved.\n\n"
                f"{calibration.describe_box(box)}\n\n"
                "tick Dry Run and hit Start to check it feels right before going live.",
            )
        )

    def _calibration_progress(self, fraction: float, n_samples: int) -> None:
        # throttled for the same reason gesture events are, this fires per
        # frame at ~100/sec and the menu only needs to look alive
        now = time.monotonic()
        self._cal_fraction = fraction
        self._cal_n_samples = n_samples
        if fraction < 1.0 and now - self._last_cal_ui_update < self._event_ui_interval:
            return
        self._last_cal_ui_update = now
        self._on_main_thread(self._update_calibration_ui, fraction, n_samples)
        self._on_main_thread(self._update_calibration_hud)

    def _calibration_sample(self, x: float, y: float) -> None:
        # runs on the calibration thread, same as _calibration_progress. the
        # tracker itself is cheap to update every sample; it's only the
        # AppKit redraw that's throttled, same reasoning as everywhere else
        # in this file that touches Cocoa from a background callback.
        self._cal_tracker.update(x, y)
        now = time.monotonic()
        if now - self._last_cal_hud_update < self._event_ui_interval:
            return
        self._last_cal_hud_update = now
        self._on_main_thread(self._update_calibration_hud)

    def _show_waiting_for_hand(self) -> None:
        self.status_item.title = "status: waiting for your hand..."
        self.title = ICON_CALIBRATING

    def _show_measuring_tilt(self) -> None:
        self.status_item.title = "status: hold your hand flat for tilt calibration..."
        self.title = ICON_CALIBRATING

    def _update_calibration_ui(self, fraction: float, n_samples: int) -> None:
        filled = int(fraction * 10)
        bar = "#" * filled + "." * (10 - filled)
        self.status_item.title = f"calibrating: [{bar}] {n_samples} samples"
        self.title = ICON_CALIBRATING

    def _update_calibration_hud(self) -> None:
        tracker = self._cal_tracker
        if tracker is None:
            return
        self.calibration_overlay.render(
            {
                "rect": coverage_rect(tracker),
                "marker": marker_fraction(tracker),
                "fraction": self._cal_fraction,
                "n_samples": self._cal_n_samples,
            }
        )

    def _end_calibration_ui(self) -> None:
        self.status_item.title = "status: stopped"
        self.title = ICON_IDLE
        self.calibration_overlay.render(None)
        self._cal_tracker = None


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    OrvixApp().run()


if __name__ == "__main__":
    main()
