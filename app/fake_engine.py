"""Stand-in for `app.core.engine.Engine` (docs/ARCHITECTURE.md section 6) used by `--fake` and by the bridge tests.

It has the same public surface and produces the payload shapes of docs/BRIDGE_API.md with simulated jobs and
engine / auth events. No network access, no yt-dlp. It is a development tool: it may duplicate a tiny link regex
instead of importing the real link cleaner.
"""

from __future__ import annotations

import base64
import os
import random
import re
import shutil
import struct
import tempfile
import threading
import time
import uuid
import zlib
from pathlib import Path
from typing import Any, Callable, Optional

EmitFn = Callable[[str, dict], None]

_LINK_RE = re.compile(
    r"https?://(?:www\.)?instagram\.com/(?:[A-Za-z0-9_.]+/)?(reel|reels|p|tv)/([A-Za-z0-9_-]+)", re.IGNORECASE
)


class FakePaths:
    """Minimal subset of AppPaths that the shell touches."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.bin_dir = root / "bin"
        self.auth_dir = root / "auth"
        self.cookies_file = self.auth_dir / "cookies.txt"
        self.logs_dir = root / "logs"
        self.webview_dir = root / "webview"
        for d in (self.bin_dir, self.auth_dir, self.logs_dir, self.webview_dir):
            d.mkdir(parents=True, exist_ok=True)


def _thumbnail_data_uri(seed: int) -> str:
    """Tiny gradient PNG (stdlib only), 90x160, as a data URI."""
    w, h = 90, 160
    rnd = random.Random(seed)
    r0, g0, b0 = rnd.randint(40, 200), rnd.randint(40, 200), rnd.randint(40, 200)
    rows = bytearray()
    for y in range(h):
        rows.append(0)
        t = y / (h - 1)
        for x in range(w):
            u = x / (w - 1)
            rows += bytes((int(r0 * (1 - t) + 255 * t * u) & 255, int(g0 * (1 - u) + 90 * t) & 255, int(b0 * (0.4 + 0.6 * u)) & 255))

    def chunk(tag: bytes, data: bytes) -> bytes:
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(bytes(rows), 6))
        + chunk(b"IEND", b"")
    )
    return "data:image/png;base64," + base64.b64encode(png).decode("ascii")


class FakeEngine:
    def __init__(self, emit: EmitFn, app_version: str, root: Optional[str] = None) -> None:
        self._emit = emit
        self._app_version = app_version
        base = root or os.environ.get("REELS_SAVER_HOME") or os.path.join(tempfile.gettempdir(), "ReelsSaverFake")
        self.paths = FakePaths(Path(base))
        self._lock = threading.RLock()
        self._jobs: dict[str, dict] = {}
        self._order: list[str] = []
        self._cancel: set[str] = set()
        self._stop = threading.Event()
        self._started = False
        self._rnd = random.Random(7)
        out_dir = str(self.paths.root / "Downloads")
        self._settings: dict[str, Any] = {
            "output_dir": out_dir,
            "filename_template": "{author}_{shortcode}",
            "quality": "max",
            "h264_compat": True,
            "skip_existing": True,
            "concurrency": 2,
            "update_channel": "nightly",
            "auto_update": True,
            "auth_mode": "auto",
            "browser_cookies": "",
            "clipboard_watch": True,
            "open_folder_when_done": False,
        }
        self._engine_status: dict[str, Any] = {
            "stage": "starting",
            "message": "Starting…",
            "progress": None,
            "components": {
                "yt-dlp": {"version": None, "channel": "nightly", "updated": False, "ok": False},
                "gallery-dl": {"version": None, "updated": False, "ok": False},
                "ffmpeg": {"version": None, "source": "system", "ok": False},
            },
            "last_check": None,
        }

    # -- lifecycle
    def start(self) -> None:
        if self._started:
            return
        self._started = True
        threading.Thread(target=self._boot, name="fake-engine-boot", daemon=True).start()

    def shutdown(self) -> None:
        self._stop.set()

    def _boot(self) -> None:
        self._update_sequence(first=True)

    def _set_engine(self, **fields: Any) -> None:
        with self._lock:
            self._engine_status.update(fields)
            snapshot = _deep(self._engine_status)
        self._emit("engine", snapshot)

    def _update_sequence(self, first: bool) -> None:
        steps = [
            ("checking", "Checking for updates…", None),
            ("downloading" if first else "updating", "Updating yt-dlp…", 0.35),
            ("updating", "Updating gallery-dl…", 0.7),
            ("updating", "Checking ffmpeg…", 0.9),
        ]
        for stage, message, progress in steps:
            if self._stop.is_set():
                return
            self._set_engine(stage=stage, message=message, progress=progress)
            time.sleep(0.7)
        comps = {
            "yt-dlp": {"version": "2026.10.18.234512", "channel": "nightly", "updated": True, "ok": True},
            "gallery-dl": {"version": "1.30.10", "updated": False, "ok": True},
            "ffmpeg": {"version": "9.0.2", "source": "system", "ok": True},
        }
        self._set_engine(stage="ready", message="Engine ready", progress=None, components=comps, last_check=time.time())

    # -- state
    def get_state(self) -> dict:
        with self._lock:
            return {
                "app_version": self._app_version,
                "engine": _deep(self._engine_status),
                "auth": self.auth_status(),
                "settings": dict(self._settings),
                "jobs": [_deep(self._jobs[i]) for i in reversed(self._order) if i in self._jobs],
            }

    # -- links
    def clean_links(self, text: str) -> dict:
        items = []
        seen: set[str] = set()
        clean_list: list[str] = []
        duplicates = 0
        for token in re.split(r"\s+", text or ""):
            if not token:
                continue
            m = _LINK_RE.search(token)
            if not m:
                host_ok = "instagram.com" in token.lower()
                items.append(
                    {
                        "original": token,
                        "clean": None,
                        "shortcode": None,
                        "kind": None,
                        "error": "unsupported_path" if host_ok else "not_instagram",
                    }
                )
                continue
            kind = "p" if m.group(1).lower() == "p" else "reel"
            code = m.group(2)
            clean = f"https://www.instagram.com/{kind}/{code}"
            items.append({"original": token, "clean": clean, "shortcode": code, "kind": kind, "error": None})
            if code in seen:
                duplicates += 1
            else:
                seen.add(code)
                clean_list.append(clean)
        return {
            "items": items,
            "clean_text": "\n".join(clean_list),
            "valid": len(clean_list),
            "invalid": sum(1 for i in items if i["error"]),
            "duplicates": duplicates,
        }

    def enqueue(self, text: str) -> dict:
        cleaned = self.clean_links(text)
        added, skipped = [], []
        with self._lock:
            active = {j["shortcode"] for j in self._jobs.values() if j["status"] not in ("done", "error", "canceled")}
            queued_now: set[str] = set()
            for item in cleaned["items"]:
                if not item["clean"]:
                    skipped.append({"input": item["original"], "reason": "invalid"})
                    continue
                code = item["shortcode"]
                if code in active or code in queued_now:
                    skipped.append({"input": item["original"], "reason": "duplicate"})
                    continue
                queued_now.add(code)
                job = self._new_job(item["clean"], code)
                added.append(_deep(job))
        for job in added:
            self._emit("job", job)
            threading.Thread(target=self._run_job, args=(job["id"],), name="fake-job", daemon=True).start()
        return {"added": added, "skipped": skipped}

    def _new_job(self, url: str, code: str) -> dict:
        job_id = uuid.uuid4().hex[:8]
        job = {
            "id": job_id, "url": url, "shortcode": code, "status": "queued", "stage_text": "Queued",
            "progress": 0.0, "speed_bps": None, "eta_s": None, "title": None, "author": None, "duration_s": None,
            "thumbnail": None, "width": None, "height": None, "fps": None, "vcodec": None, "acodec": None,
            "filesize": None, "filepath": None, "already_existed": False, "transcoded": False,
            "attempt": {"engine": "yt-dlp", "auth": "anonymous"}, "error": None,
            "created_at": time.time(), "finished_at": None,
        }
        self._jobs[job_id] = job
        self._order.append(job_id)
        return job

    def _update_job(self, job_id: str, **fields: Any) -> Optional[dict]:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            job.update(fields)
            snapshot = _deep(job)
        self._emit("job", snapshot)
        return snapshot

    def _run_job(self, job_id: str) -> None:
        def canceled() -> bool:
            return job_id in self._cancel or self._stop.is_set()

        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            code = job["shortcode"]
        self._cancel.discard(job_id)
        time.sleep(0.4)
        if canceled():
            return self._finish_canceled(job_id)
        self._update_job(job_id, status="probing", stage_text="Fetching info", progress=0.04)
        time.sleep(0.8)
        if canceled():
            return self._finish_canceled(job_id)
        author = "demo_" + code[:4].lower()
        if "fail" in code.lower():
            self._update_job(
                job_id,
                status="error",
                stage_text="Error",
                finished_at=time.time(),
                error={
                    "code": "login_required",
                    "message": "Instagram won't show this video without signing in. Sign in to Instagram in the app.",
                    "can_login": not self.auth_status()["logged_in"],
                    "log_path": str(self.paths.logs_dir / f"{job_id}.log"),
                },
            )
            return
        self._update_job(
            job_id, title=f"Demo reel {code}", author=author, duration_s=23.4, width=1080, height=1920, fps=30,
            thumbnail=_thumbnail_data_uri(sum(map(ord, code))),
        )
        total = 12_345_678
        steps = 24
        for i in range(steps):
            if canceled():
                return self._finish_canceled(job_id)
            frac = (i + 1) / steps
            self._update_job(
                job_id, status="downloading", stage_text="Downloading video", progress=0.05 + 0.8 * frac,
                speed_bps=3_145_728 + self._rnd.randint(-400_000, 400_000), eta_s=max(0, int((steps - i - 1) * 0.15)),
            )
            time.sleep(0.15)
        self._update_job(job_id, status="processing", stage_text="Merging tracks", progress=0.92, speed_bps=None, eta_s=None)
        time.sleep(0.6)
        if canceled():
            return self._finish_canceled(job_id)
        out_dir = Path(self._settings["output_dir"])
        out_dir.mkdir(parents=True, exist_ok=True)
        target = out_dir / f"{author}_{code}.mp4"
        target.write_bytes(b"fake mp4 placeholder\n")
        self._update_job(
            job_id, status="done", stage_text="Done", progress=1.0, filesize=total, filepath=str(target),
            vcodec="h264", acodec="aac", finished_at=time.time(), speed_bps=None, eta_s=None,
        )

    def _finish_canceled(self, job_id: str) -> None:
        self._cancel.discard(job_id)
        self._update_job(job_id, status="canceled", stage_text="Canceled", speed_bps=None, eta_s=None, finished_at=time.time())

    def cancel(self, job_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job["status"] in ("done", "error", "canceled"):
                return False
            self._cancel.add(job_id)
            return True

    def retry(self, job_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job["status"] not in ("error", "canceled"):
                return False
            job.update(status="queued", stage_text="Queued", progress=0.0, error=None, finished_at=None)
            snapshot = _deep(job)
        self._emit("job", snapshot)
        threading.Thread(target=self._run_job, args=(job_id,), name="fake-job", daemon=True).start()
        return True

    def remove(self, job_id: str) -> bool:
        with self._lock:
            if job_id not in self._jobs:
                return False
            self._cancel.add(job_id)
            del self._jobs[job_id]
            self._order.remove(job_id)
        self._emit("job_removed", {"id": job_id})
        return True

    def clear_finished(self) -> int:
        with self._lock:
            finished = [i for i, j in self._jobs.items() if j["status"] in ("done", "error", "canceled")]
            for i in finished:
                del self._jobs[i]
                self._order.remove(i)
        for i in finished:
            self._emit("job_removed", {"id": i})
        return len(finished)

    def job_file(self, job_id: str) -> Optional[str]:
        with self._lock:
            job = self._jobs.get(job_id)
            path = job.get("filepath") if job else None
        return path if path and os.path.exists(path) else None

    # -- settings
    def get_settings(self) -> dict:
        with self._lock:
            return dict(self._settings)

    def set_settings(self, patch: dict) -> dict:
        with self._lock:
            unknown = [k for k in patch if k not in self._settings]
            if unknown:
                raise ValueError("Unknown setting: " + ", ".join(unknown))
            if "concurrency" in patch and not (isinstance(patch["concurrency"], int) and 1 <= patch["concurrency"] <= 4):
                raise ValueError("Concurrent downloads must be between 1 and 4.")
            if "quality" in patch and patch["quality"] not in ("max", "1080", "720"):
                raise ValueError("Invalid quality.")
            self._settings.update(patch)
            snapshot = dict(self._settings)
        self._emit("settings", snapshot)
        return snapshot

    def update_engine(self) -> None:
        threading.Thread(target=self._update_sequence, args=(False,), name="fake-update", daemon=True).start()

    # -- auth
    def _read_cookie_names(self) -> dict[str, str]:
        out: dict[str, str] = {}
        try:
            for line in self.paths.cookies_file.read_text(encoding="utf-8", errors="replace").splitlines():
                line = line.replace("#HttpOnly_", "")
                parts = line.split("\t")
                if len(parts) >= 7 and not line.startswith("#"):
                    out[parts[5]] = parts[6]
        except OSError:
            pass
        return out

    def auth_status(self) -> dict:
        cookies = self._read_cookie_names()
        if "sessionid" not in cookies:
            return {"logged_in": False, "user_id": None, "source": None, "saved_at": None}
        try:
            saved_at = self.paths.cookies_file.stat().st_mtime
        except OSError:
            saved_at = None
        return {"logged_in": True, "user_id": cookies.get("ds_user_id"), "source": "session", "saved_at": saved_at}

    def reload_auth(self) -> dict:
        status = self.auth_status()
        self._emit("auth", status)
        return status

    def logout(self) -> dict:
        try:
            self.paths.cookies_file.unlink()
        except OSError:
            pass
        return self.reload_auth()

    def import_cookies_file(self, path: str) -> dict:
        src = Path(path)
        text = src.read_text(encoding="utf-8", errors="replace")
        if "sessionid" not in text:
            raise ValueError("The file has no Instagram sessionid cookie.")
        shutil.copyfile(src, self.paths.cookies_file)
        status = self.auth_status()
        status["source"] = "import"
        self._emit("auth", status)
        return status


def _deep(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _deep(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_deep(v) for v in value]
    return value
