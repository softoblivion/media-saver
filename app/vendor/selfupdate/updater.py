"""SelfUpdater: check GitHub Releases, download and verify the new exe, hand over to the apply mode.

Flow (docs/SELF_UPDATE.md): check (GitHub API, fallback: the /releases/latest redirect) -> download (SHA256SUMS +
size verified, `.part` file renamed only after the checks) -> apply (the downloaded exe itself is started as
`<App>-<v>.exe --apply-update <pid> <target>`; `applymode.run_apply` is that mode). Any failure leaves the running
exe untouched. Every status is an `UpdateStatus` dict pushed through `on_status`. Everything app-specific comes from
`UpdateConfig`.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Optional, TypedDict

from .applymode import APPLY_FLAG, NO_LAUNCH_FLAG, SpawnFn, clean_env, spawn_apply
from .fileio import atomic_write_json, parse_sums, replace_file, sha256_file
from .net import OfflineError, ReleaseNet

SUMS_NAME = "SHA256SUMS"
MARKER_NAME = "applied.json"       # in the updates folder: {"version", "at"} written right before the apply mode

MSG_IDLE = "Updates are not checked yet"
MSG_CHECKING = "Checking for updates…"
MSG_OFFLINE = "No internet connection"
MSG_LIMIT = "GitHub limited the request, try again later"
MSG_BAD_VERSION = "Could not read the release version"
MSG_NO_RELEASE = "No release has been published yet"
MSG_NO_SUMS = "The release has no checksum file"
MSG_DAMAGED = "The downloaded file is damaged (checksum mismatch)"
MSG_SAVE_FAILED = "Could not save the update file"
MSG_NOT_READY = "The update is not ready to install"
MSG_FILE_MISSING = "The downloaded update file is missing"
MSG_SPAWN_FAILED = "Could not start the update helper"
MSG_CHECK_FIRST = "Check for updates first"
MSG_STEP_FAILED = "The update failed. See the log for details"


class UpdateStatus(TypedDict):
    """The status payload (`SelfUpdater.status()`, `on_status`). Times are epoch seconds."""
    stage: str                   # idle | checking | up_to_date | available | downloading | ready | error
    current: str                 # running version
    latest: Optional[str]        # newest release version (no leading "v"), None until a check succeeds
    notes: Optional[str]         # release body (Markdown), None when unknown or empty
    size: Optional[int]          # byte size of the release exe, None when unknown
    published_at: Optional[float]
    progress: Optional[float]    # 0..1 while downloading, else None
    message: str                 # one user-facing sentence
    checked_at: Optional[float]  # time of the last successful check
    can_apply: bool              # True when stage is ready and the app runs as the packaged exe


@dataclass(frozen=True)
class UpdateConfig:
    """Everything the updater needs to know about the host app.

    repo:            GitHub "owner/name" whose Releases are checked.
    asset_name:      release asset of the one-file build, e.g. "MediaSaver.exe"; downloads are named
                     `<stem>-<version><ext>` in `updates_dir`.
    app_name:        display name used in the status messages and the "updated" toast.
    version:         running version (SemVer, no leading "v").
    updates_dir:     folder for downloads, the marker and the apply log (inside the app's data folder);
                     created on demand.
    exe_path:        the running exe in a frozen build, None when running from source (then nothing is applied).
    on_status:       receives a copy of every status change; exceptions are logged, never raised.
    on_toast:        (level, text) for the one-time "updated" notice after a restart; optional.
    net:             object with get_text(url) and download(url, dest, on_progress) -> sha256; default ReleaseNet.
    user_agent:      User-Agent of every request; default `<stem>/<version>`.
    release_url_env: name of an environment variable that, when set, replaces the GitHub API URL with the URL of a
                     JSON document in the API's "latest release" format (end-to-end tests against a local server;
                     the redirect fallback is skipped then).
    sums_name:       checksum asset name.
    legacy_files:    file names in updates_dir that startup() deletes (leftovers of older update schemes).
    logger_name:     logging.Logger name of this updater.
    """
    repo: str
    asset_name: str
    app_name: str
    version: str
    updates_dir: str
    exe_path: Optional[str]
    on_status: Callable[[dict], None]
    on_toast: Optional[Callable[[str, str], None]] = None
    net: Any = None
    user_agent: Optional[str] = None
    release_url_env: Optional[str] = None
    sums_name: str = SUMS_NAME
    legacy_files: tuple[str, ...] = ()
    logger_name: str = "selfupdate"

    @property
    def asset_stem(self) -> str:
        return os.path.splitext(self.asset_name)[0]

    @property
    def asset_ext(self) -> str:
        return os.path.splitext(self.asset_name)[1]

    def download_name(self, version: str) -> str:
        """`MediaSaver.exe` + `2.1.0` -> `MediaSaver-2.1.0.exe`."""
        return f"{self.asset_stem}-{version}{self.asset_ext}"

    @property
    def default_user_agent(self) -> str:
        return self.user_agent or f"{self.asset_stem}/{self.version}"

    @property
    def api_url(self) -> str:
        return f"https://api.github.com/repos/{self.repo}/releases/latest"

    @property
    def page_url(self) -> str:
        return f"https://github.com/{self.repo}/releases/latest"

    def asset_url(self, tag: str, name: str) -> str:
        return f"https://github.com/{self.repo}/releases/download/{urllib.parse.quote(tag)}/{name}"

    def release_url_override(self) -> str | None:
        if not self.release_url_env:
            return None
        return os.environ.get(self.release_url_env, "").strip() or None


@dataclass(frozen=True)
class Messages:
    """The status messages that name the app or its asset."""
    app_name: str
    asset_name: str

    @property
    def up_to_date(self) -> str:
        return f"{self.app_name} is up to date"

    @property
    def no_asset(self) -> str:
        return f"The release has no {self.asset_name}"

    @property
    def source_only(self) -> str:
        return f" (applies to the packaged {self.asset_name} only)"

    @property
    def packaged_only(self) -> str:
        return f"The update can be installed only by the packaged {self.asset_name}"

    @staticmethod
    def available(version: str) -> str:
        return f"Version {version} is available"

    def downloading(self, version: str, pct: int) -> str:
        return f"Downloading {self.app_name} {version}… {pct}%"

    def ready(self, version: str, can_apply: bool) -> str:
        message = f"Version {version} is ready to install"
        return message if can_apply else message + self.source_only

    def updated(self, version: str) -> str:
        return f"{self.app_name} updated to v{version}"


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


# =====================================================================================================
# SelfUpdater
# =====================================================================================================

class SelfUpdater:
    def __init__(self, config: UpdateConfig, *, spawn: SpawnFn | None = None,
                 urlopen: Callable[..., Any] | None = None) -> None:
        self.config = config
        self.msg = Messages(config.app_name, config.asset_name)
        self.log = logging.getLogger(config.logger_name)
        self.current_version = config.version
        self.repo = config.repo
        self.asset_name = config.asset_name
        self.exe_path = config.exe_path
        self.on_status = config.on_status
        self.on_toast = config.on_toast
        self.user_agent = config.default_user_agent
        self.net = config.net if config.net is not None else ReleaseNet(self.user_agent)
        self._spawn = spawn if spawn is not None else spawn_apply
        self._urlopen = urlopen if urlopen is not None else urllib.request.urlopen
        self._lock = threading.RLock()
        self._running: str | None = None
        self._release: dict[str, Any] | None = None   # facts about the newer release found by the last check
        self._expected_sha: str | None = None         # SHA-256 of the verified file in updates/
        self._status: dict[str, Any] = {
            "stage": "idle", "current": config.version, "latest": None, "notes": None, "size": None,
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
            self.log.exception("update status callback failed")
        return snapshot

    def _fail(self, message: str, **fields: Any) -> dict:
        self.log.warning("self-update: %s", message)
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

    def _updates_dir(self) -> str:
        os.makedirs(self.config.updates_dir, exist_ok=True)
        return self.config.updates_dir

    def _final_path(self, version: str) -> str:
        return os.path.join(self._updates_dir(), self.config.download_name(version))

    # --- startup cleanup -----------------------------------------------------------------------------
    def startup(self) -> None:
        """Delete the previous exe, report a finished update once, remove stale downloads. Never raises."""
        try:
            self._startup()
        except Exception:  # noqa: BLE001
            self.log.exception("self-update startup failed")

    def _startup(self) -> None:
        if self.exe_path:
            old = self.exe_path + ".old"
            if os.path.exists(old):
                try:
                    os.remove(old)
                except OSError as exc:
                    self.log.info("cannot delete %s yet: %s", old, exc)
        updates = self._updates_dir()
        marker = os.path.join(updates, MARKER_NAME)
        if os.path.exists(marker):
            version = None
            try:
                with open(marker, "r", encoding="utf-8") as fh:
                    version = (json.load(fh) or {}).get("version")
            except (OSError, ValueError, AttributeError):
                pass
            if version == self.current_version:
                self._toast("success", self.msg.updated(version))
            else:
                self.log.warning("update to %s did not apply (running %s)", version, self.current_version)
            self._remove(marker)
        for name in self.config.legacy_files:
            self._remove(os.path.join(updates, name))
        current = parse_version(self.current_version)
        pattern = re.compile(rf"^{re.escape(self.config.asset_stem)}-(.+?){re.escape(self.config.asset_ext)}(\.part)?$")
        for name in os.listdir(updates):
            m = pattern.match(name)
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
            self.log.exception("toast callback failed")

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
            self.log.exception("self-update step failed")
            self._fail(MSG_STEP_FAILED)
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
                      published_at=release["published_at"], progress=None, message=self.msg.up_to_date,
                      checked_at=now, can_apply=False)
            return
        info = self._describe_assets(release)
        self._release = info
        common = dict(latest=info["version"], notes=info["notes"], size=info["size"],
                      published_at=info["published_at"], progress=None, checked_at=now)
        if self._verify_existing(info):
            self._set(stage="ready", message=self.msg.ready(info["version"], self.exe_path is not None),
                      can_apply=self.exe_path is not None, **common)
            return
        self._set(stage="available", message=self.msg.available(info["version"]), can_apply=False, **common)

    # --- release lookup ------------------------------------------------------------------------------
    def _fetch_release(self) -> dict[str, Any]:
        """{"version", "tag", "notes", "published_at", "assets": {name: (url, size|None)}, "from_page": bool}."""
        override = self.config.release_url_override()
        try:
            data = json.loads(self.net.get_text(override or self.config.api_url))
            return self._parse_api(data)
        except _Fail:
            raise
        except (OfflineError, urllib.error.URLError, OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
            if override:
                raise _Fail(self._override_message(exc)) from exc
            self.log.info("release API failed (%s), trying the releases page", exc)
            return self._fetch_from_page(exc)

    @staticmethod
    def _override_message(exc: BaseException) -> str:
        if isinstance(exc, urllib.error.HTTPError) and exc.code == 404:
            return MSG_NO_RELEASE
        if isinstance(exc, (ValueError, KeyError, TypeError, AttributeError)):
            return MSG_NO_RELEASE
        return _net_message(exc)

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
            assets[name] = (item.get("browser_download_url") or self.config.asset_url(tag, name),
                            size if isinstance(size, int) and size > 0 else None)
        return {"version": self._clean_tag(tag), "tag": tag, "notes": (data.get("body") or "").strip() or None,
                "published_at": _iso_to_epoch(data.get("published_at")), "assets": assets, "from_page": False}

    @staticmethod
    def _clean_tag(tag: str) -> str:
        return tag[1:] if tag[:1] in ("v", "V") else tag

    def _fetch_from_page(self, api_error: BaseException) -> dict[str, Any]:
        request = urllib.request.Request(self.config.page_url, headers={"User-Agent": self.user_agent})
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
        sums = self.config.sums_name
        assets = {self.asset_name: (self.config.asset_url(tag, self.asset_name), None),
                  sums: (self.config.asset_url(tag, sums), None)}
        return {"version": self._clean_tag(tag), "tag": tag, "notes": None, "published_at": None,
                "assets": assets, "from_page": True}

    def _describe_assets(self, release: dict[str, Any]) -> dict[str, Any]:
        entry = release["assets"].get(self.asset_name)
        if entry is None:
            raise _Fail(self.msg.no_asset)
        asset_url, size = entry
        if release["from_page"] or size is None:
            probed = self._head_size(asset_url, missing_is_fatal=release["from_page"])
            size = size if size is not None else probed
        sums = release["assets"].get(self.config.sums_name)
        return {"version": release["version"], "tag": release["tag"], "notes": release["notes"],
                "published_at": release["published_at"], "size": size, "asset_url": asset_url,
                "sums_url": sums[0] if sums else self.config.asset_url(release["tag"], self.config.sums_name)}

    def _head_size(self, url: str, missing_is_fatal: bool) -> int | None:
        request = urllib.request.Request(url, method="HEAD", headers={"User-Agent": self.user_agent})
        try:
            resp = self._urlopen(request, timeout=20)
        except urllib.error.HTTPError as exc:
            if exc.code == 404 and missing_is_fatal:
                raise _Fail(self.msg.no_asset) from exc
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
        """True when updates/<stem>-<latest><ext> is already there and matches the release (size + SHA-256)."""
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
                  message=self.msg.downloading(self._release["version"], 0))  # type: ignore[index]
        threading.Thread(target=self._run, args=(self._do_download,), name="selfupdate-download", daemon=True).start()

    def download(self) -> dict:
        if not self._can_download() or not self._begin("download"):
            return self.status()
        self._set(stage="downloading", progress=0.0, can_apply=False,
                  message=self.msg.downloading(self._release["version"], 0))  # type: ignore[index]
        self._run(self._do_download)
        return self.status()

    def _do_download(self) -> None:
        info = self._release
        if info is None:
            raise _Fail(MSG_CHECK_FIRST)
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
                    self._set(progress=frac, message=self.msg.downloading(version, pct))

            try:
                actual = self.net.download(info["asset_url"], part, on_progress)
            except urllib.error.HTTPError as exc:
                if exc.code == 404:
                    raise _Fail(self.msg.no_asset) from exc
                raise _Fail(_net_message(exc)) from exc
            except OfflineError as exc:
                raise _Fail(_net_message(exc)) from exc
            except OSError as exc:
                raise _Fail(MSG_SAVE_FAILED) from exc
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
                raise _Fail(MSG_SAVE_FAILED) from exc
        except BaseException:
            self._remove(part)
            raise
        self._expected_sha = expected
        self._set(stage="ready", progress=None, message=self.msg.ready(version, self.exe_path is not None),
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
            self._fail(MSG_NOT_READY)
            return False
        if not self.exe_path:
            self._fail(self.msg.packaged_only)
            return False
        new_file = self._final_path(info["version"])
        if not os.path.isfile(new_file):
            self._fail(MSG_FILE_MISSING)
            return False
        if sha256_file(new_file) != expected:
            self._remove(new_file)
            self._expected_sha = None
            self._fail(MSG_DAMAGED)
            return False
        updates = self.config.updates_dir
        try:
            self._updates_dir()
            atomic_write_json(os.path.join(updates, MARKER_NAME), {"version": info["version"], "at": time.time()})
            argv = [APPLY_FLAG, str(os.getpid()), self.exe_path] + ([] if launch else [NO_LAUNCH_FLAG])
            self._spawn(new_file, argv, clean_env(), updates)
        except Exception:  # noqa: BLE001
            self.log.exception("cannot start the update helper")
            self._remove(os.path.join(updates, MARKER_NAME))
            self._fail(MSG_SPAWN_FAILED)
            return False
        self.log.info("update helper started for %s (%s)", info["version"], new_file)
        return True
