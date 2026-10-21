"""Engine facade: the only object the bridge talks to (ARCHITECTURE.md section 6, payloads in BRIDGE_API.md).

All methods are thread-safe and return plain JSON-able dicts. Events go out through `emit(type, payload)`,
called from worker threads; a failing emit is logged and never breaks the core.
"""
from __future__ import annotations

import logging
import logging.handlers
import os
import threading
import time
from typing import Any, Callable

from . import auth as authmod
from . import settings as settingsmod
from .downloader import BinaryGate, Downloader, ProcessRunner
from .paths import AppPaths
from .selfupdate import SelfUpdater
from .updater import Net, Updater

log = logging.getLogger("mediasaver.core.engine")

EmitFn = Callable[[str, dict], None]

_logging_lock = threading.Lock()


def setup_logging(paths: AppPaths) -> None:
    """Rotating logs/app.log (2 MB x 3) on the "mediasaver" logger; idempotent."""
    with _logging_lock:
        root = logging.getLogger("mediasaver")
        target = os.path.abspath(paths.app_log)
        for h in root.handlers:
            if isinstance(h, logging.handlers.RotatingFileHandler) and os.path.abspath(h.baseFilename) == target:
                return
        handler = logging.handlers.RotatingFileHandler(target, maxBytes=2 * 1024 * 1024, backupCount=3,
                                                       encoding="utf-8", delay=True)
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s [%(threadName)s] %(message)s"))
        root.addHandler(handler)
        root.setLevel(logging.INFO)


def _clean_links(text: str, platform: str | None = None) -> dict:
    from . import linkclean  # imported lazily so the core loads without it
    return linkclean.clean_text(text or "", platform)


def _platform(value: Any) -> str | None:
    if value is None or value == "":
        return None
    if value not in settingsmod.PLATFORMS:
        raise ValueError(f"Unknown platform: {value!r}")
    return value


def _format(value: Any) -> str | None:
    if value is None or value == "":
        return None
    if value not in settingsmod.FORMATS:
        raise ValueError(f"Unknown format: {value!r}")
    return value


class Engine:
    paths: AppPaths

    def __init__(self, emit: EmitFn, app_version: str, *, home: str | None = None,
                 open_folder: Callable[[str], None] | None = None,
                 runner: ProcessRunner | None = None, net: Net | None = None,
                 use_system_ffmpeg: bool | None = None, app_updates: bool | None = None) -> None:
        # app_updates: False = never check GitHub for app releases (cli, tests); otherwise the check runs on launch
        self._app_updates = app_updates
        self._emit_raw = emit
        self.app_version = app_version
        self.paths = AppPaths(home)
        self.paths.ensure_all()
        setup_logging(self.paths)
        self._open_folder = open_folder if open_folder is not None else self._default_open_folder
        self._lock = threading.RLock()
        self._started = False
        self._settings = settingsmod.SettingsStore(self.paths.settings_file)
        self._gate = BinaryGate()
        runner = runner or ProcessRunner()
        self.updater = Updater(self.paths, self._settings.get, self._on_engine_status, gate=self._gate,
                               runner=runner, net=net, use_system_ffmpeg=use_system_ffmpeg)
        self.downloader = Downloader(
            self.paths, self._settings.get, self._emit,
            binaries=self.updater.binaries, usable=self.updater.usable, gate=self._gate,
            self_heal=self.updater.self_heal, on_auth_invalid=self._on_auth_invalid,
            on_queue_drained=self._on_queue_drained, runner=runner)
        self.selfupdate = SelfUpdater(self.paths, app_version, self._on_update_status, net=net,
                                      on_toast=lambda level, text: self._emit("toast", {"level": level, "text": text}))
        log.info("engine created, version %s, home %s", app_version, self.paths.root)

    # --- events --------------------------------------------------------------------------------------
    def _emit(self, event: str, payload: dict) -> None:
        try:
            self._emit_raw(event, payload)
        except Exception:  # noqa: BLE001
            log.exception("emit %s failed", event)

    def _on_engine_status(self, status: dict) -> None:
        self._emit("engine", status)
        self.downloader.mark_waiting()

    def _on_update_status(self, status: dict) -> None:
        self._emit("update", status)

    def _on_auth_invalid(self) -> None:
        self._emit("auth", self.auth_status())

    def _on_queue_drained(self, done: int, failed: int) -> None:
        if done == 0 and failed == 0:
            return
        if failed == 0:
            text = "All downloads finished" if done > 1 else "Download finished"
            self._emit("toast", {"level": "success", "text": text})
        else:
            self._emit("toast", {"level": "warning", "text": f"Done: {done}, failed: {failed}"})
        if done and self._settings["open_folder_when_done"]:
            try:
                self._open_folder(self._settings["output_dir"])
            except Exception:  # noqa: BLE001
                log.exception("open output dir failed")

    @staticmethod
    def _default_open_folder(path: str) -> None:
        if hasattr(os, "startfile"):
            os.startfile(path)  # type: ignore[attr-defined]

    # --- lifecycle -----------------------------------------------------------------------------------
    def start(self) -> None:
        with self._lock:
            if self._started:
                return
            self._started = True
        self.downloader.load_history()
        self.downloader.start()
        self._emit("engine", self.updater.status())
        self.updater.start_background()
        try:
            self.selfupdate.startup()
        except Exception:  # noqa: BLE001
            log.exception("self-update startup cleanup failed")
        if self._app_updates is not False:
            self.selfupdate.check_background()

    def shutdown(self) -> None:
        log.info("engine shutdown")
        self.downloader.shutdown(timeout=2.5)

    # --- state ---------------------------------------------------------------------------------------
    def get_state(self) -> dict:
        return {
            "app_version": self.app_version,
            "engine": self.updater.status(),
            "update": self.selfupdate.status(),
            "auth": self.auth_status(),
            "settings": self.get_settings(),
            "jobs": self.downloader.jobs(),
        }

    # --- links / jobs --------------------------------------------------------------------------------
    def clean_links(self, text: str, platform: str | None = None) -> dict:
        return _clean_links(text, _platform(platform))

    def enqueue(self, text: str, platform: str | None = None, fmt: str | None = None) -> dict:
        result = _clean_links(text, _platform(platform))
        fmt = _format(fmt) or self._settings["format"]
        added: list[dict] = []
        skipped: list[dict] = []
        with self._lock:
            active = self.downloader.active_keys()
            for item in result.get("items", []):
                if (item.get("error") or not item.get("clean") or not item.get("shortcode")
                        or not item.get("platform") or not item.get("kind")):
                    skipped.append({"input": item.get("original", ""), "reason": "invalid"})
                    continue
                key = f"{item['platform']}:{item['shortcode']}"
                if key in active:
                    skipped.append({"input": item.get("original", ""), "reason": "duplicate"})
                    continue
                active.add(key)
                added.append(self.downloader.add(item["clean"], item["shortcode"], item["platform"], item["kind"], fmt))
        return {"added": added, "skipped": skipped}

    def set_job_format(self, job_id: str, fmt: str) -> dict | None:
        value = _format(fmt)
        if value is None:
            raise ValueError("Format must be mp4 or mp3")
        return self.downloader.set_format(job_id, value)

    def cancel(self, job_id: str) -> bool:
        return self.downloader.cancel(job_id)

    def retry(self, job_id: str) -> bool:
        return self.downloader.retry(job_id)

    def remove(self, job_id: str) -> bool:
        return self.downloader.remove(job_id)

    def clear_finished(self, platform: str | None = None) -> int:
        return self.downloader.clear_finished(_platform(platform))

    def job_file(self, job_id: str) -> str | None:
        return self.downloader.job_file(job_id)

    # --- settings ------------------------------------------------------------------------------------
    def get_settings(self) -> dict:
        return self._settings.get()

    def set_settings(self, patch: dict) -> dict:
        new, changed = self._settings.update(patch or {})
        if changed:
            self._emit("settings", new)
            if "concurrency" in changed:
                self.downloader.set_concurrency(new["concurrency"])
            if "update_channel" in changed:
                self.updater.start_background(force=True)
        return new

    def update_engine(self) -> None:
        self.updater.start_background(force=True)

    # --- app self-update -----------------------------------------------------------------------------
    def check_updates(self) -> dict:
        self.selfupdate.check_background()
        return self.selfupdate.status()

    def download_update(self) -> None:
        self.selfupdate.download_background()

    def apply_update(self) -> bool:
        return self.selfupdate.apply()

    # --- auth ----------------------------------------------------------------------------------------
    def auth_status(self) -> dict:
        return authmod.read_auth_status(self.paths.cookies_file)

    def reload_auth(self) -> dict:
        status = self.auth_status()
        self._emit("auth", status)  # the login window shows its own success toast
        return status

    def logout(self) -> dict:
        authmod.delete_cookies(self.paths.cookies_file)
        status = self.auth_status()
        self._emit("auth", status)
        return status

    def import_cookies_file(self, path: str) -> dict:
        status = authmod.import_cookies_file(path, self.paths.cookies_file)  # ValueError (ru) on bad files
        self._emit("auth", status)
        self._emit("toast", {"level": "success", "text": "Instagram cookies imported"})
        return status

    # --- helpers for the cli / tests -----------------------------------------------------------------
    def wait_idle(self, timeout: float | None = None, poll: float = 0.5) -> bool:
        """Block until the updater pass is over and no job is queued or running."""
        deadline = None if timeout is None else time.monotonic() + timeout
        self.updater.wait_done(timeout)
        while True:
            jobs = self.downloader.jobs()
            stage = self.updater.status()["stage"]
            if stage == "error" and any(j["status"] == "queued" for j in jobs):
                return False
            if all(j["status"] in ("done", "error", "canceled") for j in jobs):
                return True
            if deadline is not None and time.monotonic() > deadline:
                return False
            time.sleep(poll)
