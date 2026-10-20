"""Runtime data locations (ARCHITECTURE.md section 4).

Everything resolves from one root: %REELS_SAVER_HOME% when set (tests set it), otherwise
%LOCALAPPDATA%\\ReelsSaver. Folders are created on first access.
"""
from __future__ import annotations

import os

APP_DIR_NAME = "ReelsSaver"
HOME_ENV = "REELS_SAVER_HOME"


def _ensure(path: str) -> str:
    os.makedirs(path, exist_ok=True)
    return path


def atomic_write_bytes(path: str, data: bytes) -> None:
    """Write via <path>.tmp + os.replace so readers never see a half-written file."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    last_err: OSError | None = None
    for _ in range(5):  # another process (antivirus, indexer) may hold the target briefly
        try:
            os.replace(tmp, path)
            return
        except PermissionError as exc:
            last_err = exc
            import time
            time.sleep(0.1)
    try:
        os.remove(tmp)
    except OSError:
        pass
    raise last_err  # type: ignore[misc]


def atomic_write_text(path: str, text: str) -> None:
    atomic_write_bytes(path, text.encode("utf-8"))


def atomic_write_json(path: str, obj) -> None:
    import json
    atomic_write_text(path, json.dumps(obj, ensure_ascii=False, indent=1))


def default_root() -> str:
    env = os.environ.get(HOME_ENV, "").strip()
    if env:
        return os.path.abspath(os.path.expandvars(os.path.expanduser(env)))
    base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Local")
    return os.path.join(base, APP_DIR_NAME)


def default_output_dir() -> str:
    profile = os.environ.get("USERPROFILE") or os.path.expanduser("~")
    return os.path.join(profile, "Downloads", "Reels Saver")


class AppPaths:
    """All runtime paths. Directory properties create the folder when read."""

    def __init__(self, root: str | None = None) -> None:
        self.root = _ensure(os.path.abspath(root) if root else default_root())

    # --- folders -------------------------------------------------------------------------------------
    @property
    def bin_dir(self) -> str:
        return _ensure(os.path.join(self.root, "bin"))

    @property
    def auth_dir(self) -> str:
        return _ensure(os.path.join(self.root, "auth"))

    @property
    def cache_dir(self) -> str:
        return _ensure(os.path.join(self.root, "cache"))

    @property
    def thumbs_dir(self) -> str:
        return _ensure(os.path.join(self.cache_dir, "thumbs"))

    @property
    def jobs_cache_dir(self) -> str:
        return _ensure(os.path.join(self.cache_dir, "jobs"))

    @property
    def logs_dir(self) -> str:
        return _ensure(os.path.join(self.root, "logs"))

    @property
    def job_logs_dir(self) -> str:
        return _ensure(os.path.join(self.logs_dir, "jobs"))

    @property
    def webview_dir(self) -> str:
        return _ensure(os.path.join(self.root, "webview"))

    # --- files ---------------------------------------------------------------------------------------
    @property
    def cookies_file(self) -> str:
        return os.path.join(self.auth_dir, "cookies.txt")

    @property
    def settings_file(self) -> str:
        return os.path.join(self.root, "settings.json")

    @property
    def history_file(self) -> str:
        return os.path.join(self.root, "history.json")

    @property
    def app_log(self) -> str:
        return os.path.join(self.logs_dir, "app.log")

    @property
    def updater_state_file(self) -> str:
        """Updater bookkeeping (last checks, ffmpeg download date); internal to app.core.updater."""
        return os.path.join(self.bin_dir, "versions.json")

    @property
    def default_output_dir(self) -> str:
        return default_output_dir()

    # --- per item ------------------------------------------------------------------------------------
    def binary(self, name: str) -> str:
        """Path of an own binary in bin/, e.g. binary('yt-dlp.exe')."""
        return os.path.join(self.bin_dir, name)

    def job_cache(self, job_id: str) -> str:
        return _ensure(os.path.join(self.jobs_cache_dir, job_id))

    def job_log(self, job_id: str) -> str:
        return os.path.join(self.job_logs_dir, f"{job_id}.log")

    def thumb_file(self, key: str) -> str:
        return os.path.join(self.thumbs_dir, f"{key}.jpg")

    def ensure_all(self) -> None:
        for prop in ("bin_dir", "auth_dir", "thumbs_dir", "jobs_cache_dir", "job_logs_dir", "webview_dir"):
            getattr(self, prop)

    def __repr__(self) -> str:
        return f"AppPaths({self.root!r})"
