"""Stand-in for `app.core.engine.Engine` (docs/ARCHITECTURE.md section 6) used by `--fake` and by the bridge tests.

It has the same public surface and produces the payload shapes of docs/BRIDGE_API.md with simulated jobs and
engine / update / auth events. No network access, no yt-dlp. It is a development tool: it may duplicate tiny link
regexes instead of importing the real link cleaner.

Simulated app update: `MEDIA_SAVER_FAKE_UPDATE` = `uptodate` (the check finds nothing) | `error` (the check fails
with "No internet connection") | `major` (the next major version, a choice: no background download); anything else
(default) offers the next minor version, downloads it for about three
seconds and "applies" it (the bridge then closes the window).
"""

from __future__ import annotations

import base64
import json
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

PLATFORMS = ("instagram", "youtube", "tiktok", "twitter")
FORMATS = ("mp4", "mp3")
THEMES = ("dusk", "nova", "frost", "bloom")
# The quality ladder of the real engine (docs/ENGINE.md section 4.5): short-side cap for mp4 (None = best the
# site has) and the MP3 bitrate in kbit/s; the same four values on every platform.
QUALITIES = ("low", "medium", "high", "max")
QUALITY_CAPS = {"low": 480, "medium": 720, "high": 1080, "max": None}
MP3_BITRATES = {"low": 128, "medium": 192, "high": 256, "max": 320}
QUALITY_LABELS = {"instagram": "Instagram", "youtube": "YouTube", "tiktok": "TikTok", "twitter": "X"}
FAKE_UPDATE_ENV = "MEDIA_SAVER_FAKE_UPDATE"
FAKE_UPDATE_NOTES = (
    "## Added\n- YouTube videos and Shorts\n- MP3 audio downloads\n- Preview of queued links\n\n"
    "## Fixed\n- Progress bar jumped back at the merge step"
)

_IG_RE = re.compile(
    r"https?://(?:www\.)?instagram\.com/(?:[A-Za-z0-9_.]+/)?(reel|reels|p|tv)/([A-Za-z0-9_-]+)", re.IGNORECASE
)
_YT_WATCH_RE = re.compile(r"https?://(?:www\.|m\.|music\.)?youtube\.com/watch\?(?:[^\s]*&)?v=([A-Za-z0-9_-]{11})", re.IGNORECASE)
_YT_SHORTS_RE = re.compile(r"https?://(?:www\.|m\.)?youtube\.com/shorts/([A-Za-z0-9_-]{11})", re.IGNORECASE)
_YT_SHORT_RE = re.compile(r"https?://youtu\.be/([A-Za-z0-9_-]{11})", re.IGNORECASE)
_TT_VIDEO_RE = re.compile(r"https?://(?:www\.|m\.)?tiktok\.com/@([\w.-]*)/video/(\d{15,25})(?!\d)", re.IGNORECASE)
_TT_SHORT_HOST_RE = re.compile(r"https?://(vm|vt)\.tiktok\.com/([A-Za-z0-9]{6,20})(?![A-Za-z0-9])", re.IGNORECASE)
_TT_SHARE_RE = re.compile(r"https?://(?:www\.|m\.)?tiktok\.com/t/([A-Za-z0-9]{6,20})(?![A-Za-z0-9])", re.IGNORECASE)
_X_POST_RE = re.compile(
    r"https?://(?:www\.|m\.|mobile\.)?(?:x|twitter)\.com/(?:i/web|([A-Za-z0-9_]{1,15}))/status(?:es)?/(\d{10,25})(?!\d)",
    re.IGNORECASE,
)
_KNOWN_HOST_RE = re.compile(
    r"^(?:https?://)?(?:[a-z0-9-]+\.)*(?:tiktok\.com|x\.com|twitter\.com|t\.co)(?:[/?#:]|$)", re.IGNORECASE
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


def _match_link(token: str) -> Optional[tuple[str, str, str, str]]:
    """(platform, kind, shortcode, clean url) of a supported link inside `token`, else None."""
    m = _IG_RE.search(token)
    if m:
        kind = "p" if m.group(1).lower() == "p" else "reel"
        return "instagram", kind, m.group(2), f"https://www.instagram.com/{kind}/{m.group(2)}"
    m = _YT_SHORTS_RE.search(token)
    if m:
        return "youtube", "shorts", m.group(1), f"https://www.youtube.com/shorts/{m.group(1)}"
    m = _YT_WATCH_RE.search(token) or _YT_SHORT_RE.search(token)
    if m:
        return "youtube", "video", m.group(1), f"https://www.youtube.com/watch?v={m.group(1)}"
    m = _TT_VIDEO_RE.search(token)
    if m:
        return "tiktok", "video", m.group(2), f"https://www.tiktok.com/@{m.group(1)}/video/{m.group(2)}"
    m = _TT_SHORT_HOST_RE.search(token)
    if m:
        host = m.group(1).lower()
        return "tiktok", "share", m.group(2), f"https://{host}.tiktok.com/{m.group(2)}"
    m = _TT_SHARE_RE.search(token)
    if m:
        return "tiktok", "share", m.group(1), f"https://www.tiktok.com/t/{m.group(1)}"
    m = _X_POST_RE.search(token)
    if m:
        return "twitter", "post", m.group(2), f"https://x.com/{m.group(1) or 'i'}/status/{m.group(2)}"
    return None


_EXTRACTOR_MESSAGES = {
    "youtube": "YouTube changed something and the video could not be read. Try again later.",
    "tiktok": "TikTok changed something and the video could not be read. Try again later.",
    "twitter": "X changed something and the video could not be read. Try again later.",
}


def _demo_author(platform: str, code: str) -> str:
    if platform == "tiktok":
        return "demo_tt_" + code[:4].lower()
    if platform == "twitter":
        return "demo_x_" + code[:4].lower()
    return ("demo_" + code[:4].lower()) if platform == "instagram" else "Demo Channel"


def _demo_title(platform: str, code: str) -> str:
    if platform == "tiktok":
        return f"Demo TikTok {code}"
    if platform == "twitter":
        return f"Demo post {code}"
    return f"Demo {'reel' if platform == 'instagram' else 'video'} {code}"


def _demo_size(platform: str, kind: str, quality: str) -> tuple[int, int]:
    """(width, height) of the simulated mp4: the best the demo site has, capped by the quality ladder."""
    if platform == "youtube" and kind == "video":
        w, h = 3840, 2160          # horizontal, up to 2160p
    elif platform == "youtube":
        w, h = 2160, 3840          # Shorts, vertical, up to 2160p
    else:
        w, h = 1080, 1920          # Instagram, TikTok, X: vertical, up to 1080p
    cap = QUALITY_CAPS.get(quality)
    short = min(w, h)
    if cap is None or short <= cap:
        return w, h
    scale = cap / short
    w2, h2 = round(w * scale), round(h * scale)
    return w2 + (w2 % 2), h2 + (h2 % 2)


def _bump_minor(version: str) -> str:
    parts = [int(p) if p.isdigit() else 0 for p in re.split(r"[.\-+]", version)[:3]]
    parts += [0] * (3 - len(parts))
    return f"{parts[0]}.{parts[1] + 1}.0"


def _bump_major(version: str) -> str:
    head = re.split(r"[.\-+]", version)[0]
    return f"{(int(head) if head.isdigit() else 0) + 1}.0.0"


class FakeEngine:
    def __init__(self, emit: EmitFn, app_version: str, root: Optional[str] = None) -> None:
        self._emit = emit
        self._app_version = app_version
        base = root or os.environ.get("MEDIA_SAVER_HOME") or os.path.join(tempfile.gettempdir(), "MediaSaverFake")
        self.paths = FakePaths(Path(base))
        self._lock = threading.RLock()
        self._jobs: dict[str, dict] = {}
        self._order: list[str] = []      # oldest first; the list shows it newest first (top = last here)
        self._last_created = 0.0         # created_at of the newest job, kept strictly increasing
        self._cancel: set[str] = set()
        self._stop = threading.Event()
        self._started = False
        self._rnd = random.Random(7)
        out_dir = str(self.paths.root / "Downloads")
        self._settings: dict[str, Any] = {
            "output_dir": out_dir,
            "filename_template": "{author}_{shortcode}",
            "mode": "instagram",
            "format": "mp4",
            "quality_instagram": "max",
            "quality_youtube": "max",
            "quality_tiktok": "max",
            "quality_twitter": "max",
            "h264_compat": True,
            "skip_existing": True,
            "concurrency": 2,
            "update_channel": "nightly",
            "auto_update": True,
            "auto_download_updates": True,
            "skipped_major": None,
            "auth_mode": "auto",
            "browser_cookies": "",
            "clipboard_watch": True,
            "open_folder_when_done": False,
            "sound_on_done": True,
            "theme": "dusk",
            "ui_scale": 100,
        }
        # The theme is the one setting the fake keeps between launches (the UI checks that a chosen
        # theme survives a restart); everything else starts from the defaults above.
        self._settings_file = self.paths.root / "settings.json"
        self._load_theme()
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
        self._update: dict[str, Any] = {
            "stage": "idle", "current": app_version, "latest": None, "notes": None, "size": None,
            "published_at": None, "progress": None, "message": "Updates are not checked yet", "checked_at": None,
            "can_apply": False, "major": False,
        }
        self._update_busy = False
        self._auto_download_for: Optional[str] = None   # `latest` the background download was started for

    # -- lifecycle
    def start(self) -> None:
        if self._started:
            return
        self._started = True
        threading.Thread(target=self._boot, name="fake-engine-boot", daemon=True).start()
        if True:  # the update check always runs on launch
            self.check_updates()

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
                "update": dict(self._update),
                "auth": self.auth_status(),
                "settings": dict(self._settings),
                "jobs": [_deep(self._jobs[i]) for i in reversed(self._order) if i in self._jobs],
            }

    # -- links
    def clean_links(self, text: str, platform: Optional[str] = None) -> dict:
        _check_platform(platform)
        items = []
        seen: set[str] = set()
        clean_list: list[str] = []
        duplicates = 0
        for token in re.split(r"\s+", text or ""):
            if not token:
                continue
            found = _match_link(token)
            if found is None:
                lowered = token.lower()
                known_host = ("instagram.com" in lowered or "youtube.com" in lowered or "youtu.be" in lowered
                              or bool(_KNOWN_HOST_RE.match(lowered)))
                is_url = lowered.startswith(("http://", "https://")) or known_host
                if not is_url:
                    continue
                items.append(
                    {
                        "original": token, "clean": None, "shortcode": None, "platform": None, "kind": None,
                        "error": "unsupported_path" if known_host else "not_supported",
                    }
                )
                continue
            item_platform, kind, code, clean = found
            if platform is not None and item_platform != platform:
                items.append({"original": token, "clean": clean, "shortcode": code, "platform": item_platform,
                              "kind": kind, "error": "other_platform"})
                continue
            items.append({"original": token, "clean": clean, "shortcode": code, "platform": item_platform,
                          "kind": kind, "error": None})
            key = f"{item_platform}:{code}"
            if key in seen:
                duplicates += 1
            else:
                seen.add(key)
                clean_list.append(clean)
        return {
            "items": items,
            "clean_text": "\n".join(clean_list),
            "valid": len(clean_list),
            "invalid": sum(1 for i in items if i["error"]),
            "duplicates": duplicates,
        }

    def enqueue(self, text: str, platform: Optional[str] = None, fmt: Optional[str] = None) -> dict:
        _check_format(fmt)
        cleaned = self.clean_links(text, platform)
        job_format = fmt or self._settings["format"]
        added, skipped = [], []
        with self._lock:
            active = {
                (j["platform"], j["shortcode"]) for j in self._jobs.values() if j["status"] not in ("done", "error", "canceled")
            }
            queued_now: set[tuple[str, str]] = set()
            accepted = []
            for item in cleaned["items"]:
                if item["error"] or not item["clean"]:
                    skipped.append({"input": item["original"], "reason": "invalid"})
                    continue
                key = (item["platform"], item["shortcode"])
                if key in active or key in queued_now:
                    skipped.append({"input": item["original"], "reason": "duplicate"})
                    continue
                queued_now.add(key)
                accepted.append(item)
            # As the real engine: the batch is added in reverse, so the first pasted link is on top of the list
            # (newest created_at) and starts first; `added` comes back in paste order.
            for item in reversed(accepted):
                job = self._new_job(item["clean"], item["shortcode"], item["platform"], item["kind"], job_format)
                added.insert(0, _deep(job))
        for job in reversed(added):  # oldest first, like the real engine
            self._emit("job", job)
        for job in added:
            # previews of the whole batch run at once, in parallel with the queue (the real engine's pool)
            threading.Thread(target=self._preview, args=(job["id"], job["shortcode"], job["platform"]),
                             name="fake-preview", daemon=True).start()
            threading.Thread(target=self._run_job, args=(job["id"],), name="fake-job", daemon=True).start()
        return {"added": added, "skipped": skipped}

    def set_job_format(self, job_id: str, fmt: str) -> Optional[dict]:
        _check_format(fmt)
        if fmt is None:
            raise ValueError("Format must be mp4 or mp3.")
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job["status"] != "queued":
                return None
            job["format"] = fmt
            snapshot = _deep(job)
        self._emit("job", snapshot)
        return snapshot

    def _new_job(self, url: str, code: str, platform: str, kind: str, fmt: str) -> dict:
        job_id = uuid.uuid4().hex[:8]
        job = {
            "id": job_id, "url": url, "shortcode": code, "platform": platform, "kind": kind, "format": fmt,
            "status": "queued", "stage_text": "Queued", "progress": 0.0, "speed_bps": None, "eta_s": None,
            "previewed": False, "title": None, "author": None, "duration_s": None, "thumbnail": None,
            "width": None, "height": None, "fps": None, "vcodec": None, "acodec": None,
            "filesize": None, "filepath": None, "already_existed": False, "transcoded": False,
            "attempt": {"engine": "yt-dlp", "auth": "anonymous"}, "error": None,
            "created_at": self._next_created_at(), "finished_at": None,
        }
        self._jobs[job_id] = job
        self._order.append(job_id)
        return job

    def _next_created_at(self) -> float:
        """Caller holds the lock. time.time(), but strictly greater than the created_at of every job so far."""
        value = max(time.time(), self._last_created + 0.001)
        self._last_created = value
        return value

    def _top_queued_id(self) -> Optional[str]:
        """Caller holds the lock. The queued job shown topmost (the last queued one in `_order`)."""
        for jid in reversed(self._order):
            job = self._jobs.get(jid)
            if job is not None and job["status"] == "queued":
                return jid
        return None

    def _update_job(self, job_id: str, **fields: Any) -> Optional[dict]:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            job.update(fields)
            snapshot = _deep(job)
        self._emit("job", snapshot)
        return snapshot

    def _running_count(self) -> int:
        return sum(1 for j in self._jobs.values() if j["status"] in ("probing", "downloading", "processing"))

    def _preview(self, job_id: str, code: str, platform: str) -> None:
        """Simulated preview fetch, one thread per job so a pasted batch gets its previews within about a second.
        Fills only empty fields (a job that started meanwhile keeps what its probe set), then `previewed`."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job["previewed"]:
                return
            delay = self._rnd.uniform(0.3, 0.9)
        time.sleep(delay)
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job_id in self._cancel or job["status"] == "canceled":
                return
            found = {"title": _demo_title(platform, code), "author": _demo_author(platform, code),
                     "duration_s": round(self._rnd.uniform(12, 95), 1),
                     "thumbnail": _thumbnail_data_uri(sum(map(ord, code)))}
            fields = {k: v for k, v in found.items() if not job.get(k)}
        self._update_job(job_id, previewed=True, **fields)

    def _run_job(self, job_id: str) -> None:
        def canceled() -> bool:
            return job_id in self._cancel or self._stop.is_set()

        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            code, platform = job["shortcode"], job["platform"]
        self._cancel.discard(job_id)
        # Wait for a free slot and for the turn: the queued job shown topmost starts first (as the real engine);
        # the job stays "queued" (format can still be changed) while others run.
        while True:
            if canceled():
                return self._finish_canceled(job_id)
            with self._lock:
                job = self._jobs.get(job_id)
                if job is None:
                    return
                if (self._running_count() < int(self._settings["concurrency"])
                        and self._top_queued_id() == job_id):
                    job["status"] = "probing"
                    fmt = job["format"]
                    kind = job["kind"]
                    quality = self._settings.get(f"quality_{platform}", "max")
                    break
            time.sleep(0.05)
        self._update_job(job_id, status="probing", stage_text="Fetching info", progress=0.04)
        time.sleep(0.8)
        if canceled():
            return self._finish_canceled(job_id)
        author = self._jobs[job_id]["author"] if job_id in self._jobs and self._jobs[job_id]["author"] else None
        if not author:
            author = _demo_author(platform, code) if platform in ("tiktok", "twitter") else "demo_" + code[:4].lower()
        if "fail" in code.lower():
            instagram = platform == "instagram"
            self._update_job(
                job_id,
                status="error",
                stage_text="Error",
                finished_at=time.time(),
                error={
                    "code": "login_required" if instagram else "extractor",
                    "message": ("Instagram won't show this video without signing in. Sign in to Instagram in the app."
                                if instagram else _EXTRACTOR_MESSAGES.get(platform, _EXTRACTOR_MESSAGES["youtube"])),
                    "can_login": instagram and not self.auth_status()["logged_in"],
                    "log_path": str(self.paths.logs_dir / f"{job_id}.log"),
                },
            )
            return
        mp3 = fmt == "mp3"
        meta: dict[str, Any] = {"acodec": "mp3" if mp3 else "aac"}
        if not mp3:
            width, height = _demo_size(platform, kind, quality)
            meta.update(width=width, height=height, fps=30)
        with self._lock:
            current = self._jobs.get(job_id) or {}
            duration = current.get("duration_s") or 60.0
        meta["title"] = _demo_title(platform, code)
        meta["author"] = author
        if not current.get("duration_s"):
            meta["duration_s"] = duration
        self._update_job(job_id, **meta)
        if mp3:   # the file size follows the ladder's MP3 bitrate (kbit/s -> bytes)
            total = int(duration * MP3_BITRATES.get(quality, 320) * 125)
        else:     # the mp4 size scales a 1080p base by picture area
            total = int(12_345_678 * (width * height) / (1080 * 1920))
        steps = 24
        for i in range(steps):
            if canceled():
                return self._finish_canceled(job_id)
            frac = (i + 1) / steps
            self._update_job(
                job_id, status="downloading", stage_text="Downloading audio" if mp3 else "Downloading video",
                progress=0.05 + 0.8 * frac,
                speed_bps=3_145_728 + self._rnd.randint(-400_000, 400_000), eta_s=max(0, int((steps - i - 1) * 0.15)),
            )
            time.sleep(0.15)
        self._update_job(job_id, status="processing", stage_text="Converting to MP3" if mp3 else "Merging tracks",
                         progress=0.92, speed_bps=None, eta_s=None)
        time.sleep(0.6)
        if canceled():
            return self._finish_canceled(job_id)
        out_dir = Path(self._settings["output_dir"])
        out_dir.mkdir(parents=True, exist_ok=True)
        target = out_dir / f"{author}_{code}.{fmt}".replace(" ", "_")
        target.write_bytes(b"fake media placeholder\n")
        self._update_job(
            job_id, status="done", stage_text="Done", progress=1.0, filesize=total, filepath=str(target),
            vcodec=None if mp3 else "h264", acodec="mp3" if mp3 else "aac", finished_at=time.time(),
            speed_bps=None, eta_s=None,
        )

    def _finish_canceled(self, job_id: str) -> None:
        self._cancel.discard(job_id)
        self._update_job(
            job_id, status="canceled", stage_text="Canceled", speed_bps=None, eta_s=None, finished_at=time.time(),
            error={"code": "canceled", "message": "Canceled", "can_login": False, "log_path": None},
        )

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
            # as the real engine: the retried job moves to the top of the order and starts next; created_at stays
            self._order.remove(job_id)
            self._order.append(job_id)
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

    def clear_finished(self, platform: Optional[str] = None) -> int:
        _check_platform(platform)
        with self._lock:
            finished = [
                i for i, j in self._jobs.items()
                if j["status"] in ("done", "error", "canceled") and (platform is None or j["platform"] == platform)
            ]
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

    def _load_theme(self) -> None:
        try:
            with open(self._settings_file, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            return
        if isinstance(data, dict) and data.get("theme") in THEMES:
            self._settings["theme"] = data["theme"]

    def _save_theme(self) -> None:
        try:
            with open(self._settings_file, "w", encoding="utf-8") as fh:
                json.dump({"theme": self._settings["theme"]}, fh)
        except OSError:
            pass

    def set_settings(self, patch: dict) -> dict:
        with self._lock:
            unknown = [k for k in patch if k not in self._settings]
            if unknown:
                raise ValueError("Unknown setting: " + ", ".join(unknown))
            if "concurrency" in patch and not (isinstance(patch["concurrency"], int) and 1 <= patch["concurrency"] <= 4):
                raise ValueError("Concurrent downloads must be between 1 and 4.")
            for platform, label in QUALITY_LABELS.items():
                key = f"quality_{platform}"
                if key in patch and patch[key] not in QUALITIES:
                    raise ValueError(f"Invalid {label} quality.")
            if "mode" in patch and patch["mode"] not in PLATFORMS:
                raise ValueError("Invalid mode.")
            if "format" in patch and patch["format"] not in FORMATS:
                raise ValueError("Format must be mp4 or mp3.")
            if "skipped_major" in patch:
                value = patch["skipped_major"]
                ok = isinstance(value, int) and not isinstance(value, bool) and value >= 1
                patch = dict(patch, skipped_major=value if ok else None)  # as the core: never refused
            if "theme" in patch and patch["theme"] not in THEMES:
                patch = dict(patch, theme="dusk")  # as the core: an unknown name becomes the default
            turned_on = patch.get("auto_download_updates") is True and not self._settings["auto_download_updates"]
            self._settings.update(patch)
            snapshot = dict(self._settings)
            if turned_on:
                self._auto_download_for = None
            if "theme" in patch:
                self._save_theme()
        self._emit("settings", snapshot)
        if turned_on:
            self._maybe_auto_download()
        return snapshot

    def update_engine(self) -> None:
        threading.Thread(target=self._update_sequence, args=(False,), name="fake-update", daemon=True).start()

    # -- app self-update (simulated)
    def _set_update(self, **fields: Any) -> dict:
        with self._lock:
            self._update.update(fields)
            snapshot = dict(self._update)
        self._emit("update", snapshot)
        return snapshot

    def update_status(self) -> dict:
        with self._lock:
            return dict(self._update)

    def check_updates(self) -> dict:
        with self._lock:
            if self._update_busy or self._update["stage"] == "downloading":
                return dict(self._update)
            self._update_busy = True
        status = self._set_update(stage="checking", message="Checking for updates…", progress=None)
        threading.Thread(target=self._check_sequence, name="fake-app-check", daemon=True).start()
        return status

    def _check_sequence(self) -> None:
        try:
            time.sleep(1.0)
            if self._stop.is_set():
                return
            mode = os.environ.get(FAKE_UPDATE_ENV, "").strip().lower()
            now = time.time()
            if mode == "error":
                self._set_update(stage="error", latest=None, notes=None, size=None, published_at=None, progress=None,
                                 message="No internet connection", can_apply=False, major=False)
            elif mode == "uptodate":
                self._set_update(stage="up_to_date", latest=self._app_version, notes=None, size=None, published_at=None,
                                 progress=None, message="Media Saver is up to date", checked_at=now, can_apply=False,
                                 major=False)
            elif mode == "major":
                latest = _bump_major(self._app_version)
                self._set_update(stage="available", latest=latest, notes=FAKE_UPDATE_NOTES, size=13_100_000,
                                 published_at=now - 86_400, progress=None, checked_at=now, can_apply=False,
                                 message=f"Version {latest} is available as a new major version", major=True)
            else:
                latest = _bump_minor(self._app_version)
                self._set_update(stage="available", latest=latest, notes=FAKE_UPDATE_NOTES, size=13_100_000,
                                 published_at=now - 86_400, progress=None, checked_at=now, can_apply=False,
                                 message=f"Version {latest} is available", major=False)
        finally:
            with self._lock:
                self._update_busy = False
        self._maybe_auto_download()

    def _maybe_auto_download(self) -> None:
        """Background download while auto_download_updates is on: once per `latest` (as app/core/engine.py)."""
        with self._lock:
            if not self._settings.get("auto_download_updates") or self._update["stage"] != "available":
                return
            if self._update.get("major"):
                return  # as the core: a new major version waits for the user's "Switch"
            latest = self._update["latest"]
            if not latest or self._auto_download_for == latest:
                return
            self._auto_download_for = latest
        self.download_update()

    def download_update(self) -> None:
        with self._lock:
            if self._update_busy or self._update["stage"] not in ("available", "error") or not self._update["latest"]:
                return
            self._update_busy = True
            latest = self._update["latest"]
        self._set_update(stage="downloading", progress=0.0, can_apply=False,
                         message=f"Downloading Media Saver {latest}… 0%")
        threading.Thread(target=self._download_sequence, args=(latest,), name="fake-app-download", daemon=True).start()

    def _download_sequence(self, latest: str) -> None:
        try:
            steps = 30
            for i in range(steps):
                if self._stop.is_set():
                    return
                time.sleep(0.1)
                frac = (i + 1) / steps
                self._set_update(progress=frac, message=f"Downloading Media Saver {latest}… {int(frac * 100)}%")
            self._set_update(stage="ready", progress=None, can_apply=True, message=f"Version {latest} is ready to install")
        finally:
            with self._lock:
                self._update_busy = False

    def apply_update(self) -> bool:
        with self._lock:
            return self._update["stage"] == "ready" and bool(self._update["can_apply"])

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


def _check_platform(value: Optional[str]) -> None:
    if value is not None and value not in PLATFORMS:
        raise ValueError(f"Unknown platform: {value!r}")


def _check_format(value: Optional[str]) -> None:
    if value is not None and value not in FORMATS:
        raise ValueError("Format must be mp4 or mp3.")


def _deep(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _deep(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_deep(v) for v in value]
    return value
