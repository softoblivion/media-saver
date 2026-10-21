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

WINDOW_TITLE = "Media Saver"
MUTEX_NAME = "Local\\MediaSaver.SingleInstance"
BACKGROUND = "#0b0d14"
START_SIZE = (1180, 780)  # page viewport in logical pixels
MIN_SIZE = (940, 640)
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

    import webview  # imported late: pulls in pythonnet and WinForms

    from app.login import LoginController

    bridge = Bridge(engine, pump)
    login = LoginController(engine, bridge._toast)
    bridge._set_login_opener(login.open)

    window = webview.create_window(
        WINDOW_TITLE,
        url=str(index),
        js_api=bridge,
        width=START_SIZE[0],
        height=START_SIZE[1],
        min_size=MIN_SIZE,
        frameless=True,
        easy_drag=False,
        resizable=True,
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
        bridge._shutdown()

    def on_shown() -> None:
        _setup_native_window(window, bridge)

    window.events.loaded += on_loaded
    window.events.closing += on_closing
    window.events.shown += on_shown
    window.events.maximized += lambda: bridge._on_window_state(maximized=True)
    window.events.restored += lambda: bridge._on_window_state(maximized=False)

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


def _setup_native_window(window: Any, bridge: Bridge) -> None:
    """Runs on the UI thread after the form is shown: DWM corners, focus tracking."""
    native = window.native
    if native is None:
        return
    try:
        hwnd = int(native.Handle.ToInt64())
        winutil.set_rounded_corners(hwnd)
        hook = winutil.FramelessResizeHook(hwnd, min_client_size=MIN_SIZE)
        if hook.install():
            _keep_alive.append(hook)  # the ctypes callback must outlive the window
            hook.set_client_size(*START_SIZE, apply=native.SetBounds)
        else:
            log.warning("edge resizing hook was not installed")
    except Exception:  # noqa: BLE001
        log.exception("DWM / frame setup failed")
    try:
        native.Activated += lambda *_: bridge._on_window_state(focused=True)
        native.Deactivate += lambda *_: bridge._on_window_state(focused=False)
    except Exception:  # noqa: BLE001
        log.exception("focus tracking unavailable")


if __name__ == "__main__":
    sys.exit(main())
