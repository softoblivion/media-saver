"""App self-update from GitHub Releases (ARCHITECTURE.md 5.7, docs/SELF_UPDATE.md).

Flow: check (GitHub API, fallback: the /releases/latest redirect) -> download (SHA256SUMS + size verified, `.part`
file renamed only after the checks) -> apply (the downloaded exe itself is started as `MediaSaver-<v>.exe
--apply-update <pid> <target>`: it waits for this process to exit, renames the old exe to `<exe>.old`, copies
itself over the target and starts it; `run_apply` below is that mode). No cmd script, no console window, no
pipes: a windowed exe does the swap. Any failure leaves the running exe untouched. All statuses are UpdateStatus dicts (docs/BRIDGE_API.md) pushed through `on_status`.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from typing import Any, Callable

from .paths import AppPaths, atomic_write_json
from .updater import UA, Net, OfflineError, parse_sums, replace_file, sha256_file

log = logging.getLogger("mediasaver.core.selfupdate")

REPO = "softoblivion/media-saver"
ASSET_NAME = "MediaSaver.exe"
SUMS_NAME = "SHA256SUMS"
HELPER_NAME = "apply-update.cmd"   # legacy helper of 2.0.0 / 2.1.0, removed on startup
MARKER_NAME = "applied.json"
APPLY_FLAG = "--apply-update"
APPLY_LOG = "apply.log"
APPLY_WAIT_S = 120.0
SWAP_ATTEMPTS = 40                  # x 0.5 s: antivirus may hold the old exe for a while

MSG_IDLE = "Updates are not checked yet"
MSG_CHECKING = "Checking for updates…"
MSG_UP_TO_DATE = "Media Saver is up to date"
MSG_OFFLINE = "No internet connection"
MSG_LIMIT = "GitHub limited the request, try again later"
MSG_BAD_VERSION = "Could not read the release version"
MSG_NO_RELEASE = "No release has been published yet"
MSG_NO_SUMS = "The release has no checksum file"
MSG_DAMAGED = "The downloaded file is damaged (checksum mismatch)"
MSG_SOURCE_ONLY = " (applies to the packaged MediaSaver.exe only)"

_CREATE_NEW_PROCESS_GROUP = 0x00000200
_CREATE_BREAKAWAY_FROM_JOB = 0x01000000
_SYNCHRONIZE = 0x00100000
_WAIT_OBJECT_0 = 0
_WAIT_TIMEOUT = 0x102

SpawnFn = Callable[[str, list, dict, str], None]   # (exe, argv, env, cwd)


class _Fail(Exception):
    """A user-facing failure: the message becomes the status message."""


# =====================================================================================================
# Pure helpers
# =====================================================================================================

def parse_version(text: str) -> tuple[int, ...] | None:
    """`v2.1.0` / `2.1` / `2.1.0-beta.1` -> (2, 1, 0) / (2, 1) / (2, 1, 0); None when nothing numeric leads the text."""
    m = re.match(r"^\s*[vV]?(\d+(?:\.\d+)*)", text or "")
    if not m:
        return None
    return tuple(int(p) for p in m.group(1).split("."))


def _padded(version: tuple[int, ...], length: int = 3) -> tuple[int, ...]:
    return version + (0,) * max(0, length - len(version))


def is_newer(latest: tuple[int, ...], current: tuple[int, ...]) -> bool:
    n = max(len(latest), len(current), 3)
    return _padded(latest, n) > _padded(current, n)


def _iso_to_epoch(text: Any) -> float | None:
    if not isinstance(text, str) or not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _is_limit_error(exc: BaseException) -> bool:
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code in (403, 429)
    return isinstance(exc, OfflineError) and re.search(r"HTTP (403|429)", str(exc)) is not None


def _net_message(exc: BaseException) -> str:
    return MSG_LIMIT if _is_limit_error(exc) else MSG_OFFLINE


def clean_env(base: dict[str, str] | None = None) -> dict[str, str]:
    """Environment for a process that must start a MediaSaver.exe of its own.

    A process started from a PyInstaller one-file exe inherits `_PYI_*` variables that make a second launch of the
    same exe reuse the first one's extraction folder; they are dropped and PYINSTALLER_RESET_ENVIRONMENT asks the new
    bootloader to behave as a fresh top-level process.
    """
    env = {k: v for k, v in (base if base is not None else os.environ).items()
           if not k.upper().startswith("_PYI") and k.upper() != "_MEIPASS2"}
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    return env


def _popen_detached(argv: list[str], env: dict, cwd: str | None) -> None:
    """Start a windowed exe that must outlive this process. No console is involved: MediaSaver.exe has none.

    CREATE_BREAKAWAY_FROM_JOB lets the child survive when this process sits in a job object that kills its tree
    (some launchers do that); it is refused when the job forbids breakaway, so the plain flags are the fallback.
    """
    kwargs: dict[str, Any] = dict(cwd=cwd, env=env, close_fds=True, stdin=subprocess.DEVNULL,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if sys.platform == "win32":
        try:
            subprocess.Popen(argv, creationflags=_CREATE_NEW_PROCESS_GROUP | _CREATE_BREAKAWAY_FROM_JOB, **kwargs)
            return
        except OSError:
            subprocess.Popen(argv, creationflags=_CREATE_NEW_PROCESS_GROUP, **kwargs)
            return
    subprocess.Popen(argv, start_new_session=True, **kwargs)


def spawn_apply(exe: str, argv: list[str], env: dict, cwd: str) -> None:
    """Start the downloaded exe in apply mode (default SpawnFn)."""
    _popen_detached([exe, *argv], env, cwd)


def wait_for_pid(pid: int, timeout_s: float = APPLY_WAIT_S) -> bool:
    """Block until the process is gone. True when it exited (or never existed), False on timeout."""
    deadline = time.monotonic() + timeout_s
    if sys.platform == "win32":
        import ctypes
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.OpenProcess(_SYNCHRONIZE, False, int(pid))
        if not handle:
            return True  # no such process (already gone) or not ours to watch
        try:
            while True:
                left = max(0.0, deadline - time.monotonic())
                rc = kernel32.WaitForSingleObject(handle, int(min(left, 1.0) * 1000))
                if rc == _WAIT_OBJECT_0:
                    return True
                if rc != _WAIT_TIMEOUT or left <= 0:
                    return rc == _WAIT_OBJECT_0
        finally:
            kernel32.CloseHandle(handle)
    while time.monotonic() < deadline:
        try:
            os.kill(int(pid), 0)
        except OSError:
            return True
        time.sleep(0.2)
    return False


def swap_exe(target: str, new_file: str, attempts: int = SWAP_ATTEMPTS, delay: float = 0.5) -> None:
    """Rename target -> target.old, copy new_file over target. Restores the old file when the copy fails."""
    old = target + ".old"
    last: OSError | None = None
    for _ in range(max(1, attempts)):
        try:
            if os.path.exists(old):
                os.remove(old)
            os.replace(target, old)
            break
        except OSError as exc:  # the old exe is still open (exiting, antivirus scan)
            last = exc
            time.sleep(delay)
    else:
        raise OSError(f"cannot move the running exe aside: {last}")
    try:
        shutil.copyfile(new_file, target)
        if os.path.getsize(target) != os.path.getsize(new_file):
            raise OSError("copy is incomplete")
    except OSError:
        try:
            if os.path.exists(target):
                os.remove(target)
            os.replace(old, target)
        except OSError:
            pass
        raise


def launch_target(target: str, env: dict | None = None) -> None:
    _popen_detached([target], env if env is not None else clean_env(), os.path.dirname(target))


def run_apply(args: list[str], *, new_file: str | None = None, log_path: str | None = None,
              wait: Callable[[int, float], bool] = wait_for_pid,
              launch: Callable[[str, dict], None] = launch_target) -> int:
    """`MediaSaver-<v>.exe --apply-update <pid> <target> [--no-launch]`: the apply mode of the downloaded exe.

    Runs with no window and no console. Steps are appended to `apply.log` next to this exe (the updates folder).
    Returns 0 when the target holds the new exe, 1 otherwise (the old exe is restored or was never touched).
    """
    new_file = new_file or os.path.abspath(sys.executable)
    log_path = log_path or os.path.join(os.path.dirname(new_file), APPLY_LOG)
    lines: list[str] = []

    def note(text: str) -> None:
        lines.append(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {text}")
        try:
            with open(log_path, "a", encoding="utf-8") as fh:
                fh.write(lines[-1] + "\n")
        except OSError:
            pass

    do_launch = "--no-launch" not in args
    positional = [a for a in args if not a.startswith("--")]
    if len(positional) < 2:
        note(f"bad arguments: {args!r}")
        return 1
    try:
        pid = int(positional[0])
    except ValueError:
        note(f"bad pid: {positional[0]!r}")
        return 1
    target = os.path.abspath(positional[1])
    note(f"apply {new_file} -> {target}, waiting for pid {pid}")
    started = time.monotonic()
    exited = wait(pid, APPLY_WAIT_S)
    note(f"pid {pid} {'exited' if exited else 'still running'} after {time.monotonic() - started:.1f} s")
    try:
        swap_exe(target, new_file)
    except OSError as exc:
        note(f"swap failed, old exe kept: {exc}")
        return 1
    note("swap done")
    if do_launch:
        try:
            launch(target, clean_env())
            note("started the new exe")
        except OSError as exc:
            note(f"could not start the new exe: {exc}")
            return 1
    return 0


class ReleaseNet(Net):
    """Net that sends the GitHub API headers on api.github.com requests (other hosts get the plain User-Agent)."""

    def _open(self, url: str, method: str = "GET"):
        headers = {"User-Agent": self.user_agent}
        if urllib.parse.urlparse(url).hostname == "api.github.com":
            headers["Accept"] = "application/vnd.github+json"
            headers["X-GitHub-Api-Version"] = "2022-11-28"
        return urllib.request.urlopen(urllib.request.Request(url, method=method, headers=headers), timeout=self.timeout)


# =====================================================================================================
# SelfUpdater
# =====================================================================================================

class SelfUpdater:
    def __init__(self, paths: AppPaths, current_version: str, on_status: Callable[[dict], None], *,
                 net: Any = None, repo: str = REPO, asset_name: str = ASSET_NAME,
                 exe_path: str | None = None, on_toast: Callable[[str, str], None] | None = None,
                 spawn: SpawnFn | None = None, urlopen: Callable[..., Any] | None = None) -> None:
        self.paths = paths
        self.current_version = current_version
        self.on_status = on_status
        self.on_toast = on_toast
        self.net = net if net is not None else ReleaseNet()
        self.repo = repo
        self.asset_name = asset_name
        self.exe_path = exe_path if exe_path is not None else paths.exe_path
        self._spawn = spawn if spawn is not None else spawn_apply
        self._urlopen = urlopen if urlopen is not None else urllib.request.urlopen
        self._lock = threading.RLock()
        self._running: str | None = None
        self._release: dict[str, Any] | None = None   # facts about the newer release found by the last check
        self._expected_sha: str | None = None         # SHA-256 of the verified file in updates/
        self._status: dict[str, Any] = {
            "stage": "idle", "current": current_version, "latest": None, "notes": None, "size": None,
            "published_at": None, "progress": None, "message": MSG_IDLE, "checked_at": None, "can_apply": False,
        }

    # --- status --------------------------------------------------------------------------------------
    def status(self) -> dict:
        with self._lock:
            return dict(self._status)

    def _set(self, **fields: Any) -> dict:
        with self._lock:
            self._status.update(fields)
            snapshot = dict(self._status)
        try:
            self.on_status(snapshot)
        except Exception:  # noqa: BLE001
            log.exception("update status callback failed")
        return snapshot

    def _fail(self, message: str, **fields: Any) -> dict:
        log.warning("self-update: %s", message)
        return self._set(stage="error", message=message, progress=None, can_apply=False, **fields)

    def _begin(self, what: str) -> bool:
        with self._lock:
            if self._running:
                return False
            self._running = what
            return True

    def _end(self) -> None:
        with self._lock:
            self._running = None

    def _final_path(self, version: str) -> str:
        return os.path.join(self.paths.updates_dir, f"MediaSaver-{version}.exe")

    # --- startup cleanup -----------------------------------------------------------------------------
    def startup(self) -> None:
        """Delete the previous exe, report a finished update once, remove stale downloads. Never raises."""
        try:
            self._startup()
        except Exception:  # noqa: BLE001
            log.exception("self-update startup failed")

    def _startup(self) -> None:
        if self.exe_path:
            old = self.exe_path + ".old"
            if os.path.exists(old):
                try:
                    os.remove(old)
                except OSError as exc:
                    log.info("cannot delete %s yet: %s", old, exc)
        updates = self.paths.updates_dir
        marker = os.path.join(updates, MARKER_NAME)
        if os.path.exists(marker):
            version = None
            try:
                with open(marker, "r", encoding="utf-8") as fh:
                    version = (json.load(fh) or {}).get("version")
            except (OSError, ValueError, AttributeError):
                pass
            if version == self.current_version:
                self._toast("success", f"Media Saver updated to v{version}")
            else:
                log.warning("update to %s did not apply (running %s)", version, self.current_version)
            self._remove(marker)
        self._remove(os.path.join(updates, HELPER_NAME))
        current = parse_version(self.current_version)
        for name in os.listdir(updates):
            m = re.match(r"^MediaSaver-(.+?)\.exe(\.part)?$", name)
            if not m:
                continue
            version = parse_version(m.group(1))
            if version is not None and current is not None and not is_newer(version, current):
                self._remove(os.path.join(updates, name))

    def _toast(self, level: str, text: str) -> None:
        if self.on_toast is None:
            return
        try:
            self.on_toast(level, text)
        except Exception:  # noqa: BLE001
            log.exception("toast callback failed")

    @staticmethod
    def _remove(path: str) -> None:
        try:
            os.remove(path)
        except OSError:
            pass

    # --- check ---------------------------------------------------------------------------------------
    def check_background(self) -> None:
        if self.status()["stage"] == "downloading" or not self._begin("check"):
            return
        self._set(stage="checking", message=MSG_CHECKING, progress=None)
        threading.Thread(target=self._run, args=(self._do_check,), name="selfupdate-check", daemon=True).start()

    def check(self) -> dict:
        """Blocking check; a second call while one runs (or a download) returns the current status."""
        if self.status()["stage"] == "downloading" or not self._begin("check"):
            return self.status()
        self._set(stage="checking", message=MSG_CHECKING, progress=None)
        self._run(self._do_check)
        return self.status()

    def _run(self, fn: Callable[[], None]) -> None:
        try:
            fn()
        except _Fail as exc:
            self._fail(str(exc))
        except Exception:  # noqa: BLE001
            log.exception("self-update step failed")
            self._fail("The update failed. See the log for details")
        finally:
            self._end()

    def _do_check(self) -> None:
        self._release = None
        try:
            self._check_release()
        except _Fail as exc:
            self._release = None
            self._fail(str(exc), latest=None, notes=None, size=None, published_at=None)

    def _check_release(self) -> None:
        release = self._fetch_release()
        latest_t = parse_version(release["version"])
        current_t = parse_version(self.current_version)
        if latest_t is None:
            raise _Fail(MSG_BAD_VERSION)
        now = time.time()
        if current_t is not None and not is_newer(latest_t, current_t):
            self._set(stage="up_to_date", latest=release["version"], notes=release["notes"], size=None,
                      published_at=release["published_at"], progress=None, message=MSG_UP_TO_DATE,
                      checked_at=now, can_apply=False)
            return
        info = self._describe_assets(release)
        self._release = info
        common = dict(latest=info["version"], notes=info["notes"], size=info["size"],
                      published_at=info["published_at"], progress=None, checked_at=now)
        if self._verify_existing(info):
            self._set(stage="ready", message=self._ready_message(info["version"]),
                      can_apply=self.exe_path is not None, **common)
            return
        self._set(stage="available", message=f"Version {info['version']} is available", can_apply=False, **common)

    def _ready_message(self, version: str) -> str:
        message = f"Version {version} is ready to install"
        return message if self.exe_path is not None else message + MSG_SOURCE_ONLY

    # --- release lookup ------------------------------------------------------------------------------
    def _api_url(self) -> str:
        return f"https://api.github.com/repos/{self.repo}/releases/latest"

    def _download_url(self, tag: str, name: str) -> str:
        return f"https://github.com/{self.repo}/releases/download/{urllib.parse.quote(tag)}/{name}"

    def _fetch_release(self) -> dict[str, Any]:
        """{"version", "tag", "notes", "published_at", "assets": {name: (url, size|None)}, "from_page": bool}."""
        try:
            data = json.loads(self.net.get_text(self._api_url()))
            return self._parse_api(data)
        except _Fail:
            raise
        except (OfflineError, urllib.error.URLError, OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
            log.info("release API failed (%s), trying the releases page", exc)
            return self._fetch_from_page(exc)

    def _parse_api(self, data: Any) -> dict[str, Any]:
        if not isinstance(data, dict) or not data.get("tag_name"):
            raise ValueError("unexpected API answer")
        tag = str(data["tag_name"])
        assets: dict[str, tuple[str, int | None]] = {}
        for item in data.get("assets") or []:
            name = item.get("name")
            if not name:
                continue
            size = item.get("size")
            assets[name] = (item.get("browser_download_url") or self._download_url(tag, name),
                            size if isinstance(size, int) and size > 0 else None)
        return {"version": self._clean_tag(tag), "tag": tag, "notes": (data.get("body") or "").strip() or None,
                "published_at": _iso_to_epoch(data.get("published_at")), "assets": assets, "from_page": False}

    @staticmethod
    def _clean_tag(tag: str) -> str:
        return tag[1:] if tag[:1] in ("v", "V") else tag

    def _fetch_from_page(self, api_error: BaseException) -> dict[str, Any]:
        url = f"https://github.com/{self.repo}/releases/latest"
        request = urllib.request.Request(url, headers={"User-Agent": UA})
        try:
            resp = self._urlopen(request, timeout=20)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise _Fail(MSG_NO_RELEASE) from exc
            raise _Fail(_net_message(exc)) from exc
        except (urllib.error.URLError, OSError) as exc:
            raise _Fail(MSG_OFFLINE if not _is_limit_error(api_error) else MSG_LIMIT) from exc
        try:
            final = resp.geturl()
        finally:
            try:
                resp.close()
            except Exception:  # noqa: BLE001
                pass
        m = re.search(r"/releases/tag/([^/?#]+)", final or "")
        if not m:
            raise _Fail(MSG_NO_RELEASE)
        tag = urllib.parse.unquote(m.group(1))
        assets = {self.asset_name: (self._download_url(tag, self.asset_name), None),
                  SUMS_NAME: (self._download_url(tag, SUMS_NAME), None)}
        return {"version": self._clean_tag(tag), "tag": tag, "notes": None, "published_at": None,
                "assets": assets, "from_page": True}

    def _describe_assets(self, release: dict[str, Any]) -> dict[str, Any]:
        entry = release["assets"].get(self.asset_name)
        if entry is None:
            raise _Fail(f"The release has no {self.asset_name}")
        asset_url, size = entry
        if release["from_page"] or size is None:
            probed = self._head_size(asset_url, missing_is_fatal=release["from_page"])
            size = size if size is not None else probed
        sums = release["assets"].get(SUMS_NAME)
        return {"version": release["version"], "tag": release["tag"], "notes": release["notes"],
                "published_at": release["published_at"], "size": size, "asset_url": asset_url,
                "sums_url": sums[0] if sums else self._download_url(release["tag"], SUMS_NAME)}

    def _head_size(self, url: str, missing_is_fatal: bool) -> int | None:
        request = urllib.request.Request(url, method="HEAD", headers={"User-Agent": UA})
        try:
            resp = self._urlopen(request, timeout=20)
        except urllib.error.HTTPError as exc:
            if exc.code == 404 and missing_is_fatal:
                raise _Fail(f"The release has no {self.asset_name}") from exc
            return None
        except (urllib.error.URLError, OSError):
            return None
        try:
            value = resp.headers.get("Content-Length") if getattr(resp, "headers", None) is not None else None
        finally:
            try:
                resp.close()
            except Exception:  # noqa: BLE001
                pass
        return int(value) if value and str(value).isdigit() and int(value) > 0 else None

    # --- checksums -----------------------------------------------------------------------------------
    def _expected_checksum(self, info: dict[str, Any]) -> str:
        try:
            text = self.net.get_text(info["sums_url"])
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise _Fail(MSG_NO_SUMS) from exc
            raise _Fail(_net_message(exc)) from exc
        except OfflineError as exc:
            raise _Fail(_net_message(exc)) from exc
        expected = parse_sums(text).get(self.asset_name)
        if not expected:
            raise _Fail(MSG_NO_SUMS)
        return expected

    def _verify_existing(self, info: dict[str, Any]) -> bool:
        """True when updates/MediaSaver-<latest>.exe is already there and matches the release (size + SHA-256)."""
        path = self._final_path(info["version"])
        if not os.path.isfile(path):
            return False
        try:
            if info["size"] is not None and os.path.getsize(path) != info["size"]:
                return False
            expected = self._expected_checksum(info)
        except (_Fail, OSError):
            return False
        if sha256_file(path) != expected:
            return False
        self._expected_sha = expected
        return True

    # --- download ------------------------------------------------------------------------------------
    def _can_download(self) -> bool:
        with self._lock:
            return self._release is not None and self._status["stage"] in ("available", "error")

    def download_background(self) -> None:
        if not self._can_download() or not self._begin("download"):
            return
        self._set(stage="downloading", progress=0.0, can_apply=False,
                  message=f"Downloading Media Saver {self._release['version']}… 0%")  # type: ignore[index]
        threading.Thread(target=self._run, args=(self._do_download,), name="selfupdate-download", daemon=True).start()

    def download(self) -> dict:
        if not self._can_download() or not self._begin("download"):
            return self.status()
        self._set(stage="downloading", progress=0.0, can_apply=False,
                  message=f"Downloading Media Saver {self._release['version']}… 0%")  # type: ignore[index]
        self._run(self._do_download)
        return self.status()

    def _do_download(self) -> None:
        info = self._release
        if info is None:
            raise _Fail("Check for updates first")
        version = info["version"]
        final = self._final_path(version)
        part = final + ".part"
        try:
            expected = self._expected_checksum(info)
            last_pct = [-1]

            def on_progress(done: int, total: int | None) -> None:
                total = total or info["size"]
                if not total:
                    return
                frac = min(1.0, done / total)
                pct = int(frac * 100)
                if pct != last_pct[0]:
                    last_pct[0] = pct
                    self._set(progress=frac, message=f"Downloading Media Saver {version}… {pct}%")

            try:
                actual = self.net.download(info["asset_url"], part, on_progress)
            except urllib.error.HTTPError as exc:
                if exc.code == 404:
                    raise _Fail(f"The release has no {self.asset_name}") from exc
                raise _Fail(_net_message(exc)) from exc
            except OfflineError as exc:
                raise _Fail(_net_message(exc)) from exc
            except OSError as exc:
                raise _Fail("Could not save the update file") from exc
            size_ok = True
            if info["size"] is not None:
                try:
                    size_ok = os.path.getsize(part) == info["size"]
                except OSError:
                    size_ok = False
            if not size_ok or (actual or "").lower() != expected:
                raise _Fail(MSG_DAMAGED)
            try:
                replace_file(part, final)
            except OSError as exc:
                raise _Fail("Could not save the update file") from exc
        except BaseException:
            self._remove(part)
            raise
        self._expected_sha = expected
        self._set(stage="ready", progress=None, message=self._ready_message(version),
                  can_apply=self.exe_path is not None)

    # --- apply ---------------------------------------------------------------------------------------
    def apply(self, launch: bool = True) -> bool:
        """Write the marker and start the downloaded exe in apply mode. True when the app must exit now."""
        with self._lock:
            stage = self._status["stage"]
            info = self._release
            expected = self._expected_sha
        if stage in ("checking", "downloading"):
            return False
        if stage != "ready" or info is None or not expected:
            self._fail("The update is not ready to install")
            return False
        if not self.exe_path:
            self._fail("The update can be installed only by the packaged MediaSaver.exe")
            return False
        new_file = self._final_path(info["version"])
        if not os.path.isfile(new_file):
            self._fail("The downloaded update file is missing")
            return False
        if sha256_file(new_file) != expected:
            self._remove(new_file)
            self._expected_sha = None
            self._fail(MSG_DAMAGED)
            return False
        try:
            updates = self.paths.updates_dir
            atomic_write_json(os.path.join(updates, MARKER_NAME), {"version": info["version"], "at": time.time()})
            argv = [APPLY_FLAG, str(os.getpid()), self.exe_path] + ([] if launch else ["--no-launch"])
            self._spawn(new_file, argv, clean_env(), updates)
        except Exception:  # noqa: BLE001
            log.exception("cannot start the update helper")
            self._remove(os.path.join(self.paths.updates_dir, MARKER_NAME))
            self._fail("Could not start the update helper")
            return False
        log.info("update helper started for %s (%s)", info["version"], new_file)
        return True
