"""Application entry: single instance, main pywebview window, engine lifecycle.

    python -m app.main [--debug] [--fake] [--ui-dir PATH]

Frozen builds start here through build/entry.py.
"""

from __future__ import annotations

import argparse
import logging
import logging.handlers
import os
import socket
import sys
import threading
from pathlib import Path
from typing import Any, Optional

from app import __version__, winutil
from app.bridge import Bridge, EventPump
from app.core.settings import DEFAULT_SCALE, SCALE_STEPS, normalize_scale

WINDOW_TITLE = "Media Saver"
MUTEX_NAME = "Local\\MediaSaver.SingleInstance"
BACKGROUND = "#0b0d14"
# Window = page area in layout px (CSS px at Scale 100 %): resizable from MIN_SIZE (the size the layout and its
# media queries are designed for) up to the work area, never maximized; START_SIZE is the first launch's size.
START_SIZE = (1180, 780)
MIN_SIZE = (940, 640)
GEOMETRY_SAVE_DELAY = 0.6  # seconds after the last move or size change before the place and size are stored
HTTP_PORT_FIRST = 42001  # fixed port keeps the page origin (and its localStorage) stable between launches
HTTP_PORT_TRIES = 40

SHELL_LOGGERS = {"mediasaver.main", "mediasaver.bridge", "mediasaver.login", "mediasaver.winutil", "mediasaver.crash"}

log = logging.getLogger("mediasaver.main")
_keep_alive: list[Any] = []


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="MediaSaver", description="Media Saver")
    parser.add_argument("--debug", action="store_true", help="enable WebView2 dev tools and verbose logging")
    parser.add_argument("--fake", action="store_true", help="use the simulated engine (no network, no core)")
    parser.add_argument("--ui-dir", help="serve the UI from this folder instead of the bundled one")
    return parser.parse_args(argv)


def bundle_root() -> Path:
    """Folder that holds ui/ and the icon: PyInstaller's extraction dir when frozen, the project root otherwise."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent.parent


def resolve_ui_index(ui_dir: Optional[str]) -> Path:
    base = Path(ui_dir) if ui_dir else bundle_root() / "ui"
    return base / "index.html"


def resolve_icon() -> Optional[str]:
    for candidate in (bundle_root() / "icon.ico", bundle_root() / "build" / "icon.ico"):
        if candidate.is_file():
            return str(candidate)
    return None


def pick_http_port() -> int:
    """First free port from the fixed range; 0 lets pywebview fall back to its own choice."""
    for port in range(HTTP_PORT_FIRST, HTTP_PORT_FIRST + HTTP_PORT_TRIES):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            try:
                probe.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    return 0


def setup_logging(log_dir: Path, debug: bool) -> None:
    """File log for the shell (core keeps its own app.log). Safe in a windowed exe where sys.stderr is None."""
    # The core logs on the "mediasaver" logger into app.log; the shell's loggers are "mediasaver.main", "mediasaver.bridge", ... so they
    # land there too. shell.log keeps only the shell's own records (plus pywebview's) for easier triage.
    app_logger = logging.getLogger("mediasaver")
    app_logger.setLevel(logging.DEBUG if debug else logging.INFO)
    webview_logger = logging.getLogger("pywebview")
    webview_logger.setLevel(logging.DEBUG if debug else logging.WARNING)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s [%(threadName)s] %(message)s")
    handlers: list[logging.Handler] = []
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            log_dir / "shell.log", maxBytes=1_000_000, backupCount=2, encoding="utf-8", delay=True
        )
        file_handler.addFilter(lambda record: record.name.split(".")[0] == "pywebview" or record.name in SHELL_LOGGERS)
        handlers.append(file_handler)
    except OSError:
        pass
    if sys.stderr is not None:  # a windowed exe has no stderr
        handlers.append(logging.StreamHandler(sys.stderr))
    for handler in handlers:
        handler.setFormatter(fmt)
        app_logger.addHandler(handler)
        webview_logger.addHandler(handler)

    def _excepthook(exc_type, exc, tb):
        logging.getLogger("mediasaver.crash").critical("unhandled exception", exc_info=(exc_type, exc, tb))

    def _thread_excepthook(args):
        logging.getLogger("mediasaver.crash").critical(
            "unhandled exception in thread %s", getattr(args.thread, "name", "?"),
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )

    sys.excepthook = _excepthook
    threading.excepthook = _thread_excepthook


def build_engine(args: argparse.Namespace, pump: EventPump) -> Any:
    if args.fake:
        from app.fake_engine import FakeEngine

        return FakeEngine(pump.emit, __version__)
    from app.core.engine import Engine

    return Engine(pump.emit, __version__, open_folder=winutil.open_path)


def _webview_dir(engine: Any) -> str:
    paths = engine.paths
    folder = getattr(paths, "webview_dir", None) or Path(paths.root) / "webview"
    Path(folder).mkdir(parents=True, exist_ok=True)
    return str(folder)


def main(argv: Optional[list[str]] = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    if raw and raw[0] == "--apply-update":
        # The downloaded exe swaps the installed one and restarts it: no window, no mutex, no engine.
        from app.core.selfupdate import run_apply

        return run_apply(raw[1:])
    args = parse_args(raw)

    mutex = winutil.acquire_single_instance(MUTEX_NAME)
    if mutex is None:
        winutil.bring_window_to_front(WINDOW_TITLE)
        return 0

    index = resolve_ui_index(args.ui_dir)
    if not index.is_file():
        winutil.message_box(WINDOW_TITLE, f"UI file not found:\n{index}", error=True)
        return 2

    pump = EventPump()
    try:
        engine = build_engine(args, pump)
    except Exception as exc:  # noqa: BLE001 - frozen exe has no console; show the reason
        winutil.message_box(WINDOW_TITLE, f"Could not start the app engine:\n{exc}", error=True)
        return 3

    setup_logging(Path(getattr(engine.paths, "logs_dir", None) or Path(engine.paths.root) / "logs"), args.debug)
    log.info("Media Saver %s starting (fake=%s, debug=%s, ui=%s)", __version__, args.fake, args.debug, index)

    # Per-monitor DPI awareness before pywebview's own system-aware call: a move to a monitor of another DPI keeps
    # the page sharp and its layout size (bug of a stretched page on the second monitor).
    winutil.set_per_monitor_dpi_awareness()

    import webview  # imported late: pulls in pythonnet and WinForms

    from app.login import LoginController

    bridge = Bridge(engine, pump)
    login = LoginController(engine, bridge._toast)
    bridge._set_login_opener(login.open)

    # The first place and size, so the form opens about where it ends up; the frame sets it exactly once shown.
    zoom = _setting_scale(engine) / 100.0
    rect, monitor = winutil.restore_rect(_setting(engine, "window"), winutil.list_monitors(), zoom, MIN_SIZE,
                                         START_SIZE)
    first_scale = monitor.scale if monitor else 1.0
    window = webview.create_window(
        WINDOW_TITLE,
        url=str(index),
        js_api=bridge,
        width=int(rect[2] / first_scale),
        height=int(rect[3] / first_scale),
        x=int(rect[0] / first_scale),
        y=int(rect[1] / first_scale),
        frameless=True,
        easy_drag=False,
        # resizable=False keeps WS_MAXIMIZEBOX out of the WinForms style; the frame adds the sizing border itself
        resizable=False,
        shadow=True,
        text_select=True,  # the UI decides where selection is allowed; pywebview's global user-select:none is off
        background_color=BACKGROUND,
    )
    bridge._attach_window(window)
    bridge._set_logout_hook(lambda: login.clear_session(window))

    started = threading.Event()

    def on_loaded() -> None:
        if not started.is_set():
            started.set()
            engine.start()

    def on_closing() -> None:
        bridge._shutdown()  # stores a pending window place first

    def on_shown() -> None:
        _setup_native_window(window, bridge)

    window.events.loaded += on_loaded
    window.events.closing += on_closing
    window.events.shown += on_shown

    try:
        webview.start(
            gui="edgechromium",
            debug=args.debug,
            private_mode=False,
            storage_path=_webview_dir(engine),
            http_port=pick_http_port(),
            icon=resolve_icon(),
        )
    finally:
        bridge._shutdown()
        log.info("Media Saver stopped")
        logging.shutdown()
    # Non-daemon .NET / WebView2 helper threads can keep the process alive after the last window is gone.
    os._exit(0)


def _setting(engine: Any, key: str, default: Any = None) -> Any:
    try:
        return engine.get_settings().get(key, default)
    except Exception:  # noqa: BLE001
        log.exception("cannot read setting %s", key)
        return default


def _setting_scale(engine: Any) -> int:
    return normalize_scale(_setting(engine, "ui_scale", DEFAULT_SCALE))


class WindowFrame:
    """The resizable window around the page: its place and size at start, the Scale setting (WebView2 zoom factor
    and window size), the Scale steps that fit the screen, the hand-over of title-bar presses to the system, and the
    stored place and size. Native calls run on the UI thread; `apply_scale`, `begin_drag`, `fitting_scales` and
    `save_now` may be called from any thread."""

    def __init__(self, window: Any, hook: winutil.ResizableHook, engine: Any, bridge: Bridge) -> None:
        self._window = window
        self._hook = hook
        self._engine = engine
        self._bridge = bridge
        self.percent = DEFAULT_SCALE
        self._fitting = list(SCALE_STEPS)
        self._lock = threading.Lock()
        self._pending: Optional[dict] = None
        self._timer: Optional[threading.Timer] = None
        self._ready = False  # no geometry is stored before the first place is set
        hook.on_settled = self._on_settled
        hook.on_dpi = self._on_dpi

    # -- any thread
    def fitting_scales(self) -> list[int]:
        return list(self._fitting)

    def apply_scale(self, percent: int) -> None:
        _on_ui_thread(self._window, lambda: self._apply_scale(int(percent)))

    def begin_drag(self, part: str) -> None:
        hwnd = self._hook.hwnd
        _on_ui_thread(self._window, lambda: winutil.begin_native_drag(hwnd, part), wait=False)

    def save_now(self) -> None:
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
        self._store()

    # -- UI thread
    def restore(self, saved: Optional[dict], percent: int) -> None:
        """Open at the stored place and size (or the start size, centred), on a connected monitor, at the largest
        Scale step up to `percent` whose minimum window fits that monitor."""
        monitors = winutil.list_monitors()
        _, monitor = winutil.restore_rect(saved, monitors, percent / 100.0, MIN_SIZE, START_SIZE)
        fitting = winutil.fitting_scales(SCALE_STEPS, MIN_SIZE, monitor)
        allowed = winutil.allowed_scale(percent, fitting)
        rect, monitor = winutil.restore_rect(saved, monitors, allowed / 100.0, MIN_SIZE, START_SIZE)
        self.percent = allowed
        self._hook.zoom = allowed / 100.0
        winutil.set_zoom_factor(self._window, self._hook.zoom)
        if monitor is not None and abs(monitor.scale - self._hook.scale) > 1e-6:
            # Reach the monitor first: its WM_DPICHANGED is handled before the exact size is set.
            self._move_to(rect[0], rect[1])
        self._place(rect)
        self._ready = True
        self._refresh_fitting()
        if allowed != percent:
            log.info("Scale %s%% does not fit this screen; %s%% is used", percent, allowed)
            self._engine.set_settings({"ui_scale": allowed})
        log.info("window opens at %s physical (page %s layout px) at %s%%, dpi scale %.2f", rect,
                 winutil.layout_size(rect[2:], self._hook.zoom, self._hook.scale), allowed, self._hook.scale)

    def _apply_scale(self, percent: int) -> None:
        """A Scale change: the window grows or shrinks by the ratio of the steps around its centre (the page keeps
        its layout size), clamped to the work area and the new minimum."""
        old_zoom = self._hook.zoom or 1.0
        zoom = percent / 100.0
        current = self._hook.client_rect()
        self._hook.zoom = zoom
        self.percent = percent
        winutil.set_zoom_factor(self._window, zoom)
        if current is None:
            return
        monitor = winutil.window_monitor(self._hook.hwnd)
        rect = winutil.scaled_rect(current, zoom / old_zoom, monitor.work if monitor else None,
                                   self._hook.min_client_px())
        self._place(rect)
        log.info("Scale %s%%: page %s -> %s physical", percent, current, rect)

    def _move_to(self, x: int, y: int) -> None:
        wx, wy, _, _ = self._hook.window_rect_for((x, y, 0, 0))
        self._window.native.Location = _point(wx, wy)

    def _place(self, rect: tuple[int, int, int, int]) -> None:
        self._hook.set_client_rect(rect, apply=self._window.native.SetBounds)

    def _refresh_fitting(self) -> None:
        monitor = winutil.window_monitor(self._hook.hwnd)
        self._fitting = winutil.fitting_scales(SCALE_STEPS, MIN_SIZE, monitor)
        self._bridge._on_window_state(scales=self._fitting)

    def _on_settled(self) -> None:
        """The end of a move or size loop, or a place or size set by code: store the geometry soon."""
        if not self._ready or not self._hook.is_normal():
            return
        rect = self._hook.client_rect()
        if rect is None:
            return
        width, height = winutil.layout_size(rect[2:], self._hook.zoom, self._hook.scale)
        value = {"x": rect[0], "y": rect[1], "width": width, "height": height}
        with self._lock:
            self._pending = value
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(GEOMETRY_SAVE_DELAY, self._store)
            self._timer.daemon = True
            self._timer.start()
        self._refresh_fitting()

    def _on_dpi(self) -> None:
        self._refresh_fitting()

    def _store(self) -> None:
        with self._lock:
            value, self._pending, self._timer = self._pending, None, None
        if value is None:
            return
        try:
            self._engine.set_settings({"window": value})
        except Exception:  # noqa: BLE001 - a lost place is not worth an error on screen
            log.exception("window place could not be stored")


def _point(x: int, y: int) -> Any:
    from System.Drawing import Point  # pythonnet

    return Point(int(x), int(y))


def _on_ui_thread(window: Any, fn, wait: bool = True) -> None:
    """Run `fn` on the WinForms UI thread (pywebview calls event handlers and API methods on worker threads);
    `wait=False` queues it and returns at once."""
    native = getattr(window, "native", None)
    try:
        if native is not None and native.InvokeRequired:
            from System import Action  # pythonnet

            if wait:
                native.Invoke(Action(fn))
            else:
                native.BeginInvoke(Action(fn))
            return
    except Exception:  # noqa: BLE001
        log.debug("UI-thread invoke failed; running directly", exc_info=True)
    fn()


def _setup_native_window(window: Any, bridge: Bridge) -> None:
    """After the form is shown: the sizing border and the minimum size, no maximize, the stored place and size at
    the saved Scale, DWM corners and border colour, focus tracking. The place, size and zoom are set on the UI thread
    before the page shows anything (the page waits for the bridge)."""
    native = window.native
    if native is None:
        return
    engine = bridge._engine
    try:
        hwnd = int(native.Handle.ToInt64())
        winutil.set_rounded_corners(hwnd)
        winutil.set_border_color(hwnd, BACKGROUND)
        hook = winutil.ResizableHook(hwnd, MIN_SIZE)
        if hook.install():
            _keep_alive.append(hook)  # the ctypes callback must outlive the window
            frame = WindowFrame(window, hook, engine, bridge)
            _on_ui_thread(window, lambda: frame.restore(_setting(engine, "window"), _setting_scale(engine)))
            bridge._set_window_frame(frame)
        else:
            log.warning("window hook was not installed")
    except Exception:  # noqa: BLE001
        log.exception("DWM / window setup failed")
    try:
        native.Activated += lambda *_: bridge._on_window_state(focused=True)
        native.Deactivate += lambda *_: bridge._on_window_state(focused=False)
    except Exception:  # noqa: BLE001
        log.exception("focus tracking unavailable")


if __name__ == "__main__":
    sys.exit(main())
