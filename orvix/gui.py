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

from orvix import calibration
from orvix.config import CalibrationBox, Settings, load_config, save_config
from orvix.gesture_interpreter import GestureEvent
from orvix.leap_client import LeapConnectionError, pick_hand, stream_frames
from orvix.main import run_live

logger = logging.getLogger("orvix.gui")

ICON_IDLE = "✋"  # raised hand
ICON_RUNNING = "\U0001f7e2"  # green circle
ICON_ERROR = "❌"  # cross mark

ACTION_CHOICES = ["click", "scroll", "disabled"]
ACTION_LABELS = {"click": "Click / Drag", "scroll": "Scroll", "disabled": "Disabled"}


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

    def __init__(self, on_event, on_status, on_error):
        self._on_event = on_event
        self._on_status = on_status
        self._on_error = on_error
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

    def stop(self) -> None:
        if not self.running or self._loop is None or self._task is None:
            return
        loop, task = self._loop, self._task
        loop.call_soon_threadsafe(task.cancel)

    def _run_thread(self, settings: Settings, dry_run: bool) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        try:
            self._task = loop.create_task(
                run_live(dry_run=dry_run, verbose=False, settings=settings, on_event=self._on_event)
            )
            self._on_status("running")
            loop.run_until_complete(self._task)
        except (asyncio.CancelledError, CancelledError):
            pass
        except LeapConnectionError as exc:
            self._on_error(str(exc))
        except SystemExit:
            self._on_error("couldn't connect to leapd, see docs/SETUP.md")
        except Exception as exc:  # noqa: BLE001 - surface anything unexpected to the menu bar rather than dying silently
            logger.exception("live pipeline crashed")
            self._on_error(str(exc))
        finally:
            loop.close()
            self._loop = None
            self._task = None
            self._on_status("stopped")


class OrvixApp(rumps.App):
    def __init__(self):
        super().__init__("orvix", title=ICON_IDLE, quit_button=None)

        self.settings = load_config()
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
        self._refresh_action_checkmarks()

        self.menu = [
            self.status_item,
            self.last_event_item,
            None,
            self.start_stop_item,
            self.dry_run,
            None,
            self.pinch_menu,
            self.grab_menu,
            None,
            rumps.MenuItem("Calibrate...", callback=self._calibrate),
            None,
            rumps.MenuItem("Quit", callback=self._quit),
        ]

        self.worker = PipelineWorker(
            on_event=self._handle_event,
            on_status=self._handle_status,
            on_error=self._handle_error,
        )
        # gesture events can fire at up to target_fps (100/s by default), way
        # more often than a menu bar label needs redrawing. throttle how
        # often we hop to the main thread for a status update so a fast
        # hand doesn't spam Cocoa with selector calls.
        self._last_event_ui_update = 0.0
        self._event_ui_interval = 0.15

    # -- menu callbacks (always invoked on the main thread by rumps) --

    def _toggle_running(self, sender: rumps.MenuItem) -> None:
        if self.worker.running:
            self.worker.stop()
        else:
            self.worker.start(self.settings, dry_run=self.dry_run.state)

    def _toggle_dry_run(self, sender: rumps.MenuItem) -> None:
        sender.state = not sender.state
        self.dry_run.state = sender.state

    def _make_action_setter(self, family: str, action: str):
        def _set(sender: rumps.MenuItem) -> None:
            if family == "pinch":
                self.settings.pinch_action = action
            else:
                self.settings.grab_action = action
            save_config(self.settings)
            self._refresh_action_checkmarks()

        return _set

    def _refresh_action_checkmarks(self) -> None:
        for item in self.pinch_menu.values():
            item.state = ACTION_LABELS[self.settings.pinch_action] == item.title
        for item in self.grab_menu.values():
            item.state = ACTION_LABELS[self.settings.grab_action] == item.title

    def _calibrate(self, sender: rumps.MenuItem) -> None:
        if self.worker.running:
            rumps.alert("orvix", "stop the live pipeline before calibrating.")
            return
        threading.Thread(target=self._run_calibration, daemon=True).start()

    def _quit(self, sender: rumps.MenuItem) -> None:
        self.worker.stop()
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
        try:
            asyncio.run(_gui_calibrate(self.settings, self._on_main_thread))
        except LeapConnectionError as exc:
            self._handle_error(str(exc))
        else:
            self.settings = load_config()
            self._on_main_thread(self._refresh_action_checkmarks)
            self._on_main_thread(
                lambda: rumps.alert("orvix", "calibration saved. try Dry Run first to check it feels right.")
            )


async def _gui_calibrate(settings: Settings, on_main_thread) -> None:
    """
    calibration.py's flow is built around input()/print() for the terminal.
    this reimplements the same two-point capture using rumps alerts instead
    of stdin, so calibration works from the menu bar with no terminal open.
    """
    done = threading.Event()
    result: dict = {}

    def _prompt(prompt: str) -> None:
        rumps.alert("orvix calibration", prompt)
        done.set()

    async def _capture(prompt: str) -> tuple[float, float, float]:
        done.clear()
        on_main_thread(_prompt, prompt)
        while not done.is_set():
            await asyncio.sleep(0.05)
        samples = await calibration._collect_samples(settings.preferred_hand, calibration.SAMPLES_PER_POINT)
        return calibration._average_point(samples)

    top_left = await _capture("move your hand to the TOP-LEFT of your comfortable range, then click OK and hold still.")
    bottom_right = await _capture("now move your hand to the BOTTOM-RIGHT of your comfortable range, then click OK and hold still.")

    x_min, x_max = min(top_left[0], bottom_right[0]), max(top_left[0], bottom_right[0])
    y_min, y_max = min(top_left[1], bottom_right[1]), max(top_left[1], bottom_right[1])
    z_min, z_max = min(top_left[2], bottom_right[2]) - 40, max(top_left[2], bottom_right[2]) + 40

    settings.calibration = CalibrationBox(
        x_min=x_min, x_max=x_max, y_min=y_min, y_max=y_max, z_min=z_min, z_max=z_max
    )
    save_config(settings)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    OrvixApp().run()


if __name__ == "__main__":
    main()
