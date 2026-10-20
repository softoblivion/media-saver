"""Bootstrap and update of the download engines on every launch (ARCHITECTURE.md 5.1).

Components:
- yt-dlp.exe (nightly or stable channel). Up-to-date check: SHA-256 of the local exe against the channel's
  `SHA2-256SUMS` (a plain release asset, no GitHub API quota). Differs -> `yt-dlp.exe --update-to <channel>`;
  if that fails (file lock, API quota) a fresh copy is downloaded, verified and swapped in with os.replace.
- gallery-dl.exe (fallback engine, stable channel from codeberg.org, the project's release host since 2026).
  Same SHA check against `SHA256SUMS`; differs -> `gallery-dl.exe -U`; still differs -> fresh download.
- ffmpeg.exe + ffprobe.exe: bin/ -> PATH -> download yt-dlp/FFmpeg-Builds win64-gpl zip (extract the two exes
  only). The own copy is re-checked every 30 days.

All downloads go to `<name>.part`, are verified against the published SHA-256 and then replace the target.
While a binary is being replaced the BinaryGate is held exclusively, so no job starts a subprocess.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import socket
import threading
import time
import urllib.error
import urllib.request
import zipfile
from typing import Any, Callable

from .downloader import BinaryGate, ProcessRunner
from .paths import AppPaths, atomic_write_json

log = logging.getLogger("mediasaver.core.updater")

YTDLP_URLS = {
    "nightly": "https://github.com/yt-dlp/yt-dlp-nightly-builds/releases/latest/download/",
    "stable": "https://github.com/yt-dlp/yt-dlp/releases/latest/download/",
}
YTDLP_SUMS = "SHA2-256SUMS"
GALLERYDL_BASE = "https://codeberg.org/mikf/gallery-dl/releases/download/latest/"
GALLERYDL_SUMS = "SHA256SUMS"
FFMPEG_BASE = "https://github.com/yt-dlp/FFmpeg-Builds/releases/download/latest/"
FFMPEG_ZIP = "ffmpeg-master-latest-win64-gpl.zip"
FFMPEG_SUMS = "checksums.sha256"
FFMPEG_REFRESH_S = 30 * 24 * 3600
CONNECTIVITY_URLS = ("https://github.com/", "https://codeberg.org/")
UPDATE_TIMEOUT = 90.0
NO_SYSTEM_FFMPEG_ENV = "MEDIA_SAVER_NO_SYSTEM_FFMPEG"

UA = "MediaSaver/1 (+https://github.com/yt-dlp/yt-dlp; Windows)"


class OfflineError(Exception):
    """The network is unreachable (or the host refuses us: GitHub 403/429)."""


class VerifyError(Exception):
    pass


# =====================================================================================================
# HTTP
# =====================================================================================================

class Net:
    """urllib with a User-Agent, timeouts and retries."""

    def __init__(self, user_agent: str = UA, timeout: float = 20.0, retries: int = 3) -> None:
        self.user_agent, self.timeout, self.retries = user_agent, timeout, retries

    def _open(self, url: str, method: str = "GET"):
        req = urllib.request.Request(url, method=method, headers={"User-Agent": self.user_agent})
        return urllib.request.urlopen(req, timeout=self.timeout)

    def _retry(self, fn, url: str):
        last: Exception | None = None
        for attempt in range(self.retries):
            try:
                return fn()
            except urllib.error.HTTPError as exc:
                if exc.code in (403, 429):
                    raise OfflineError(f"{url}: HTTP {exc.code}") from exc
                if exc.code < 500:
                    raise
                last = exc
            except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError, OSError) as exc:
                last = exc
            time.sleep(min(4.0, 1.0 + attempt * 1.5))
        raise OfflineError(f"{url}: {last}")

    def get_bytes(self, url: str, limit: int = 4 << 20) -> bytes:
        def go():
            with self._open(url) as resp:
                return resp.read(limit)
        return self._retry(go, url)

    def get_text(self, url: str) -> str:
        return self.get_bytes(url).decode("utf-8", errors="replace")

    def reachable(self, url: str) -> bool:
        try:
            with urllib.request.urlopen(urllib.request.Request(url, method="HEAD", headers={"User-Agent": self.user_agent}),
                                        timeout=8):
                return True
        except urllib.error.HTTPError:
            return True  # the host answered
        except Exception:  # noqa: BLE001
            return False

    def download(self, url: str, dest: str, on_progress: Callable[[int, int | None], None] | None = None) -> str:
        """Stream to dest (normally a .part path); returns its SHA-256 hex."""
        def go():
            sha = hashlib.sha256()
            with self._open(url) as resp, open(dest, "wb") as fh:
                total = resp.headers.get("Content-Length")
                total_i = int(total) if total and total.isdigit() else None
                done = 0
                last = 0.0
                while True:
                    chunk = resp.read(256 * 1024)
                    if not chunk:
                        break
                    fh.write(chunk)
                    sha.update(chunk)
                    done += len(chunk)
                    now = time.monotonic()
                    if on_progress and now - last > 0.2:
                        last = now
                        on_progress(done, total_i)
                if total_i is not None and done < total_i:
                    raise ConnectionError(f"short read {done}/{total_i}")
            if on_progress:
                on_progress(done, total_i)
            return sha.hexdigest()
        return self._retry(go, url)


def parse_sums(text: str) -> dict[str, str]:
    """`<sha256>  <name>` (or `<sha256> *<name>`) lines -> {name: sha}."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        m = re.match(r"^\s*([0-9a-fA-F]{64})\s+\*?(.+?)\s*$", line)
        if m:
            out.setdefault(m.group(2), m.group(1).lower())
    return out


def sha256_file(path: str) -> str | None:
    try:
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def replace_file(src: str, dest: str, attempts: int = 10) -> None:
    """os.replace with retries: a just-finished process or antivirus may still hold the target."""
    last: OSError | None = None
    for _ in range(attempts):
        try:
            os.replace(src, dest)
            return
        except PermissionError as exc:
            last = exc
            time.sleep(0.5)
    raise last  # type: ignore[misc]


def parse_ffmpeg_version(text: str) -> str | None:
    m = re.search(r"version\s+(\S+)", text or "")
    if not m:
        return None
    ver = m.group(1)
    short = re.match(r"^n?(\d+(?:\.\d+)+)", ver)
    return short.group(1) if short else ver[:40]


def parse_ytdlp_update(text: str) -> str:
    """Classify `yt-dlp --update-to` output: "updated" | "uptodate" | "failed"."""
    if re.search(r"Updated yt-dlp to|Updating to .*\n.*Updated", text, re.I):
        return "updated"
    if re.search(r"is up to date|already (on|at) the (latest|requested)", text, re.I):
        return "uptodate"
    return "failed"


def parse_gallerydl_update(text: str) -> str:
    if re.search(r"Updated to|Successfully updated|update(d)? to version", text, re.I):
        return "updated"
    if re.search(r"is up to date", text, re.I):
        return "uptodate"
    return "failed"


# =====================================================================================================
# Updater
# =====================================================================================================

class Updater:
    def __init__(self, paths: AppPaths, get_settings: Callable[[], dict], on_status: Callable[[dict], None], *,
                 gate: BinaryGate | None = None, runner: ProcessRunner | None = None, net: Net | None = None,
                 use_system_ffmpeg: bool | None = None) -> None:
        self.paths = paths
        self.get_settings = get_settings
        self.on_status = on_status
        self.gate = gate or BinaryGate()
        self.runner = runner or ProcessRunner()
        self.net = net or Net()
        if use_system_ffmpeg is None:
            use_system_ffmpeg = os.environ.get(NO_SYSTEM_FFMPEG_ENV, "") not in ("1", "true", "yes")
        self.use_system_ffmpeg = use_system_ffmpeg
        self._lock = threading.RLock()
        self._run_lock = threading.Lock()
        self._pending_force = False
        self._thread: threading.Thread | None = None
        self._self_healed = False
        self._done_event = threading.Event()
        self._state = self._load_state()
        self._bins: dict[str, str | None] = {"yt-dlp": None, "gallery-dl": None, "ffmpeg": None, "ffprobe": None}
        self._status: dict[str, Any] = {
            "stage": "starting", "message": "Starting the engine…", "progress": None,
            "components": {
                "yt-dlp": {"version": None, "channel": None, "updated": False, "ok": False},
                "gallery-dl": {"version": None, "updated": False, "ok": False},
                "ffmpeg": {"version": None, "source": None, "ok": False},
            },
            "last_check": self._state.get("last_check"),
        }
        self._detect_existing()

    # --- state ---------------------------------------------------------------------------------------
    def _load_state(self) -> dict:
        try:
            with open(self.paths.updater_state_file, "r", encoding="utf-8") as fh:
                data = json.load(fh)
                return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save_state(self) -> None:
        try:
            atomic_write_json(self.paths.updater_state_file, self._state)
        except OSError as exc:
            log.warning("cannot save updater state: %s", exc)

    def status(self) -> dict:
        with self._lock:
            return json.loads(json.dumps(self._status))

    def binaries(self) -> dict[str, str | None]:
        with self._lock:
            return dict(self._bins)

    def usable(self) -> bool:
        with self._lock:
            return (self._status["stage"] in ("ready", "offline") and bool(self._bins.get("yt-dlp"))
                    and not self.gate.replacing)

    def wait_done(self, timeout: float | None = None) -> bool:
        return self._done_event.wait(timeout)

    def _emit(self, **fields: Any) -> None:
        with self._lock:
            self._status.update(fields)
            snapshot = json.loads(json.dumps(self._status))
        try:
            self.on_status(snapshot)
        except Exception:  # noqa: BLE001
            log.exception("status callback failed")

    def _component(self, name: str, **fields: Any) -> None:
        with self._lock:
            self._status["components"][name].update(fields)

    # --- detection -----------------------------------------------------------------------------------
    def _version_of(self, exe: str, args: list[str]) -> str | None:
        res = self.runner.run([exe, *args], timeout=30)
        if res.returncode != 0:
            return None
        line = (res.stdout or res.stderr).strip().splitlines()
        return line[0].strip() if line else None

    def _detect_ffmpeg(self) -> None:
        own_ff, own_fp = self.paths.binary("ffmpeg.exe"), self.paths.binary("ffprobe.exe")
        if os.path.isfile(own_ff) and os.path.isfile(own_fp):
            ff, fp, source = own_ff, own_fp, "bundled"
        elif self.use_system_ffmpeg and shutil.which("ffmpeg") and shutil.which("ffprobe"):
            ff, fp, source = shutil.which("ffmpeg"), shutil.which("ffprobe"), "system"
        else:
            with self._lock:
                self._bins["ffmpeg"] = self._bins["ffprobe"] = None
            self._component("ffmpeg", version=None, source=None, ok=False)
            return
        res = self.runner.run([ff, "-hide_banner", "-version"], timeout=30)
        version = parse_ffmpeg_version(res.stdout) if res.returncode == 0 else None
        with self._lock:
            self._bins["ffmpeg"], self._bins["ffprobe"] = (ff, fp) if version else (None, None)
        self._component("ffmpeg", version=version, source=source, ok=bool(version))

    def _detect_ytdlp(self) -> None:
        exe = self.paths.binary("yt-dlp.exe")
        version = self._version_of(exe, ["--version"]) if os.path.isfile(exe) else None
        with self._lock:
            self._bins["yt-dlp"] = exe if version else None
        self._component("yt-dlp", version=version, ok=bool(version),
                        channel=self._state.get("yt-dlp", {}).get("channel") if version else None)

    def _detect_gallerydl(self) -> None:
        exe = self.paths.binary("gallery-dl.exe")
        version = self._version_of(exe, ["--version"]) if os.path.isfile(exe) else None
        with self._lock:
            self._bins["gallery-dl"] = exe if version else None
        self._component("gallery-dl", version=version, ok=bool(version))

    def _detect_existing(self) -> None:
        try:
            self._detect_ytdlp()
            self._detect_gallerydl()
            self._detect_ffmpeg()
        except Exception:  # noqa: BLE001
            log.exception("binary detection failed")

    # --- running -------------------------------------------------------------------------------------
    def start_background(self, force: bool = False) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                self._pending_force = True  # a request during a pass runs one more (forced) pass
                return
            self._done_event.clear()
            self._thread = threading.Thread(target=self._thread_main, args=(force,), name="ms-updater", daemon=True)
            self._thread.start()

    def _thread_main(self, force: bool) -> None:
        try:
            while True:
                self.run(force=force)
                with self._lock:
                    if not self._pending_force:
                        break
                    self._pending_force = False
                    force = True
        finally:
            self._done_event.set()

    def self_heal(self) -> bool:
        """One forced yt-dlp update per app session after an extractor error. True when it ran now."""
        with self._lock:
            if self._self_healed:
                return False
            self._self_healed = True
        log.info("self-heal: forced engine update")
        self.run(force=True, only_ytdlp=True)
        return True

    def run(self, force: bool = False, only_ytdlp: bool = False) -> dict:
        """Blocking update pass. `force` ignores auto_update=false and the ffmpeg refresh interval."""
        with self._run_lock:
            try:
                self._run(force, only_ytdlp)
            except Exception as exc:  # noqa: BLE001
                log.exception("updater crashed")
                self._detect_existing()
                ok = bool(self._bins.get("yt-dlp"))
                self._emit(stage="ready" if ok else "error", progress=None,
                           message="Update failed, using the current version" if ok
                           else f"Could not prepare the engine: {exc}")
            return self.status()

    def _run(self, force: bool, only_ytdlp: bool) -> None:
        settings = self.get_settings()
        channel = settings.get("update_channel", "nightly")
        auto = bool(settings.get("auto_update", True)) or force
        self._emit(stage="checking", message="Checking the engine…", progress=None)
        self._detect_existing()
        missing = [n for n in ("yt-dlp", "ffmpeg") if not self._bins.get(n)]
        if not missing and not auto:
            self._finish(offline=False)
            return
        online = any(self.net.reachable(u) for u in CONNECTIVITY_URLS)
        if not online:
            self._finish(offline=True)
            return
        offline = False
        steps = [("yt-dlp", lambda: self._update_ytdlp(channel, auto))]
        if not only_ytdlp:
            steps.append(("ffmpeg", lambda: self._update_ffmpeg(force)))
            steps.append(("gallery-dl", lambda: self._update_gallerydl(auto)))
        for name, step in steps:
            try:
                step()
            except OfflineError as exc:
                log.warning("%s: offline (%s)", name, exc)
                offline = True
            except Exception as exc:  # noqa: BLE001  one component must not block the others
                log.exception("%s update failed: %s", name, exc)
        self._state["last_check"] = time.time()
        self._save_state()
        with self._lock:
            self._status["last_check"] = self._state["last_check"]
        self._finish(offline=offline)

    def _finish(self, offline: bool) -> None:
        self._detect_existing()
        comps = self.status()["components"]
        ytdlp_ok = comps["yt-dlp"]["ok"]
        if not ytdlp_ok:
            msg = ("No internet: the download engine is not installed" if offline
                   else "Could not install yt-dlp. Click “Update engine”")
            self._emit(stage="error", message=msg, progress=None)
            return
        if offline:
            self._emit(stage="offline", message="No internet: using the installed version", progress=None)
            return
        parts = [f"yt-dlp {comps['yt-dlp']['version']}"]
        if not comps["ffmpeg"]["ok"]:
            self._emit(stage="ready", progress=None,
                       message="Engine ready, but ffmpeg was not found: merging and MP3 need it; "
                               "the next engine update fetches it")
            return
        updated = comps["yt-dlp"]["updated"]
        self._emit(stage="ready", progress=None,
                   message=("Engine updated: " if updated else "Engine ready: ") + ", ".join(parts))

    def _with_gate(self, fn: Callable[[], Any]) -> Any:
        if not self.gate.acquire_exclusive(timeout=300):
            raise RuntimeError("binary is busy: running jobs did not finish in time")
        try:
            return fn()
        finally:
            self.gate.release_exclusive()

    def _download_verified(self, url: str, dest: str, expected_sha: str, label: str) -> None:
        part = dest + ".part"

        def progress(done: int, total: int | None) -> None:
            frac = (done / total) if total else None
            pct = f" {int(frac * 100)}%" if frac is not None else ""
            self._emit(stage="downloading", message=f"Downloading {label}…{pct}", progress=frac)

        try:
            got = self.net.download(url, part, progress)
            if got.lower() != expected_sha.lower():
                raise VerifyError(f"{label}: SHA-256 mismatch ({got} != {expected_sha})")
        except BaseException:
            try:
                os.remove(part)
            except OSError:
                pass
            raise
        self._with_gate(lambda: replace_file(part, dest))

    # --- yt-dlp --------------------------------------------------------------------------------------
    def _update_ytdlp(self, channel: str, auto: bool) -> None:
        exe = self.paths.binary("yt-dlp.exe")
        base = YTDLP_URLS.get(channel, YTDLP_URLS["nightly"])
        sums = parse_sums(self.net.get_text(base + YTDLP_SUMS))
        expected = sums.get("yt-dlp.exe")
        if not expected:
            raise RuntimeError("yt-dlp.exe is missing from SHA2-256SUMS")
        state = self._state.setdefault("yt-dlp", {})
        if not os.path.isfile(exe) or not self._bins.get("yt-dlp"):
            self._download_verified(base + "yt-dlp.exe", exe, expected, "yt-dlp")
            state.update(channel=channel, installed_at=time.time())
            self._detect_ytdlp()
            self._component("yt-dlp", updated=True, channel=channel)
            return
        if not auto:
            return
        local = sha256_file(exe)
        if local == expected:
            state["channel"] = channel
            self._component("yt-dlp", updated=False, channel=channel)
            return
        old_version = self._status["components"]["yt-dlp"]["version"]
        self._emit(stage="updating", message="Updating yt-dlp…", progress=None)

        def self_update() -> str:
            res = self.runner.run([exe, "--no-config", "--update-to", channel], timeout=UPDATE_TIMEOUT)
            text = f"{res.stdout}\n{res.stderr}"
            log.info("yt-dlp --update-to %s: rc=%s %s", channel, res.returncode, text.strip()[-500:])
            return parse_ytdlp_update(text) if res.returncode == 0 or "Updated" in text else "failed"

        outcome = self._with_gate(self_update)
        if outcome == "failed" or sha256_file(exe) != expected:
            # self-update failed (file lock, API quota) or landed on another build: take the verified copy
            self._download_verified(base + "yt-dlp.exe", exe, expected, "yt-dlp")
        self._cleanup_old(exe)
        state.update(channel=channel, updated_at=time.time())
        self._detect_ytdlp()
        new_version = self._status["components"]["yt-dlp"]["version"]
        self._component("yt-dlp", updated=new_version != old_version, channel=channel)

    @staticmethod
    def _cleanup_old(exe: str) -> None:
        for suffix in (".old", ".new"):
            try:
                os.remove(exe + suffix)
            except OSError:
                pass

    # --- gallery-dl ----------------------------------------------------------------------------------
    def _update_gallerydl(self, auto: bool) -> None:
        exe = self.paths.binary("gallery-dl.exe")
        sums = parse_sums(self.net.get_text(GALLERYDL_BASE + GALLERYDL_SUMS))
        expected = sums.get("gallery-dl.exe")
        if not expected:
            raise RuntimeError("gallery-dl.exe is missing from SHA256SUMS")
        if not os.path.isfile(exe) or not self._bins.get("gallery-dl"):
            self._download_verified(GALLERYDL_BASE + "gallery-dl.exe", exe, expected, "gallery-dl")
            self._detect_gallerydl()
            self._component("gallery-dl", updated=True)
            return
        if not auto or sha256_file(exe) == expected:
            self._component("gallery-dl", updated=False)
            return
        old = self._status["components"]["gallery-dl"]["version"]
        self._emit(stage="updating", message="Updating gallery-dl…", progress=None)

        def self_update() -> None:
            res = self.runner.run([exe, "--config-ignore", "-U"], timeout=UPDATE_TIMEOUT)
            log.info("gallery-dl -U: rc=%s %s", res.returncode, (res.stdout + res.stderr).strip()[-500:])

        self._with_gate(self_update)
        if sha256_file(exe) != expected:
            self._download_verified(GALLERYDL_BASE + "gallery-dl.exe", exe, expected, "gallery-dl")
        self._cleanup_old(exe)
        self._detect_gallerydl()
        self._component("gallery-dl", updated=self._status["components"]["gallery-dl"]["version"] != old)

    # --- ffmpeg --------------------------------------------------------------------------------------
    def _update_ffmpeg(self, force: bool) -> None:
        source = self._status["components"]["ffmpeg"]["source"]
        if source == "system":
            return
        state = self._state.setdefault("ffmpeg", {})
        if source == "bundled":
            age = time.time() - float(state.get("downloaded_at") or 0)
            if age < FFMPEG_REFRESH_S and not force:
                return
        sums = parse_sums(self.net.get_text(FFMPEG_BASE + FFMPEG_SUMS))
        expected = sums.get(FFMPEG_ZIP)
        if not expected:
            raise RuntimeError(f"{FFMPEG_ZIP} is missing from {FFMPEG_SUMS}")
        if source == "bundled" and state.get("zip_sha") == expected:
            state["downloaded_at"] = time.time()  # still the latest build
            return
        self.install_ffmpeg(expected)
        state.update(downloaded_at=time.time(), zip_sha=expected)
        self._detect_ffmpeg()

    def install_ffmpeg(self, expected_sha: str) -> None:
        zip_path = os.path.join(self.paths.cache_dir, FFMPEG_ZIP)
        part = zip_path + ".part"

        def progress(done: int, total: int | None) -> None:
            frac = (done / total) if total else None
            pct = f" {int(frac * 100)}%" if frac is not None else ""
            self._emit(stage="downloading", message=f"Downloading ffmpeg…{pct}", progress=frac)

        try:
            got = self.net.download(FFMPEG_BASE + FFMPEG_ZIP, part, progress)
            if got != expected_sha:
                raise VerifyError(f"ffmpeg zip SHA-256 mismatch ({got} != {expected_sha})")
            os.replace(part, zip_path)
            self._emit(stage="updating", message="Unpacking ffmpeg…", progress=None)
            extracted: dict[str, str] = {}
            with zipfile.ZipFile(zip_path) as zf:
                for member in zf.infolist():
                    base = member.filename.replace("\\", "/").rsplit("/", 1)[-1].lower()
                    if base in ("ffmpeg.exe", "ffprobe.exe") and "/bin/" in "/" + member.filename.replace("\\", "/"):
                        target_part = self.paths.binary(base) + ".part"
                        with zf.open(member) as src, open(target_part, "wb") as dst:
                            shutil.copyfileobj(src, dst, 1 << 20)
                        extracted[base] = target_part
            if set(extracted) != {"ffmpeg.exe", "ffprobe.exe"}:
                raise VerifyError("ffmpeg zip does not contain bin/ffmpeg.exe and bin/ffprobe.exe")

            def swap() -> None:
                for base, p in extracted.items():
                    replace_file(p, self.paths.binary(base))
            self._with_gate(swap)
        finally:
            for p in (part, zip_path, self.paths.binary("ffmpeg.exe") + ".part", self.paths.binary("ffprobe.exe") + ".part"):
                try:
                    os.remove(p)
                except OSError:
                    pass
