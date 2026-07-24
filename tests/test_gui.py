"""
tests for gui.py: the pure label/checkmark helpers, and OrvixApp's menu
construction + setter callbacks. previously zero coverage, even though the
menu bar is the primary way most people actually run orvix.

AppKit/rumps are real here, not mocked out (this dev machine has them, same
as the py2app build in test_setup_py2app.py) -- constructing an NSStatusItem-
backed App works fine without calling .run(), which is what actually spins
up the Cocoa run loop. what every test guards against is touching the real
~/.orvix/config.yaml on whatever machine runs this suite: load_config and
save_config are monkeypatched everywhere, never left to hit disk.
"""

import asyncio
import time

import pytest

import orvix.gui as gui
from orvix.config import Settings
from orvix.gui import (
    _dwell_label_for,
    _fist_choice_for,
)


# -- pure helpers --


def test_dwell_label_matches_the_configured_seconds():
    assert _dwell_label_for(Settings(radial_dwell_seconds=0.6)) == "0.6s"


def test_dwell_label_is_off_for_zero():
    assert _dwell_label_for(Settings(radial_dwell_seconds=0.0)) == "Off (pinch only)"


def test_dwell_label_is_blank_for_an_unlisted_value():
    # a value set outside the GUI (hand-edited config) shouldn't force a
    # checkmark onto the nearest listed choice
    assert _dwell_label_for(Settings(radial_dwell_seconds=0.123)) == ""


def test_fist_choice_off_when_require_fist_is_false():
    assert _fist_choice_for(Settings(grab_require_fist=False)) == "off"


@pytest.mark.parametrize("choice", ["strict", "thumb", "loose"])
def test_fist_choice_round_trips_through_settings(choice):
    from orvix.gui import FIST_SETTINGS

    require_fist, max_extended = FIST_SETTINGS[choice]
    settings = Settings(grab_require_fist=require_fist, grab_fist_max_extended=max_extended)
    assert _fist_choice_for(settings) == choice


def test_fist_choice_blank_for_a_custom_max_extended():
    settings = Settings(grab_require_fist=True, grab_fist_max_extended=99)
    assert _fist_choice_for(settings) == ""


# -- startup config loading --


def test_load_startup_config_returns_settings_unchanged_when_valid(monkeypatch):
    good = Settings(cursor_mode="tilt")
    monkeypatch.setattr(gui, "load_config", lambda: good)

    assert gui._load_startup_config() is good


def test_load_startup_config_falls_back_to_defaults_on_a_broken_file(monkeypatch):
    # a stray/typo'd key or invalid yaml in ~/.orvix/config.yaml used to crash
    # the app before it could even show a menu bar icon, with nothing visible
    # to explain why (no terminal attached when launched from Finder). it
    # should degrade to defaults and say so instead.
    def _broken():
        raise TypeError("__init__() got an unexpected keyword argument 'bogus_key'")

    monkeypatch.setattr(gui, "load_config", _broken)
    alerts = []
    monkeypatch.setattr(gui.rumps, "alert", lambda *a, **k: alerts.append(a))

    settings = gui._load_startup_config()

    assert settings == Settings()
    assert len(alerts) == 1
    assert "bogus_key" in alerts[0][1]


# -- OrvixApp construction + setters --


@pytest.fixture
def isolated_app(monkeypatch, tmp_path):
    """
    an OrvixApp built against a throwaway Settings() and a save_config that
    just records calls instead of writing anywhere, so tests can drive menu
    callbacks freely without any risk to the real machine's config.

    profiles are backed by an in-memory dict rather than the real
    ~/.orvix/profiles directory, same reasoning as save_config above.
    """
    from orvix.config import _validate_profile_name

    monkeypatch.setattr(gui, "load_config", lambda: Settings())
    saved = []
    monkeypatch.setattr(gui, "save_config", lambda settings, *a, **k: saved.append(settings))
    monkeypatch.setattr(gui, "DEFAULT_CONFIG_PATH", tmp_path / "config.yaml")

    profiles: dict = {}

    def _save_profile(name, settings):
        _validate_profile_name(name)
        profiles[name] = settings

    def _load_profile(name):
        if name not in profiles:
            raise FileNotFoundError(name)
        return profiles[name]

    def _delete_profile(name):
        if name not in profiles:
            raise FileNotFoundError(name)
        del profiles[name]

    monkeypatch.setattr(gui, "list_profiles", lambda: sorted(profiles))
    monkeypatch.setattr(gui, "save_profile", _save_profile)
    monkeypatch.setattr(gui, "load_profile", _load_profile)
    monkeypatch.setattr(gui, "delete_profile", _delete_profile)

    app = gui.OrvixApp()
    app._test_saved = saved
    app._test_profiles = profiles
    return app


def test_menu_shows_the_current_version(isolated_app):
    from orvix import __version__

    version_items = [
        item for item in isolated_app.menu.values() if getattr(item, "title", None) == f"orvix v{__version__}"
    ]
    assert len(version_items) == 1
    assert version_items[0].callback is None


def test_pinch_and_grab_menus_offer_every_action_choice(isolated_app):
    titles = {item.title for item in isolated_app.pinch_menu.values()}
    assert titles == {"Click / Drag", "Scroll", "Disabled"}
    titles = {item.title for item in isolated_app.grab_menu.values()}
    assert titles == {"Click / Drag", "Scroll", "Disabled"}


def test_thumbs_menu_offers_every_named_shortcut(isolated_app):
    from orvix.shortcuts import NAMED_SHORTCUT_LABELS

    titles = {item.title for item in isolated_app.thumbs_menu.values()}
    assert titles == set(NAMED_SHORTCUT_LABELS.values())


def test_default_settings_check_the_right_menu_items(isolated_app):
    # defaults: pinch=click, grab=scroll, thumbs_up_action=confirm ("Return")
    checked_pinch = [i.title for i in isolated_app.pinch_menu.values() if i.state]
    checked_grab = [i.title for i in isolated_app.grab_menu.values() if i.state]
    checked_thumbs = [i.title for i in isolated_app.thumbs_menu.values() if i.state]
    assert checked_pinch == ["Click / Drag"]
    assert checked_grab == ["Scroll"]
    assert checked_thumbs == ["Return"]


def test_multi_monitor_toggle_reflects_settings_default_on(isolated_app):
    assert bool(isolated_app.multi_monitor_toggle.state) is True


def test_action_setter_updates_settings_and_saves(isolated_app):
    setter = isolated_app._make_action_setter("pinch", "scroll")
    sender = isolated_app.pinch_menu.get("Scroll")

    setter(sender)

    assert isolated_app.settings.pinch_action == "scroll"
    assert sender.state  # menu item itself gets checked
    assert len(isolated_app._test_saved) == 1


def test_refresh_action_checkmarks_survives_invalid_pinch_action(monkeypatch, tmp_path):
    # config.py's _sanitize_settings is the normal line of defense against an
    # invalid pinch_action/grab_action (see test_config.py), but this checks
    # gui.py's own checkmark refresh doesn't KeyError even if something else
    # ever hands it an out-of-band Settings -- ACTION_LABELS[...] used to be
    # a direct index here, which crashed the (terminal-less) menu bar app.
    monkeypatch.setattr(gui, "load_config", lambda: Settings(pinch_action="typo", grab_action="also_bad"))
    monkeypatch.setattr(gui, "save_config", lambda settings, *a, **k: None)
    monkeypatch.setattr(gui, "DEFAULT_CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr(gui, "list_profiles", lambda: [])

    app = gui.OrvixApp()  # must not raise

    assert all(not item.state for item in app.pinch_menu.values())
    assert all(not item.state for item in app.grab_menu.values())


def test_thumbs_up_setter_updates_settings_and_refreshes_checkmarks(isolated_app):
    setter = isolated_app._make_thumbs_up_setter("undo")
    sender = isolated_app.thumbs_menu.get("Undo")

    setter(sender)

    assert isolated_app.settings.thumbs_up_action == "undo"
    # the checkmark moved off "Return" onto "Undo"
    checked = [i.title for i in isolated_app.thumbs_menu.values() if i.state]
    assert checked == ["Undo"]


def test_multi_monitor_toggle_flips_the_setting(isolated_app):
    sender = isolated_app.multi_monitor_toggle
    assert isolated_app.settings.multi_monitor is True

    isolated_app._toggle_multi_monitor(sender)

    assert isolated_app.settings.multi_monitor is False
    assert bool(sender.state) is False


def test_worker_reports_an_error_when_leapd_drops_the_connection_mid_session(monkeypatch):
    # run_live only ever returns normally (no exception) in the one case its
    # own async-for loop ends on its own: leapd closing the websocket cleanly
    # mid-session (see leap_client.py's stream_frames docstring). a real
    # user-requested stop() always cancels the task instead, landing in
    # PipelineWorker's CancelledError branch. before this fix, a clean
    # mid-session drop fell through to the same "status: stopped" the menu
    # bar shows for a normal stop, with no indication tracking was lost.
    #
    # it also must NOT report "stopped" afterward: _show_error already sets
    # its own persistent icon/status text on the real app, and a trailing
    # "stopped" would silently overwrite that back to a normal-looking idle
    # state the instant the one-time alert dialog gets dismissed.
    async def fake_run_live(**kwargs):
        return

    monkeypatch.setattr(gui, "run_live", fake_run_live)

    statuses = []
    errors = []
    worker = gui.PipelineWorker(
        on_event=lambda e: None,
        on_status=statuses.append,
        on_error=errors.append,
    )
    worker.start(Settings(), dry_run=True)
    worker._thread.join(timeout=2.0)

    assert errors == ["lost connection to leapd mid-session, see docs/SETUP.md"]
    assert "stopped" not in statuses


def test_worker_reports_no_trailing_stopped_status_when_the_pipeline_crashes(monkeypatch):
    # same reasoning as the mid-session-drop test above, but for the
    # generic "the pipeline blew up" except branch instead of the clean
    # disconnect one -- both need to skip the trailing "stopped" so a real
    # crash's error state isn't quietly overwritten right after it's shown.
    async def fake_run_live(**kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(gui, "run_live", fake_run_live)

    statuses = []
    errors = []
    worker = gui.PipelineWorker(
        on_event=lambda e: None,
        on_status=statuses.append,
        on_error=errors.append,
    )
    worker.start(Settings(), dry_run=True)
    worker._thread.join(timeout=2.0)

    assert errors == ["boom"]
    assert "stopped" not in statuses


def test_worker_reports_nothing_extra_on_a_real_user_requested_stop(monkeypatch):
    # the counterpart to the test above: cancelling the task (what stop()
    # does) must NOT trip the new "lost connection" error path.
    async def fake_run_live(**kwargs):
        await asyncio.sleep(10)

    monkeypatch.setattr(gui, "run_live", fake_run_live)

    statuses = []
    errors = []
    worker = gui.PipelineWorker(
        on_event=lambda e: None,
        on_status=statuses.append,
        on_error=errors.append,
    )
    worker.start(Settings(), dry_run=True)
    time.sleep(0.1)  # let the thread actually start running() before stopping it
    worker.stop(wait=True)

    assert errors == []
    assert statuses[-1] == "stopped"


class _FakeRunningWorker:
    """stands in for a PipelineWorker that's mid-flight, so stop(wait=True)
    followed by start() can be asserted on without spinning up a real
    asyncio thread. stop(wait=True) is what actually blocks the main/menu
    thread for up to 2s (see PipelineWorker.stop) -- that's the whole reason
    these restart paths need a status update before calling it."""

    def __init__(self, stop_succeeds=True):
        self.running = True
        self.stop_calls = []
        self.start_calls = []
        self._stop_succeeds = stop_succeeds

    def stop(self, wait=False):
        self.stop_calls.append(wait)
        return self._stop_succeeds

    def start(self, settings, dry_run):
        self.start_calls.append((settings, dry_run))


class _FakeMenuItem:
    """bare sender stand-in for callbacks that only touch sender.state."""

    def __init__(self):
        self.state = False


@pytest.mark.parametrize(
    "trigger",
    [
        lambda app: app._make_mode_setter("tilt")(None),
        lambda app: app._toggle_multi_monitor(app.multi_monitor_toggle),
        lambda app: app._make_extra_toggle("zoom_enabled")(_FakeMenuItem()),
        lambda app: app._make_dwell_setter("0.6s")(None),
        lambda app: app._apply_loaded_settings(),
        lambda app: app._toggle_dry_run(app.dry_run),
    ],
    ids=["cursor_mode", "multi_monitor", "extra_toggle", "dwell", "profile_load", "dry_run"],
)
def test_every_pipeline_restart_path_shows_restarting_status_first(isolated_app, trigger):
    # stop(wait=True) blocks up to 2s with the menu otherwise unchanged,
    # which reads as a hang the same way the pre-fix calibration wait phase
    # did (see test_run_calibration_shows_waiting_for_hand_before_the_blocking_call).
    # cycle 32's click-flash only covered one of several action paths at
    # first and had to be patched again after the fact -- fix every restart
    # site in this same pass instead of repeating that.
    fake_worker = _FakeRunningWorker()
    isolated_app.worker = fake_worker

    trigger(isolated_app)

    assert fake_worker.stop_calls == [True]
    assert len(fake_worker.start_calls) == 1
    assert isolated_app.status_item.title == "status: restarting..."


def test_restart_shows_error_instead_of_silently_no_opping_when_stop_times_out(isolated_app, monkeypatch):
    # stop(wait=True) returning False means the old thread didn't unwind in
    # time -- start() right after would be a silent no-op (PipelineWorker
    # refuses to run a second pipeline on top of a live one), leaving the menu
    # stuck on "restarting..." forever while the stale pipeline kept running
    # on the old settings. this must surface an error instead.
    fake_worker = _FakeRunningWorker(stop_succeeds=False)
    isolated_app.worker = fake_worker
    monkeypatch.setattr(gui.rumps, "alert", lambda *a, **k: 1)

    isolated_app._make_mode_setter("tilt")(None)

    assert fake_worker.stop_calls == [True]
    assert fake_worker.start_calls == []
    assert isolated_app.status_item.title == "status: error"


def test_quit_waits_for_the_pipeline_to_stop_before_quitting_the_app(isolated_app, monkeypatch):
    # same leak _quit could otherwise cause as the restart paths above: without
    # wait=True the process can exit before the worker's background thread
    # gets to close the leapd websocket, see PipelineWorker._shutdown_loop.
    fake_worker = _FakeRunningWorker()
    isolated_app.worker = fake_worker
    quit_calls = []
    monkeypatch.setattr(gui.rumps, "quit_application", lambda *a, **k: quit_calls.append(a))

    isolated_app._quit(None)

    assert fake_worker.stop_calls == [True]
    assert len(quit_calls) == 1


def test_quit_warns_and_can_be_cancelled_while_calibration_is_running(isolated_app, monkeypatch):
    # calibration holds its own leapd stream open on a daemon thread with no
    # clean cancellation path, so quitting mid-sweep should ask first instead
    # of silently abandoning it, same spirit as the worker-stop leak fix above.
    isolated_app._calibrating = True
    fake_worker = _FakeRunningWorker()
    isolated_app.worker = fake_worker
    quit_calls = []
    monkeypatch.setattr(gui.rumps, "quit_application", lambda *a, **k: quit_calls.append(a))
    monkeypatch.setattr(gui.rumps, "alert", lambda *a, **k: 0)  # cancel

    isolated_app._quit(None)

    assert fake_worker.stop_calls == []
    assert quit_calls == []


def test_quit_proceeds_past_the_calibration_warning_if_confirmed(isolated_app, monkeypatch):
    isolated_app._calibrating = True
    fake_worker = _FakeRunningWorker()
    isolated_app.worker = fake_worker
    quit_calls = []
    monkeypatch.setattr(gui.rumps, "quit_application", lambda *a, **k: quit_calls.append(a))
    monkeypatch.setattr(gui.rumps, "alert", lambda *a, **k: 1)  # "Quit anyway"

    isolated_app._quit(None)

    assert fake_worker.stop_calls == [True]
    assert len(quit_calls) == 1


def test_cursor_ring_toggle_reflects_settings_default_off(isolated_app):
    assert bool(isolated_app.cursor_ring_toggle.state) is False


def test_cursor_ring_toggle_flips_the_setting(isolated_app):
    sender = isolated_app.cursor_ring_toggle
    assert isolated_app.settings.cursor_ring_enabled is False

    isolated_app._toggle_cursor_ring(sender)

    assert isolated_app.settings.cursor_ring_enabled is True
    assert bool(sender.state) is True
    assert len(isolated_app._test_saved) == 1


def test_dry_run_toggle_flips_independently_of_settings(isolated_app):
    # dry-run is deliberately not part of Settings/config.yaml, it's a
    # per-session menu checkbox, see _toggle_dry_run
    sender = isolated_app.dry_run
    assert bool(sender.state) is False

    isolated_app._toggle_dry_run(sender)

    assert bool(sender.state) is True
    assert bool(isolated_app.dry_run.state) is True


# -- profiles menu --


def test_profiles_menu_starts_with_only_save_option(isolated_app):
    titles = [item.title for item in isolated_app.profiles_menu.values()]
    assert titles == ["Save current as..."]


def test_save_profile_as_adds_it_to_the_menu(isolated_app, monkeypatch):
    monkeypatch.setattr(gui.rumps, "Window", lambda **kw: _FakeWindow("my-profile"))

    isolated_app._save_profile_as(None)

    assert "my-profile" in isolated_app._test_profiles
    titles = [getattr(item, "title", None) for item in isolated_app.profiles_menu.values()]
    assert "my-profile" in titles
    assert "Delete..." in titles


def test_save_profile_as_does_nothing_on_cancel(isolated_app, monkeypatch):
    monkeypatch.setattr(gui.rumps, "Window", lambda **kw: _FakeWindow("ignored", clicked=False))

    isolated_app._save_profile_as(None)

    assert isolated_app._test_profiles == {}


def test_save_profile_as_rejects_an_invalid_name(isolated_app, monkeypatch):
    monkeypatch.setattr(gui.rumps, "Window", lambda **kw: _FakeWindow("bad/name"))
    alerts = []
    monkeypatch.setattr(gui.rumps, "alert", lambda title, message: alerts.append(message))

    isolated_app._save_profile_as(None)

    assert isolated_app._test_profiles == {}
    assert len(alerts) == 1


def test_save_profile_as_over_an_existing_name_asks_first(isolated_app, monkeypatch):
    isolated_app._test_profiles["work"] = Settings()
    isolated_app._rebuild_profiles_menu()
    monkeypatch.setattr(gui.rumps, "Window", lambda **kw: _FakeWindow("work"))
    calls = []
    monkeypatch.setattr(gui.rumps, "alert", lambda *a, **k: calls.append((a, k)) or 1)

    isolated_app._save_profile_as(None)

    assert len(calls) == 1
    assert isolated_app._test_profiles["work"] is isolated_app.settings


def test_save_profile_as_over_an_existing_name_can_be_cancelled(isolated_app, monkeypatch):
    original = Settings(cursor_mode="tilt")
    isolated_app._test_profiles["work"] = original
    isolated_app._rebuild_profiles_menu()
    monkeypatch.setattr(gui.rumps, "Window", lambda **kw: _FakeWindow("work"))
    monkeypatch.setattr(gui.rumps, "alert", lambda *a, **k: 0)

    isolated_app._save_profile_as(None)

    assert isolated_app._test_profiles["work"] is original


def test_load_profile_setter_replaces_settings_and_saves(isolated_app):
    other = Settings(cursor_mode="tilt", multi_monitor=False, cursor_ring_enabled=True)
    isolated_app._test_profiles["work"] = other

    isolated_app._make_profile_load_setter("work")(None)

    assert isolated_app.settings is other
    assert bool(isolated_app.multi_monitor_toggle.state) is False
    assert bool(isolated_app.cursor_ring_toggle.state) is True
    checked_mode = [i.title for i in isolated_app.mode_menu.values() if i.state]
    assert checked_mode == [gui.CURSOR_MODE_LABELS["tilt"]]
    assert len(isolated_app._test_saved) == 1


def test_load_profile_setter_refuses_while_calibration_is_running(isolated_app, monkeypatch):
    # calibration finishes by writing to whatever self.settings currently
    # points at, so swapping settings out from under it mid-sweep would
    # silently land the calibration result on the wrong profile.
    other = Settings(cursor_mode="tilt")
    isolated_app._test_profiles["work"] = other
    isolated_app._calibrating = True
    before = isolated_app.settings
    alerts = []
    monkeypatch.setattr(gui.rumps, "alert", lambda *a, **k: alerts.append(a))

    isolated_app._make_profile_load_setter("work")(None)

    assert isolated_app.settings is before
    assert len(alerts) == 1
    assert len(isolated_app._test_saved) == 0


def test_load_profile_setter_handles_a_since_deleted_profile(isolated_app, monkeypatch):
    monkeypatch.setattr(gui.rumps, "alert", lambda *a, **k: None)

    isolated_app._make_profile_load_setter("gone")(None)

    # nothing blew up, and settings weren't touched
    assert isolated_app.settings.cursor_mode == "relative"


def test_load_profile_setter_handles_a_corrupt_profile(isolated_app, monkeypatch):
    # a hand-edited or half-written profile yaml can fail with all sorts of
    # things (TypeError on an unexpected key, yaml.YAMLError on bad syntax) --
    # none of that should crash the app or clobber the current settings.
    def _broken(name):
        raise TypeError("__init__() got an unexpected keyword argument 'bogus_key'")

    monkeypatch.setattr(gui, "load_profile", _broken)
    alerts = []
    monkeypatch.setattr(gui.rumps, "alert", lambda *a, **k: alerts.append(a))
    before = isolated_app.settings

    isolated_app._make_profile_load_setter("busted")(None)

    assert isolated_app.settings is before
    assert len(alerts) == 1
    assert "busted" in alerts[0][1]


def test_delete_profile_setter_removes_it_when_confirmed(isolated_app, monkeypatch):
    isolated_app._test_profiles["temp"] = Settings()
    isolated_app._rebuild_profiles_menu()
    monkeypatch.setattr(gui.rumps, "alert", lambda *a, **k: 1)

    isolated_app._make_profile_delete_setter("temp")(None)

    assert "temp" not in isolated_app._test_profiles


def test_delete_profile_setter_keeps_it_when_cancelled(isolated_app, monkeypatch):
    isolated_app._test_profiles["temp"] = Settings()
    isolated_app._rebuild_profiles_menu()
    monkeypatch.setattr(gui.rumps, "alert", lambda *a, **k: 0)

    isolated_app._make_profile_delete_setter("temp")(None)

    assert "temp" in isolated_app._test_profiles


def test_calibrate_refuses_a_second_run_while_one_is_in_progress(isolated_app, monkeypatch):
    # clicking "Calibrate..." twice back to back used to spin up two threads
    # racing each other for self._cal_tracker/self.settings.calibration/
    # save_config -- this guard is what stops the second one from starting.
    started = []
    monkeypatch.setattr(gui.threading, "Thread", lambda *a, **k: started.append(k) or _NoopThread())
    alerts = []
    monkeypatch.setattr(gui.rumps, "alert", lambda *a, **k: alerts.append(a))

    isolated_app._calibrating = True
    isolated_app._calibrate(None)

    assert started == []
    assert len(alerts) == 1
    assert "already running" in alerts[0][1]


def test_calibrate_starts_when_not_already_calibrating(isolated_app, monkeypatch):
    started = []
    monkeypatch.setattr(gui.threading, "Thread", lambda *a, **k: started.append(k) or _NoopThread())
    monkeypatch.setattr(gui.rumps, "alert", lambda *a, **k: None)

    isolated_app._calibrate(None)

    assert len(started) == 1
    assert isolated_app._calibrating is True


def test_skipping_onboarding_saves_settings_so_the_welcome_alert_does_not_come_back(isolated_app, monkeypatch):
    # "skip for now" used to leave config.yaml untouched, so is_first_run
    # (no config.yaml, no profiles) stayed true and the exact same welcome
    # alert reappeared on every later launch until the user happened to
    # calibrate or touch some other setting -- not what "skip for now" is
    # supposed to mean, per onboarding.is_first_run's own docstring.
    monkeypatch.setattr(gui.rumps, "alert", lambda *a, **k: 2)  # cancel/skip
    isolated_app._is_first_run = True
    isolated_app._test_saved.clear()

    isolated_app._maybe_show_onboarding()

    assert isolated_app._test_saved == [isolated_app.settings]


def test_calibrating_from_onboarding_does_not_also_save_settings_directly(isolated_app, monkeypatch):
    monkeypatch.setattr(gui.rumps, "alert", lambda *a, **k: 1)  # calibrate now
    started = []
    monkeypatch.setattr(gui.threading, "Thread", lambda *a, **k: started.append(k) or _NoopThread())
    isolated_app._is_first_run = True
    isolated_app._test_saved.clear()

    isolated_app._maybe_show_onboarding()

    assert len(started) == 1
    assert isolated_app._test_saved == []


def test_onboarding_alert_is_skipped_entirely_on_a_later_run(isolated_app, monkeypatch):
    alerts = []
    monkeypatch.setattr(gui.rumps, "alert", lambda *a, **k: alerts.append(a) or 2)
    isolated_app._is_first_run = False
    isolated_app._test_saved.clear()

    isolated_app._maybe_show_onboarding()

    assert alerts == []
    assert isolated_app._test_saved == []


def test_toggle_running_refuses_to_start_the_pipeline_while_calibrating(isolated_app, monkeypatch):
    # the reverse of test_calibrate_refuses_a_second_run_while_one_is_in_progress:
    # starting the live pipeline mid-calibration would have both threads
    # holding their own leapd stream open and racing each other for
    # self._cal_tracker/self.settings.calibration/save_config, the exact
    # race _calibrate's own guard exists to prevent, just triggered the
    # other way around since _toggle_running never checked _calibrating.
    fake_worker = _FakeRunningWorker()
    fake_worker.running = False
    isolated_app.worker = fake_worker
    alerts = []
    monkeypatch.setattr(gui.rumps, "alert", lambda *a, **k: alerts.append(a))

    isolated_app._calibrating = True
    isolated_app._toggle_running(None)

    assert fake_worker.start_calls == []
    assert len(alerts) == 1
    assert "calibration" in alerts[0][1]


def test_toggle_running_starts_the_pipeline_normally_when_not_calibrating(isolated_app, monkeypatch):
    fake_worker = _FakeRunningWorker()
    fake_worker.running = False
    isolated_app.worker = fake_worker
    monkeypatch.setattr(gui.rumps, "alert", lambda *a, **k: None)

    isolated_app._toggle_running(None)

    assert len(fake_worker.start_calls) == 1


def test_run_calibration_shows_waiting_for_hand_before_the_blocking_call(isolated_app, monkeypatch):
    # wait_for_hand blocks for up to 30s with no progress callbacks at all,
    # so without an upfront status update the menu bar looks frozen right
    # after you click OK -- this checks that feedback actually gets set
    # before calibration.calibrate() is called, not just eventually.
    seen_status_before_calibrate = []

    def _record_and_raise(*a, **k):
        seen_status_before_calibrate.append(isolated_app.status_item.title)
        raise gui.calibration.CalibrationError("never saw a hand")

    monkeypatch.setattr(gui.calibration, "calibrate", _record_and_raise)
    monkeypatch.setattr(gui, "_main_thread_invoker", _SyncMainThreadInvoker())
    monkeypatch.setattr(gui.rumps, "alert", lambda *a, **k: None)

    isolated_app._run_calibration()

    assert seen_status_before_calibrate == ["status: waiting for your hand..."]


def test_run_calibration_also_measures_neutral_tilt_on_success(isolated_app, monkeypatch):
    # tilt mode needs tilt_center_x/z to not be stuck at the 0.0 default, see
    # calibration.py's collect_neutral_tilt -- the menu bar flow used to only
    # run the sweep and skip this step entirely, which the terminal flow
    # always did. this checks the gui flow now captures it too.
    from orvix.config import CalibrationBox

    box = CalibrationBox(x_min=-100, x_max=100, y_min=50, y_max=250, z_min=-50, z_max=50)

    async def _fake_calibrate(*a, **k):
        return box

    async def _fake_neutral_tilt(*a, **k):
        return 0.2, -0.1

    monkeypatch.setattr(gui.calibration, "calibrate", _fake_calibrate)
    monkeypatch.setattr(gui.calibration, "collect_neutral_tilt", _fake_neutral_tilt)
    monkeypatch.setattr(gui, "_main_thread_invoker", _SyncMainThreadInvoker())
    monkeypatch.setattr(gui.rumps, "alert", lambda *a, **k: None)

    isolated_app._run_calibration()

    assert isolated_app.settings.calibration == box
    assert isolated_app.settings.tilt_center_x == pytest.approx(0.2)
    assert isolated_app.settings.tilt_center_z == pytest.approx(-0.1)
    assert isolated_app._test_saved[-1] is isolated_app.settings


def test_run_calibration_keeps_the_sweep_if_neutral_tilt_read_fails(isolated_app, monkeypatch):
    # a flaky read on the tilt step shouldn't throw away a perfectly good
    # sweep -- same "best effort, don't lose what we already have" reasoning
    # the terminal flow uses.
    from orvix.config import CalibrationBox

    box = CalibrationBox(x_min=-100, x_max=100, y_min=50, y_max=250, z_min=-50, z_max=50)

    async def _fake_calibrate(*a, **k):
        return box

    async def _broken_neutral_tilt(*a, **k):
        raise gui.calibration.CalibrationError("couldn't get a steady read")

    monkeypatch.setattr(gui.calibration, "calibrate", _fake_calibrate)
    monkeypatch.setattr(gui.calibration, "collect_neutral_tilt", _broken_neutral_tilt)
    monkeypatch.setattr(gui, "_main_thread_invoker", _SyncMainThreadInvoker())
    monkeypatch.setattr(gui.rumps, "alert", lambda *a, **k: None)

    isolated_app._run_calibration()

    assert isolated_app.settings.calibration == box
    assert isolated_app.settings.tilt_center_x == 0.0
    assert isolated_app.settings.tilt_center_z == 0.0
    assert isolated_app._test_saved[-1] is isolated_app.settings


def test_run_calibration_clears_the_flag_even_when_calibration_errors(isolated_app, monkeypatch):
    def _broken(*a, **k):
        raise gui.calibration.CalibrationError("sweep too short")

    monkeypatch.setattr(gui.calibration, "calibrate", _broken)
    monkeypatch.setattr(gui, "_main_thread_invoker", _SyncMainThreadInvoker())
    # _handle_error's path pops a real rumps.alert, which blocks forever
    # waiting for a click in a headless test run if left unmocked
    monkeypatch.setattr(gui.rumps, "alert", lambda *a, **k: None)
    isolated_app._calibrating = True

    isolated_app._run_calibration()

    assert isolated_app._calibrating is False


class _NoopThread:
    """stand-in for threading.Thread so calibration tests never spin up a real thread."""

    def start(self) -> None:
        pass


class _SyncMainThreadInvoker:
    """runs the callback synchronously instead of hopping to a real Cocoa main thread."""

    def performSelectorOnMainThread_withObject_waitUntilDone_(self, _selector, args, _wait):
        fn, fn_args = args
        fn(*fn_args)


class _FakeWindow:
    """stand-in for rumps.Window so tests never pop a real text-input dialog."""

    def __init__(self, text, clicked=True):
        self._text = text
        self._clicked = clicked

    def run(self):
        return _FakeResponse(self._text, self._clicked)


class _FakeResponse:
    def __init__(self, text, clicked):
        self.text = text
        self.clicked = clicked
