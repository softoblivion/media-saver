"""Runtime data locations (ARCHITECTURE.md section 4).

The app is portable: nothing it writes depends on the user profile or on a drive letter. Everything resolves
from one root, the `media-saver_data` folder next to the executable (next to the project root when run from source):

- `%MEDIA_SAVER_HOME%` when set (tests and workers must set it to a scratch folder), otherwise
- `<app dir>\\media-saver_data`, where the app dir is the folder of `MediaSaver.exe` in a frozen build and the project root
  when running from source.

Folders are created on first access. The default output folder is the user's Videos folder plus
`Media-Saver` (the one deliberate exception to the portable layout: videos belong with the user's videos;
ARCHITECTURE.md section 4). Under `%MEDIA_SAVER_HOME%` it is `<home>\\Downloads` so tests never touch it.
"""
from __future__ import annotations

import os
import sys

DATA_DIR_NAME = "media-saver_data"  # <project>_data (workspace rule "Portable projects")
DOWNLOADS_DIR_NAME = "Downloads"      # output folder under %MEDIA_SAVER_HOME% (tests, scratch runs)
OUTPUT_DIR_NAME = "Media-Saver"       # subfolder of the user's Videos folder (default output)
_FOLDERID_VIDEOS = "{18989B1D-99B5-455B-841C-AB7C74E4DDFC}"
HOME_ENV = "MEDIA_SAVER_HOME"


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def exe_path() -> str | None:
    """Full path of the running MediaSaver.exe, None when running from source."""
    return os.path.abspath(sys.executable) if is_frozen() else None


def app_dir() -> str:
    """Folder of the executable (frozen) or the project root (source). Never the PyInstaller extraction dir."""
    exe = exe_path()
    if exe:
        return os.path.dirname(exe)
    return os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, os.pardir))


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
    return os.path.join(app_dir(), DATA_DIR_NAME)


def videos_dir() -> str:
    """The user's Videos folder: the Windows known folder (follows a redirected or localized library), else ~\\Videos."""
    if sys.platform == "win32":
        try:
            import ctypes
            import ctypes.wintypes
            import uuid

            class _GUID(ctypes.Structure):
                _fields_ = [("Data1", ctypes.c_uint32), ("Data2", ctypes.c_uint16), ("Data3", ctypes.c_uint16),
                            ("Data4", ctypes.c_ubyte * 8)]

            u = uuid.UUID(_FOLDERID_VIDEOS)
            guid = _GUID(u.time_low, u.time_mid, u.time_hi_version, (ctypes.c_ubyte * 8)(*u.bytes[8:]))
            out = ctypes.c_wchar_p()
            shell32 = ctypes.windll.shell32  # type: ignore[attr-defined]
            if shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(out)) == 0 and out.value:
                path = out.value
                ctypes.windll.ole32.CoTaskMemFree(out)  # type: ignore[attr-defined]
                return path
        except Exception:  # noqa: BLE001  fall back to the conventional location
            pass
    return os.path.join(os.path.expanduser("~"), "Videos")


def default_output_dir() -> str:
    """`<Videos>\\Media-Saver`; under `%MEDIA_SAVER_HOME%` it is `<home>\\Downloads` (tests, scratch runs)."""
    env = os.environ.get(HOME_ENV, "").strip()
    if env:
        return os.path.join(os.path.abspath(os.path.expandvars(os.path.expanduser(env))), DOWNLOADS_DIR_NAME)
    return os.path.join(videos_dir(), OUTPUT_DIR_NAME)


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

    @property
    def updates_dir(self) -> str:
        """Downloaded application updates and the swap helper (app/core/selfupdate.py)."""
        return _ensure(os.path.join(self.root, "updates"))

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

    @property
    def exe_path(self) -> str | None:
        """The running executable (frozen builds only); the self-updater replaces this file."""
        return exe_path()

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
