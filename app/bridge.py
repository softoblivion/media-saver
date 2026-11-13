"""pywebview js_api object (`Bridge`) and the Python -> JS event pump (`EventPump`).

Contract: docs/BRIDGE_API.md. Every public attribute of `Bridge` is exposed to JavaScript by pywebview (it walks the
object recursively), so the class has exactly the contract methods as public names and everything else starts with an
underscore. Do not add public helpers or public data attributes to it.
"""

from __future__ import annotations

import functools
import inspect
import json
import logging
import os
import threading
import time
from typing import Any, Callable, Optional

from app import winutil
from app.core.settings import DEFAULT_SCALE, SCALE_STEPS, normalize_scale

WINDOW_DRAG_PARTS = ("move", "top", "top-left", "top-right")

log = logging.getLogger("mediasaver.bridge")

# pywebview FileDialog enum values (webview.FileDialog), repeated here so the module imports without pywebview.
_DIALOG_OPEN = 10
_DIALOG_FOLDER = 20


class BridgeError(Exception):
    """An error whose message is already a user-facing English sentence."""


def friendly_message(exc: BaseException) -> str:
    """Turn any exception into an English sentence for the UI toast.

    BridgeError and ValueError carry user-facing messages written by the bridge and the core (settings validation,
    cookies import), so their text passes through unchanged. Everything else gets a generic wrapper so raw tracebacks
    never reach the user (the full traceback goes to the log).
    """
    if isinstance(exc, BridgeError):
        return str(exc)
    text = str(exc).strip()
    if isinstance(exc, ValueError) and text:
        return text
    if isinstance(exc, PermissionError):
        return "No access to the file or folder."
    if isinstance(exc, FileNotFoundError):
        return "File or folder not found."
    if isinstance(exc, OSError):
        return "File system error" + (f": {exc.strerror}" if exc.strerror else ".")
    if isinstance(exc, (ValueError, TypeError, KeyError)):
        return "Invalid value" + (f": {text}" if text else ".")
    return "The operation failed. See the log for details."


def _api(fn: Callable) -> Callable:
    """Wrap a Bridge method: log failures and re-raise them as BridgeError with an English message."""

    @functools.wraps(fn)
    def wrapper(self, *args, **kwargs):
        try:
            return fn(self, *args, **kwargs)
        except BridgeError:
            raise
        except Exception as exc:  # noqa: BLE001 - the UI needs a message for any failure
            log.exception("bridge.%s failed", fn.__name__)
            raise BridgeError(friendly_message(exc)) from None

    # pywebview reads the parameter names with inspect.getfullargspec, which does not follow __wrapped__.
    wrapper.__signature__ = inspect.signature(fn)  # type: ignore[attr-defined]
    return wrapper


# --- event pump --------------------------------------------------------------------------------------------------

def js_literal(value: Any) -> str:
    """JSON text that is also a safe JavaScript expression (U+2028 / U+2029 escaped, non-ASCII kept)."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")


def build_event_script(events: list[tuple[str, Any]]) -> str:
    """One JS snippet that calls window.__msEvent for every event, isolating failures per event."""
    calls = "".join(
        f"try{{f({js_literal(etype)},{js_literal(payload)})}}catch(e){{console.error(e)}};" for etype, payload in events
    )
    return "(function(){var f=window.__msEvent;if(typeof f!=='function')return;" + calls + "})();"


class EventPump:
    """Thread-safe queue of events for the UI.

    - Nothing is delivered before `set_ready()` (the UI called ready()); everything emitted earlier is kept.
    - `job` events are coalesced per job id: only the newest payload is kept and one id is delivered at most
      `1 / job_interval` times per second (default 8/s). The newest payload is always delivered eventually, so a
      final `done` state is never lost.
    - `job_removed` drops a pending `job` event of that id, so a removed job cannot reappear.
    - Events are delivered in batches through `deliver(script)` (window.run_js) on one daemon thread.
    """

    def __init__(
        self,
        deliver: Optional[Callable[[str], Any]] = None,
        clock: Callable[[], float] = time.monotonic,
        job_interval: float = 0.125,
        start_delay: float = 0.15,
        threaded: bool = True,
    ) -> None:
        self._deliver = deliver
        self._clock = clock
        self._job_interval = job_interval
        self._start_delay = start_delay
        self._lock = threading.Condition()
        self._queue: list[tuple[str, Any]] = []  # ("job", id) markers and ordinary (type, payload) entries
        self._pending_jobs: dict[str, Any] = {}
        self._last_job_sent: dict[str, float] = {}
        self._ready_at: Optional[float] = None
        self._stopped = False
        self._thread: Optional[threading.Thread] = None
        self._threaded = threaded

    # -- producer side
    def set_deliver(self, deliver: Callable[[str], Any]) -> None:
        with self._lock:
            self._deliver = deliver
            self._lock.notify_all()

    def emit(self, etype: str, payload: Any) -> None:
        with self._lock:
            if self._stopped:
                return
            if etype == "job" and isinstance(payload, dict) and "id" in payload:
                job_id = str(payload["id"])
                if job_id not in self._pending_jobs:
                    self._queue.append(("job", job_id))
                self._pending_jobs[job_id] = payload
            else:
                if etype == "job_removed" and isinstance(payload, dict):
                    self._drop_job(str(payload.get("id")))
                self._queue.append((etype, payload))
            self._lock.notify_all()

    def _drop_job(self, job_id: str) -> None:
        if job_id in self._pending_jobs:
            del self._pending_jobs[job_id]
            self._queue = [e for e in self._queue if not (e[0] == "job" and e[1] == job_id)]
        self._last_job_sent.pop(job_id, None)

    def set_ready(self) -> None:
        """The UI called ready(): start delivering (after a short grace so ready()'s return value goes first)."""
        with self._lock:
            if self._ready_at is None:
                self._ready_at = self._clock() + self._start_delay
            self._lock.notify_all()
        if self._threaded:
            self._ensure_thread()

    def stop(self) -> None:
        with self._lock:
            self._stopped = True
            self._lock.notify_all()

    @property
    def ready(self) -> bool:
        return self._ready_at is not None

    # -- consumer side
    def _ensure_thread(self) -> None:
        with self._lock:
            if self._thread is not None:
                return
            self._thread = threading.Thread(target=self._run, name="event-pump", daemon=True)
            self._thread.start()

    def collect(self, now: Optional[float] = None) -> tuple[list[tuple[str, Any]], Optional[float]]:
        """Take the events due at `now`. Returns (events, seconds until the next held job event is due or None)."""
        now = self._clock() if now is None else now
        with self._lock:
            if self._ready_at is None or self._deliver is None:
                return [], None
            if now < self._ready_at:
                return [], self._ready_at - now
            due: list[tuple[str, Any]] = []
            kept: list[tuple[str, Any]] = []
            wait: Optional[float] = None
            for entry in self._queue:
                if entry[0] == "job":
                    job_id = entry[1]
                    last = self._last_job_sent.get(job_id)
                    if last is not None and now - last < self._job_interval:
                        remaining = self._job_interval - (now - last)
                        wait = remaining if wait is None else min(wait, remaining)
                        kept.append(entry)
                        continue
                    due.append(("job", self._pending_jobs.pop(job_id)))
                    self._last_job_sent[job_id] = now
                else:
                    due.append(entry)
            self._queue = kept
            return due, wait

    def flush(self, now: Optional[float] = None) -> Optional[float]:
        """Deliver everything due now; returns the delay until the next held event or None (also used by tests)."""
        events, wait = self.collect(now)
        if events:
            deliver = self._deliver
            try:
                if deliver is not None:
                    deliver(build_event_script(events))
            except Exception:  # noqa: BLE001 - the window may be closing; events are not worth crashing for
                log.debug("event delivery failed", exc_info=True)
        return wait

    def _run(self) -> None:
        while True:
            wait = self.flush()
            with self._lock:
                if self._stopped:
                    return
                if wait is None and self._queue and self._deliver is not None:
                    wait = 0.0  # entries arrived between flush() and here
                self._lock.wait(timeout=wait if wait is not None else 1.0)
                if self._stopped:
                    return
            # A job id is not forgotten forever: trim the send-time table so it does not grow without bound.
            self._trim_sent_times()

    def _trim_sent_times(self) -> None:
        with self._lock:
            if len(self._last_job_sent) > 512:
                cutoff = self._clock() - 5 * self._job_interval
                self._last_job_sent = {k: v for k, v in self._last_job_sent.items() if v > cutoff}


# --- bridge ------------------------------------------------------------------------------------------------------

class Bridge:
    """The js_api object. See docs/BRIDGE_API.md section 2 for every method."""

    def __init__(
        self,
        engine: Any,
        pump: EventPump,
        login_opener: Optional[Callable[[], None]] = None,
    ) -> None:
        self._engine = engine
        self._pump = pump
        self._window: Any = None
        self._login_opener = login_opener
        self._logout_hook: Optional[Callable[[], None]] = None
        self._frame: Any = None  # the shell's window frame (main.WindowFrame): Scale, drag, fitting steps
        self._focused = True
        self._scales: list[int] = list(SCALE_STEPS)  # the Scale steps whose minimum window fits the screen
        self._shutdown_lock = threading.Lock()
        self._shut_down = False

    # -- wiring (underscore names are not exposed to JS)
    def _attach_window(self, window: Any) -> None:
        self._window = window
        self._pump.set_deliver(lambda script: window.run_js(script))

    def _set_login_opener(self, opener: Callable[[], None]) -> None:
        self._login_opener = opener

    def _set_logout_hook(self, hook: Callable[[], None]) -> None:
        self._logout_hook = hook

    def _set_window_frame(self, frame: Any) -> None:
        """The shell's window frame: `apply_scale(percent)` (zoom factor and window size), `begin_drag(part)` (the
        system move or size loop) and `fitting_scales()` (the Scale steps whose minimum window fits the screen)."""
        self._frame = frame
        self._on_window_state(scales=frame.fitting_scales())

    def _toast(self, level: str, text: str, action: Optional[dict] = None) -> None:
        payload: dict[str, Any] = {"level": level, "text": text}
        if action:
            payload["action"] = action
        self._pump.emit("toast", payload)

    def _file_not_found(self, job_id: str) -> None:
        """The toast of open_file / reveal_file on a missing file; a done job's toast offers to download it again."""
        action = None
        if self._engine.can_download_again(job_id):
            action = {"label": "Download again", "method": "download_again", "id": job_id}
        self._toast("warning", "File not found. It may have been moved or deleted.", action)

    def _window_state(self) -> dict:
        return {"focused": self._focused, "scales": list(self._scales)}

    def _on_window_state(self, focused: Optional[bool] = None, scales: Optional[list[int]] = None) -> None:
        """Called by the native focus events and by the window frame when the window reaches a monitor of another
        size or DPI; emits `window` ({"focused": bool, "scales": [int]}) when either changed. The window cannot be
        maximized, so focus and the fitting Scale steps are the whole window state."""
        changed = False
        if focused is not None and focused != self._focused:
            self._focused, changed = focused, True
        if scales is not None and list(scales) != self._scales:
            self._scales, changed = [int(s) for s in scales], True
        if changed:
            self._pump.emit("window", self._window_state())

    def _shutdown(self) -> None:
        """Idempotent: stop the pump and shut the engine down (<= 3 s by contract)."""
        with self._shutdown_lock:
            if self._shut_down:
                return
            self._shut_down = True
        if self._frame is not None:
            try:
                self._frame.save_now()  # the window's last place and size, while the engine still saves settings
            except Exception:  # noqa: BLE001
                log.exception("window place could not be stored")
        try:
            self._engine.shutdown()
        except Exception:  # noqa: BLE001
            log.exception("engine shutdown failed")
        self._pump.stop()

    def _setting(self, key: str, default: Any = None) -> Any:
        try:
            return self._engine.get_settings().get(key, default)
        except Exception:  # noqa: BLE001
            log.exception("cannot read setting %s", key)
            return default

    def _paths_attr(self, name: str, fallback_rel: str = "") -> Optional[str]:
        paths = getattr(self._engine, "paths", None)
        value = getattr(paths, name, None)
        if value:
            return str(value)
        root = getattr(paths, "root", None)
        if root and fallback_rel:
            return os.path.join(str(root), fallback_rel)
        return None

    # -- state
    @_api
    def ready(self):
        # The UI may call ready() before the window's loaded event starts the engine; starting here is idempotent
        # and guarantees that the state carries the restored history.
        start = getattr(self._engine, "start", None)
        if callable(start):
            start()
        state = self._engine.get_state()
        self._pump.set_ready()
        self._pump.emit("window", self._window_state())
        return state

    # -- links and jobs
    @_api
    def clean_links(self, text, platform=None):
        return self._engine.clean_links(_as_text(text), _as_choice(platform, "platform"))

    @_api
    def enqueue(self, text, platform=None, format=None):  # noqa: A002 - parameter name is part of the JS contract
        return self._engine.enqueue(_as_text(text), _as_choice(platform, "platform"), _as_choice(format, "format"))

    @_api
    def set_job_format(self, id, format):  # noqa: A002
        choice = _as_choice(format, "format")
        if choice is None:
            raise BridgeError("Format must be mp4 or mp3.")
        return self._engine.set_job_format(str(id), choice)

    @_api
    def cancel(self, id):  # noqa: A002 - parameter name is part of the JS contract
        return bool(self._engine.cancel(str(id)))

    @_api
    def retry(self, id):  # noqa: A002
        return bool(self._engine.retry(str(id)))

    @_api
    def retry_failed(self, platform=None):
        return int(self._engine.retry_failed(_as_choice(platform, "platform")))

    @_api
    def resume_interrupted(self):
        return int(self._engine.resume_interrupted())

    @_api
    def download_again(self, id):  # noqa: A002
        return bool(self._engine.download_again(str(id)))

    @_api
    def remove(self, id):  # noqa: A002
        return bool(self._engine.remove(str(id)))

    @_api
    def clear_finished(self, platform=None):
        return int(self._engine.clear_finished(_as_choice(platform, "platform")))

    # -- files and folders
    @_api
    def open_file(self, id):  # noqa: A002
        path = self._engine.job_file(str(id))
        if not path or not os.path.exists(path):
            self._file_not_found(str(id))
            return False
        return winutil.open_path(path)

    @_api
    def reveal_file(self, id):  # noqa: A002
        path = self._engine.job_file(str(id))
        if not path or not os.path.exists(path):
            self._file_not_found(str(id))
            return False
        return winutil.reveal_in_explorer(path)

    @_api
    def open_output_dir(self):
        folder = self._setting("output_dir")
        if not folder:
            return False
        os.makedirs(folder, exist_ok=True)
        return winutil.open_path(folder)

    @_api
    def open_logs(self):
        folder = self._paths_attr("logs_dir", "logs")
        if not folder:
            return False
        os.makedirs(folder, exist_ok=True)
        return winutil.open_path(folder)

    @_api
    def choose_output_dir(self):
        if self._window is None:
            return None
        current = self._setting("output_dir", "") or ""
        result = self._window.create_file_dialog(_DIALOG_FOLDER, directory=current)
        if not result:
            return None
        chosen = result[0] if isinstance(result, (list, tuple)) else result
        chosen = str(chosen)
        self._engine.set_settings({"output_dir": chosen})  # validates, saves, emits `settings`
        return chosen

    # -- settings and engine
    @_api
    def get_settings(self):
        return self._engine.get_settings()

    @_api
    def set_settings(self, patch):
        if not isinstance(patch, dict):
            raise BridgeError("Expected a settings object.")
        return self._engine.set_settings(patch)

    @_api
    def set_scale(self, value):
        """The Scale setting (BRIDGE_API Settings.ui_scale): saves the step and applies it to the window at once.
        A step whose minimum window does not fit the screen (not in the `window` event's `scales`) is refused."""
        step = normalize_scale(value)
        if step not in self._scales:
            raise BridgeError(f"Scale {step}% does not fit this screen.")
        new = self._engine.set_settings({"ui_scale": step})
        if self._frame is not None:
            try:
                self._frame.apply_scale(int(new.get("ui_scale", DEFAULT_SCALE)))
            except Exception:  # noqa: BLE001 - the setting is saved; the window keeps its size
                log.exception("scale could not be applied")
        return new

    @_api
    def update_engine(self):
        self._engine.update_engine()
        return None

    # -- app self-update
    @_api
    def update_status(self):
        return self._engine.update_status()

    @_api
    def check_updates(self):
        return self._engine.check_updates()

    @_api
    def download_update(self):
        self._engine.download_update()
        return None

    @_api
    def apply_update(self):
        if not self._engine.apply_update():
            raise BridgeError("The update is not ready to install.")
        # The helper waits for this process to exit; close the window the same way the title bar button does.
        threading.Thread(target=self._close_window, name="window-close", daemon=True).start()
        return None

    # -- Instagram session
    @_api
    def login(self):
        if self._login_opener is None:
            raise BridgeError("The sign-in window is not available in this launch mode.")
        self._login_opener()
        return None

    @_api
    def logout(self):
        auth = self._engine.logout()
        if self._logout_hook is not None:
            try:
                self._logout_hook()  # also forget the Instagram page session of the WebView2 profile
            except Exception:  # noqa: BLE001
                log.exception("logout hook failed")
        return auth

    @_api
    def import_cookies_file(self):
        if self._window is None:
            return None
        result = self._window.create_file_dialog(
            _DIALOG_OPEN,
            allow_multiple=False,
            file_types=("Cookie files (*.txt)", "All files (*.*)"),
        )
        if not result:
            return None
        path = result[0] if isinstance(result, (list, tuple)) else result
        return self._engine.import_cookies_file(str(path))

    # -- clipboard and browser
    @_api
    def read_clipboard(self):
        return winutil.read_clipboard()

    @_api
    def copy_text(self, text):
        return bool(winutil.write_clipboard(_as_text(text)))

    @_api
    def open_url(self, url):
        return bool(winutil.open_url(_as_text(url)))

    # -- window control (resizable frameless window: the HTML title bar offers minimize and close only, no maximize)
    @_api
    def window_drag(self, part):
        """A press on the title bar ("move") or on the page's top edge ("top", "top-left", "top-right") hands the
        mouse to the system's move or size loop. Nothing happens once the button is already up."""
        if part not in WINDOW_DRAG_PARTS:
            raise BridgeError(f"Unknown window part: {part!r}")
        if self._frame is not None:
            self._frame.begin_drag(part)
        return None

    @_api
    def window_minimize(self):
        if self._window is not None:
            self._window.minimize()
        return None

    @_api
    def window_close(self):
        # Hand the work to a thread: the JS call must return, and the engine shutdown may take up to 3 s.
        threading.Thread(target=self._close_window, name="window-close", daemon=True).start()
        return None

    def _close_window(self) -> None:
        window = self._window
        try:
            if window is not None:
                window.hide()  # instant feedback while the engine shuts down
        except Exception:  # noqa: BLE001
            log.debug("hide before close failed", exc_info=True)
        self._shutdown()
        try:
            if window is not None:
                window.destroy()
        except Exception:  # noqa: BLE001
            log.debug("destroy failed", exc_info=True)
        # destroying the last window ends webview.start() in main.py, whose `finally` exits the process


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise BridgeError("Expected text.")
    return value


def _as_choice(value: Any, what: str) -> Optional[str]:
    """An optional short identifier argument (platform, format): None / "" mean default."""
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise BridgeError(f"Expected a {what} name.")
    return value
