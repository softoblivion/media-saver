"""Job queue, subprocess control, progress parsing, error classification and the auth ladder.

Pipeline per job (ARCHITECTURE.md 5.2): probe with `yt-dlp -J` -> info json in cache/jobs/<id>/ -> thumbnail ->
download with `--load-info-json` -> merge to mp4 -> optional H.264 pass -> ffprobe the final file. The auth
ladder walks anonymous -> session cookies -> browser cookies -> gallery-dl with the session.

Everything that touches the outside world goes through `ProcessRunner` (subprocesses) and `fetch_url`
(thumbnails), so tests replace them with fakes.
"""
from __future__ import annotations

import base64
import ctypes
import json
import logging
import os
import re
import shutil
import subprocess
import threading
import time
import urllib.request
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from . import auth as authmod
from .paths import AppPaths, atomic_write_json

log = logging.getLogger("reels.core.downloader")

EmitFn = Callable[[str, dict], None]

CREATE_NO_WINDOW = 0x08000000
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/150.0.0.0 Safari/537.36")

# =====================================================================================================
# Subprocess control
# =====================================================================================================


def subprocess_env() -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env.pop("PYTHONHOME", None)  # a frozen parent must not leak its Python into the standalone engines
    env.pop("PYTHONPATH", None)
    return env


class _WinJobObject:
    """Win32 Job Object with KILL_ON_JOB_CLOSE: terminating it kills the whole process tree."""

    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
    JobObjectExtendedLimitInformation = 9

    class _IoCounters(ctypes.Structure):
        _fields_ = [(n, ctypes.c_ulonglong) for n in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

    class _BasicLimit(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", ctypes.c_uint32),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", ctypes.c_uint32),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", ctypes.c_uint32),
            ("SchedulingClass", ctypes.c_uint32),
        ]

    class _ExtLimit(ctypes.Structure):
        pass

    _ExtLimit._fields_ = [
        ("BasicLimitInformation", _BasicLimit),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]

    def __init__(self) -> None:
        self.handle = None
        if os.name != "nt":
            return
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateJobObjectW.restype = ctypes.c_void_p
        k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
        k32.SetInformationJobObject.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
        k32.AssignProcessToJobObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        k32.TerminateJobObject.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        k32.CloseHandle.argtypes = [ctypes.c_void_p]
        self._k32 = k32
        handle = k32.CreateJobObjectW(None, None)
        if not handle:
            return
        info = self._ExtLimit()
        info.BasicLimitInformation.LimitFlags = self.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not k32.SetInformationJobObject(handle, self.JobObjectExtendedLimitInformation,
                                           ctypes.byref(info), ctypes.sizeof(info)):
            k32.CloseHandle(handle)
            return
        self.handle = handle

    def assign(self, proc: subprocess.Popen) -> bool:
        if not self.handle:
            return False
        try:
            return bool(self._k32.AssignProcessToJobObject(self.handle, int(proc._handle)))  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            return False

    def terminate(self) -> None:
        if self.handle:
            self._k32.TerminateJobObject(self.handle, 1)

    def close(self) -> None:
        if self.handle:
            self._k32.CloseHandle(self.handle)
            self.handle = None


def kill_tree(proc: subprocess.Popen, job: _WinJobObject | None = None) -> None:
    """Kill a process and all its children (yt-dlp spawns ffmpeg)."""
    if proc.poll() is not None:
        return
    if job is not None and job.handle:
        job.terminate()
    elif os.name == "nt":
        try:
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], stdin=subprocess.DEVNULL,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           creationflags=CREATE_NO_WINDOW, timeout=10)
        except Exception:  # noqa: BLE001
            pass
    try:
        proc.kill()
    except OSError:
        pass


@dataclass
class ProcessResult:
    returncode: int | None
    stdout: str
    stderr: str
    canceled: bool = False
    timed_out: bool = False


class ProcessRunner:
    """Runs a console tool hidden, streams decoded lines, supports cancel and timeout."""

    MAX_CAPTURE = 8 * 1024 * 1024

    def run(self, argv: list[str], *, cwd: str | None = None,
            on_stdout: Callable[[str], None] | None = None,
            on_stderr: Callable[[str], None] | None = None,
            capture_stdout: bool = True,
            cancel: threading.Event | None = None,
            timeout: float | None = None) -> ProcessResult:
        try:
            proc = subprocess.Popen(argv, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, env=subprocess_env(),
                                    creationflags=CREATE_NO_WINDOW if os.name == "nt" else 0)
        except FileNotFoundError:
            return ProcessResult(None, "", f"ERROR: executable not found: {argv[0]}")
        except OSError as exc:
            return ProcessResult(None, "", f"ERROR: cannot start {argv[0]}: {exc}")
        winjob = _WinJobObject()
        if not winjob.assign(proc):
            winjob.close()
            winjob = None  # type: ignore[assignment]
        out_chunks: list[str] = []
        err_chunks: list[str] = []

        def pump(stream, sink: list[str], cb, keep: bool) -> None:
            size = 0
            for raw in iter(stream.readline, b""):
                line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                if keep and size < self.MAX_CAPTURE:
                    sink.append(line)
                    size += len(line) + 1
                if cb is not None:
                    try:
                        cb(line)
                    except Exception:  # noqa: BLE001  a parser bug must not kill the pump
                        log.exception("line callback failed")
            stream.close()

        t_out = threading.Thread(target=pump, args=(proc.stdout, out_chunks, on_stdout, capture_stdout), daemon=True)
        t_err = threading.Thread(target=pump, args=(proc.stderr, err_chunks, on_stderr, True), daemon=True)
        t_out.start()
        t_err.start()
        deadline = time.monotonic() + timeout if timeout else None
        canceled = timed_out = False
        while proc.poll() is None:
            if cancel is not None and cancel.is_set():
                canceled = True
                kill_tree(proc, winjob)
                break
            if deadline is not None and time.monotonic() > deadline:
                timed_out = True
                kill_tree(proc, winjob)
                break
            time.sleep(0.1)
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            kill_tree(proc, None)
        t_out.join(timeout=5)
        t_err.join(timeout=5)
        if winjob is not None:
            winjob.close()
        return ProcessResult(proc.returncode, "\n".join(out_chunks), "\n".join(err_chunks), canceled, timed_out)


class BinaryGate:
    """Readers-writer gate: jobs use binaries (shared), the updater replaces them (exclusive)."""

    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._users = 0
        self._writer = False

    def acquire_shared(self, cancel: threading.Event | None = None) -> bool:
        with self._cond:
            while self._writer:
                if cancel is not None and cancel.is_set():
                    return False
                self._cond.wait(0.2)
            self._users += 1
            return True

    def release_shared(self) -> None:
        with self._cond:
            self._users -= 1
            self._cond.notify_all()

    def acquire_exclusive(self, timeout: float = 300.0) -> bool:
        deadline = time.monotonic() + timeout
        with self._cond:
            while self._writer:
                if not self._cond.wait(max(0.0, deadline - time.monotonic())):
                    return False
            self._writer = True  # blocks new users while the running ones finish
            while self._users > 0:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self._writer = False
                    self._cond.notify_all()
                    return False
                self._cond.wait(remaining)
            return True

    def release_exclusive(self) -> None:
        with self._cond:
            self._writer = False
            self._cond.notify_all()

    @property
    def replacing(self) -> bool:
        return self._writer


# =====================================================================================================
# Progress parsing
# =====================================================================================================

PROGRESS_MARK = "[[IDLP]]"
PP_MARK = "[[IDLPP]]"
FILE_MARK = "[[IDLFILE]]"

# %(field)j prints JSON; a missing field prints the NA placeholder, which parse_marker_line maps to null.
DOWNLOAD_TEMPLATE = ("download:" + PROGRESS_MARK +
                     '{"s":%(progress.status)j,"d":%(progress.downloaded_bytes)j,"t":%(progress.total_bytes)j,'
                     '"te":%(progress.total_bytes_estimate)j,"sp":%(progress.speed)j,"eta":%(progress.eta)j,'
                     '"f":%(info.format_id)j,"vc":%(info.vcodec)j}')
POSTPROCESS_TEMPLATE = "postprocess:" + PP_MARK + '{"s":%(progress.status)j,"pp":%(progress.postprocessor)j}'

_NA_RE = re.compile(r'(?<=[:\[,])\s*NA\s*(?=[,}\]])')


def progress_args() -> list[str]:
    return ["--newline", "--progress", "--progress-delta", "0.3",
            "--progress-template", DOWNLOAD_TEMPLATE,
            "--progress-template", POSTPROCESS_TEMPLATE]


def parse_marker_line(line: str) -> tuple[str, Any] | None:
    """Returns ("download", dict) | ("postprocess", dict) | ("file", path) | None for other lines."""
    for mark, kind in ((PROGRESS_MARK, "download"), (PP_MARK, "postprocess"), (FILE_MARK, "file")):
        idx = line.find(mark)
        if idx < 0:
            continue
        payload = line[idx + len(mark):].strip()
        if kind == "file":
            try:
                value = json.loads(payload)
            except ValueError:
                value = payload.strip('"')
            return (kind, value) if isinstance(value, str) and value else None
        try:
            data = json.loads(_NA_RE.sub("null", payload))
        except ValueError:
            return None
        return (kind, data) if isinstance(data, dict) else None
    return None


def parse_ffmpeg_progress(line: str) -> tuple[str, str] | None:
    """`-progress pipe:1` emits key=value lines; returns (key, value)."""
    if "=" not in line:
        return None
    key, _, value = line.partition("=")
    return key.strip(), value.strip()


class ProgressModel:
    """Maps per-stage fractions to one overall 0..1 value."""

    def __init__(self, weights: dict[str, float]) -> None:
        total = sum(w for w in weights.values() if w > 0) or 1.0
        self.order = [k for k, w in weights.items() if w > 0]
        self.weights = {k: weights[k] / total for k in self.order}
        self.fractions = {k: 0.0 for k in self.order}

    def set(self, stage: str, fraction: float) -> float:
        if stage in self.fractions:
            frac = min(1.0, max(0.0, float(fraction)))
            # never move backwards inside a stage (yt-dlp restarts at 0 for the next stream)
            self.fractions[stage] = max(self.fractions[stage], frac)
            idx = self.order.index(stage)
            for earlier in self.order[:idx]:
                self.fractions[earlier] = 1.0
        return self.value

    @property
    def value(self) -> float:
        return round(min(1.0, sum(self.weights[k] * self.fractions[k] for k in self.order)), 4)


def stage_weights(has_audio: bool, transcode: bool) -> dict[str, float]:
    return {
        "probe": 0.06,
        "video": 0.62 if has_audio else 0.72,
        "audio": 0.10 if has_audio else 0.0,
        "merge": 0.04 if has_audio else 0.0,
        "transcode": 0.30 if transcode else 0.0,
        "finalize": 0.02,
    }


# =====================================================================================================
# Error classification
# =====================================================================================================

ERROR_CODES = ("login_required", "private", "not_found", "rate_limited", "network", "engine_missing",
               "ffmpeg_missing", "extractor", "disk", "canceled", "unknown")

MESSAGES = {
    "login_required": "Instagram won't show this video without signing in. Sign in to Instagram in the app.",
    "private": "This account is private: only followers can see the video.",
    "not_found": "Post not found: it was deleted or the link is wrong.",
    "no_video": "This post has no video.",
    "rate_limited": "Instagram has temporarily limited requests. Wait a while or sign in.",
    "network": "Can't reach Instagram. Check your internet connection.",
    "engine_missing": "The download engine is not installed. Check your internet connection and update the engine.",
    "ffmpeg_missing": "ffmpeg is missing, so video and audio can't be merged. Update the engine.",
    "extractor": "Instagram changed its site and the engine can't read it yet. Try again later.",
    "disk": "Could not write the file: the disk is full or the folder is not accessible.",
    "canceled": "Download canceled.",
    "unknown": "Unknown error. See the log for details.",
    "session_invalid": "Your Instagram session has expired. Sign in again.",
}

# (code, message key, pattern). First match wins; matched against ERROR lines first, then the whole text.
ERROR_PATTERNS: list[tuple[str, str, re.Pattern[str]]] = [(c, m, re.compile(p, re.I)) for c, m, p in [
    ("disk", "disk", r"No space left on device|\[Errno 28\]|not enough space on the disk|\[WinError 112\]"
                     r"|Permission denied|\[Errno 13\]|\[WinError 5\]|Access is denied|unable to open for writing"
                     r"|Read-only file system"),
    ("ffmpeg_missing", "ffmpeg_missing", r"ffmpeg is not installed|ffprobe and ffmpeg not found|ffmpeg not found"
                                         r"|ffprobe not found|ffmpeg-location .* does not exist"),
    ("not_found", "no_video", r"There is no video in this post|No video formats found"),
    ("private", "private", r"only available for registered users who follow this account|This account is private"
                           r"|private account|PrivateAccount"),
    ("rate_limited", "rate_limited", r"HTTP Error 429|Too Many Requests|'429 |exceeded the rate-limit"
                                     r"|Please wait a few minutes|rate.?limit(ed)? exceeded"),
    ("login_required", "login_required", r"rate-limit reached or login required|login required|log in to"
                                         r"|redirect(ed)? to (the )?login page|registered users|empty media response"
                                         r"|Restricted Video|HTTP Error 401|401 Unauthorized|AuthorizationError"
                                         r"|checkpoint_required|challenge_required|login_required"
                                         r"|cookies are no longer valid|use --cookies"),
    ("not_found", "not_found", r"HTTP Error 404|404 Not Found|NotFoundError|Page Not Found|isn't available"
                               r"|Media not found|media is unavailable|post (was|has been) (removed|deleted)"),
    ("network", "network", r"getaddrinfo failed|Failed to resolve|Could not resolve|Name or service not known"
                           r"|Temporary failure in name resolution|timed out|Timeout was reached|Connection reset"
                           r"|Connection refused|Connection aborted|Failed to connect|Network is unreachable"
                           r"|No connection could be made|RemoteDisconnected|curl: \((5|6|7|28|35|52|56)\)"
                           r"|TransportError|urlopen error|SSL: |EOF occurred in violation|ConnectionError"
                           r"|HTTP Error 5\d\d"),
    ("extractor", "extractor", r"Unable to extract|Failed to parse JSON|please report this issue|Unsupported URL"
                               r"|KeyError|TypeError|IndexError|AttributeError|Traceback|Requested format is not"
                               r" available|Unable to (decode|parse)|AbortExtraction|is not a valid URL"
                               r"|Unsupported (site|URL)|ExtractorError"),
]]

_ERROR_LINE_RE = re.compile(r"^(ERROR:|.*\[error\])", re.I)


def classify_error(text: str) -> tuple[str, str]:
    """Map yt-dlp / gallery-dl stderr to (ErrorCode, Russian message)."""
    if not text:
        return "unknown", MESSAGES["unknown"]
    error_lines = "\n".join(line for line in text.splitlines() if _ERROR_LINE_RE.match(line.strip()))
    for haystack in (error_lines, text) if error_lines else (text,):
        for code, msg_key, pattern in ERROR_PATTERNS:
            if pattern.search(haystack):
                return code, MESSAGES[msg_key]
    return "unknown", MESSAGES["unknown"]


def session_rejected(stderr: str) -> bool:
    return "cookies are no longer valid" in stderr


# =====================================================================================================
# File names
# =====================================================================================================

_INVALID_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f\x7f]')
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10)),
             "COM¹", "COM²", "COM³", "LPT¹", "LPT²", "LPT³"}
MAX_NAME_LEN = 150


def sanitize_filename(name: str, max_len: int = MAX_NAME_LEN, fallback: str = "video") -> str:
    """Make a Windows-safe base name (no extension): invalid chars, reserved names, trailing dots, length."""
    name = re.sub(r"\s+", " ", name or "")
    name = _INVALID_CHARS.sub("_", name).strip()
    name = re.sub(r"_{2,}", "_", name)
    if len(name) > max_len:
        name = name[:max_len]
    name = name.strip(" .")
    if not name:
        name = fallback
    stem = name.split(".")[0].strip().upper()
    if stem in _RESERVED:
        name = "_" + name
    return name[:max_len]


def _first_line(text: str | None) -> str:
    for line in (text or "").splitlines():
        if line.strip():
            return line.strip()
    return ""


def meta_from_info(info: dict[str, Any]) -> dict[str, Any]:
    """Author, title, date, duration from a yt-dlp info dict."""
    author = info.get("channel") or info.get("uploader_id") or info.get("uploader") or "instagram"
    if isinstance(author, str) and author.isdigit() and info.get("uploader"):
        author = info.get("uploader")
    title = _first_line(info.get("description")) or info.get("title") or ""
    if len(title) > 200:
        title = title[:199].rstrip() + "…"
    date = ""
    ud = info.get("upload_date")
    if isinstance(ud, str) and re.fullmatch(r"\d{8}", ud):
        date = f"{ud[:4]}-{ud[4:6]}-{ud[6:]}"
    elif isinstance(info.get("timestamp"), (int, float)):
        date = time.strftime("%Y-%m-%d", time.gmtime(info["timestamp"]))
    duration = info.get("duration")
    return {
        "author": str(author),
        "title": title or None,
        "date": date,
        "duration_s": float(duration) if isinstance(duration, (int, float)) else None,
    }


def render_filename(template: str, shortcode: str, author: str | None, title: str | None, date: str | None) -> str:
    values = {
        "author": sanitize_filename(author or "", 60, "instagram"),
        "shortcode": sanitize_filename(shortcode, 60, "video"),
        "date": date or "",
        "title": sanitize_filename(title or "", 60, ""),
    }
    out = re.sub(r"\{(author|shortcode|date|title)\}", lambda m: values[m.group(1)], template)
    out = re.sub(r"([ _\-.])\1+", r"\1", out).strip(" _-.")
    return sanitize_filename(out, MAX_NAME_LEN, values["shortcode"])


def unique_path(path: str) -> str:
    if not os.path.exists(path):
        return path
    base, ext = os.path.splitext(path)
    for i in range(2, 1000):
        candidate = f"{base} ({i}){ext}"
        if not os.path.exists(candidate):
            return candidate
    return f"{base} ({uuid.uuid4().hex[:6]}){ext}"


# =====================================================================================================
# Format selection
# =====================================================================================================

QUALITY_CAPS = {"max": None, "1080": 1080, "720": 720}
CAP_TOLERANCE = 1.02  # Instagram serves 1088x1936 next to 1080x1920; "1080" keeps such near-1080 streams


def _is_avc(vcodec: str | None) -> bool:
    return bool(vcodec) and vcodec.lower().startswith(("avc1", "avc3", "h264"))


def _is_video(f: dict) -> bool:
    return f.get("vcodec") not in (None, "none") and bool(f.get("height") or f.get("width"))


def _is_audio_only(f: dict) -> bool:
    return f.get("vcodec") == "none" and f.get("acodec") not in (None, "none")


def _area(f: dict) -> int:
    w, h = f.get("width") or 0, f.get("height") or 0
    if w and h:
        return int(w * h)
    side = w or h
    return int(side * side * 16 / 9)


def _short_side(f: dict) -> int:
    w, h = f.get("width") or 0, f.get("height") or 0
    return min(w, h) if (w and h) else (w or h)


def _vkey(f: dict) -> tuple:
    return (_area(f), f.get("fps") or 0, f.get("vbr") or f.get("tbr") or 0)


@dataclass
class FormatChoice:
    spec: str
    sort_args: list[str]
    video: dict | None
    audio: dict | None
    needs_transcode: bool

    @property
    def has_audio(self) -> bool:
        return self.audio is not None


def select_formats(info: dict[str, Any], quality: str, h264_compat: bool) -> FormatChoice:
    """Pick video + audio: highest resolution, then fps, then bitrate; AVC at the same size when h264_compat."""
    formats = info.get("formats") or []
    cap = QUALITY_CAPS.get(quality)
    videos = [f for f in formats if _is_video(f) and f.get("format_id")]
    audios = [f for f in formats if _is_audio_only(f) and f.get("format_id")]
    if not videos:
        sort = "res,fps,br" if cap is None else f"res:{cap},fps,br"
        if h264_compat:
            sort += ",vcodec:avc,acodec:aac"
        return FormatChoice("bv*+ba/b", ["-S", sort], None, None, False)
    pool = videos
    if cap is not None:
        within = [f for f in videos if _short_side(f) <= cap * CAP_TOLERANCE]
        pool = within or [min(videos, key=_vkey)]
    best = max(pool, key=_vkey)
    chosen = best
    if h264_compat and not _is_avc(best.get("vcodec")):
        same = [f for f in pool if _is_avc(f.get("vcodec"))
                and _area(f) >= _area(best) * 0.98 and (f.get("fps") or 0) >= (best.get("fps") or 0) - 1]
        if same:
            chosen = max(same, key=_vkey)
    audio = None
    if audios:
        audio = max(audios, key=lambda f: (1 if (h264_compat and (f.get("acodec") or "").startswith("mp4a")) else 0,
                                           f.get("abr") or f.get("tbr") or 0, f.get("asr") or 0))
    vid = chosen["format_id"]
    spec = f"{vid}+{audio['format_id']}/{vid}" if audio else vid
    spec += "/bv*+ba/b"
    needs = bool(h264_compat and not _is_avc(chosen.get("vcodec")))
    return FormatChoice(spec, [], chosen, audio, needs)


# =====================================================================================================
# Auth ladder
# =====================================================================================================

@dataclass(frozen=True)
class Rung:
    engine: str  # "yt-dlp" | "gallery-dl"
    auth: str    # "anonymous" | "session" | "browser"


def plan_ladder(auth_mode: str, has_session: bool, browser: str) -> list[Rung]:
    if auth_mode == "never":
        return [Rung("yt-dlp", "anonymous")]
    rungs: list[Rung] = []
    if auth_mode == "auto":
        rungs.append(Rung("yt-dlp", "anonymous"))
    if has_session:
        rungs.append(Rung("yt-dlp", "session"))
    if browser:
        rungs.append(Rung("yt-dlp", "browser"))
    if has_session:
        rungs.append(Rung("gallery-dl", "session"))
    if not rungs:  # "always" without any credentials: anonymous is still worth a try
        rungs.append(Rung("yt-dlp", "anonymous"))
    return rungs


STOP_CODES = {"network", "disk", "ffmpeg_missing", "engine_missing", "canceled", "not_found"}


def ladder_action(code: str, rung: Rung, remaining: list[Rung], *, rate_retries_left: int,
                  self_heal_available: bool) -> str:
    """Decide what to do after a failed attempt: "next" | "backoff" | "self_heal" | "stop"."""
    if code in STOP_CODES:
        return "stop"
    if code == "rate_limited":
        if rung.auth == "anonymous" and any(r.auth != "anonymous" for r in remaining):
            return "next"  # signing in lifts the anonymous limit
        return "backoff" if rate_retries_left > 0 else "stop"
    if code == "extractor":
        if rung.engine == "yt-dlp" and self_heal_available:
            return "self_heal"
        return "next" if remaining else "stop"
    # login_required, private, unknown
    return "next" if remaining else "stop"


@dataclass
class Failure:
    rung: Rung
    code: str
    message: str


def pick_final_error(failures: list[Failure]) -> Failure:
    last = failures[-1]
    if last.code in STOP_CODES | {"private", "rate_limited"}:
        return last
    for code in ("private", "login_required", "rate_limited", "not_found"):
        for f in reversed(failures):
            if f.code == code:
                return f
    return last


def can_login_for(code: str, logged_in: bool) -> bool:
    if code == "login_required":
        return True
    if code in ("private", "rate_limited"):
        return not logged_in
    return False


# =====================================================================================================
# Media helpers (ffprobe, thumbnail)
# =====================================================================================================

def _parse_rate(value: str | None) -> float | None:
    if not value or value in ("0/0", "N/A"):
        return None
    try:
        if "/" in value:
            num, den = value.split("/", 1)
            return float(num) / float(den) if float(den) else None
        return float(value)
    except ValueError:
        return None


def parse_ffprobe_json(text: str, path: str | None = None) -> dict[str, Any]:
    data = json.loads(text or "{}")
    streams = data.get("streams") or []
    v = next((s for s in streams if s.get("codec_type") == "video" and not (s.get("disposition") or {}).get("attached_pic")), None)
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)
    fps = _parse_rate((v or {}).get("avg_frame_rate")) or _parse_rate((v or {}).get("r_frame_rate"))
    if fps is not None:
        fps = int(round(fps)) if abs(fps - round(fps)) < 0.01 else round(fps, 3)
    fmt = data.get("format") or {}
    size = None
    if path and os.path.exists(path):
        size = os.path.getsize(path)
    elif fmt.get("size"):
        size = int(fmt["size"])
    duration = None
    try:
        duration = float(fmt.get("duration")) if fmt.get("duration") else None
    except ValueError:
        pass
    return {
        "width": (v or {}).get("width"), "height": (v or {}).get("height"), "fps": fps,
        "vcodec": (v or {}).get("codec_name"), "acodec": (a or {}).get("codec_name"),
        "filesize": size, "duration": duration,
    }


def fetch_url(url: str, headers: dict[str, str] | None = None, timeout: float = 20.0, limit: int = 8 << 20) -> bytes:
    hdrs = {"User-Agent": USER_AGENT}
    if headers:
        hdrs.update({k: v for k, v in headers.items() if k.lower() in ("user-agent", "accept", "referer", "accept-language")})
    req = urllib.request.Request(url, headers=hdrs)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read(limit)


# =====================================================================================================
# Jobs
# =====================================================================================================

ACTIVE = ("queued", "probing", "downloading", "processing")
RUNNING = ("probing", "downloading", "processing")
FINISHED = ("done", "error", "canceled")
HISTORY_LIMIT = 300

PUBLIC_FIELDS = ("id", "url", "shortcode", "status", "stage_text", "progress", "speed_bps", "eta_s", "title",
                 "author", "duration_s", "thumbnail", "width", "height", "fps", "vcodec", "acodec", "filesize",
                 "filepath", "already_existed", "transcoded", "attempt", "error", "created_at", "finished_at")


@dataclass
class Job:
    id: str
    url: str
    shortcode: str
    status: str = "queued"
    stage_text: str = "Queued"
    progress: float = 0.0
    speed_bps: float | None = None
    eta_s: float | None = None
    title: str | None = None
    author: str | None = None
    duration_s: float | None = None
    thumbnail: str | None = None
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    vcodec: str | None = None
    acodec: str | None = None
    filesize: int | None = None
    filepath: str | None = None
    already_existed: bool = False
    transcoded: bool = False
    attempt: dict | None = None
    error: dict | None = None
    created_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    # internal
    cancel_event: threading.Event = field(default_factory=threading.Event, repr=False)
    removed: bool = False
    thumb_path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d = {k: getattr(self, k) for k in PUBLIC_FIELDS}
        d["attempt"] = dict(self.attempt) if self.attempt else {"engine": "yt-dlp", "auth": "anonymous"}
        d["error"] = dict(self.error) if self.error else None
        return d

    def to_history(self) -> dict[str, Any]:
        d = self.to_dict()
        d.pop("thumbnail", None)
        d["thumb_path"] = self.thumb_path
        return d

    @classmethod
    def from_history(cls, d: dict[str, Any]) -> "Job | None":
        try:
            job = cls(id=str(d["id"]), url=str(d["url"]), shortcode=str(d["shortcode"]))
        except (KeyError, TypeError):
            return None
        for k in PUBLIC_FIELDS:
            if k in d and k not in ("id", "url", "shortcode", "thumbnail"):
                setattr(job, k, d[k])
        job.thumb_path = d.get("thumb_path")
        if job.status not in FINISHED:
            job.status = "canceled"
            job.stage_text = "Interrupted when the app closed"
            job.speed_bps = job.eta_s = None
            job.finished_at = job.finished_at or time.time()
            job.error = {"code": "canceled", "message": "Download interrupted when the app closed.",
                         "can_login": False, "log_path": None}
        return job

    def reset_for_retry(self) -> None:
        self.status, self.stage_text, self.progress = "queued", "Queued", 0.0
        self.speed_bps = self.eta_s = None
        self.error = None
        self.finished_at = None
        self.already_existed = self.transcoded = False
        self.filesize = None
        self.filepath = None
        self.cancel_event = threading.Event()


class JobFailed(Exception):
    def __init__(self, code: str, message: str, stderr: str = "") -> None:
        super().__init__(message)
        self.code, self.message, self.stderr = code, message, stderr


class JobCanceled(Exception):
    pass


def data_url_jpeg(path: str) -> str | None:
    try:
        with open(path, "rb") as fh:
            return "data:image/jpeg;base64," + base64.b64encode(fh.read()).decode("ascii")
    except OSError:
        return None


# =====================================================================================================
# Downloader
# =====================================================================================================

class Downloader:
    """Thread-safe job table + worker pool running the per-job pipeline."""

    MAX_WORKERS = 4
    RATE_BACKOFF = (30.0, 90.0)
    EMIT_INTERVAL = 0.15
    PROBE_TIMEOUT = 180.0
    DOWNLOAD_TIMEOUT = 3600.0

    def __init__(self, paths: AppPaths, get_settings: Callable[[], dict], emit: EmitFn, *,
                 binaries: Callable[[], dict[str, str | None]],
                 usable: Callable[[], bool],
                 gate: BinaryGate | None = None,
                 self_heal: Callable[[], bool] | None = None,
                 on_auth_invalid: Callable[[], None] | None = None,
                 on_queue_drained: Callable[[int, int], None] | None = None,
                 runner: ProcessRunner | None = None,
                 fetch: Callable[..., bytes] = fetch_url,
                 sleep: Callable[[threading.Event, float], bool] | None = None) -> None:
        self.paths = paths
        self.get_settings = get_settings
        self.emit = emit
        self.binaries = binaries
        self.usable = usable
        self.gate = gate or BinaryGate()
        self.self_heal = self_heal or (lambda: False)
        self.on_auth_invalid = on_auth_invalid or (lambda: None)
        self.on_queue_drained = on_queue_drained or (lambda done, failed: None)
        self.runner = runner or ProcessRunner()
        self.fetch = fetch
        self.sleep = sleep or (lambda ev, s: ev.wait(s))
        self._lock = threading.RLock()
        self._cond = threading.Condition(self._lock)
        self._jobs: dict[str, Job] = {}
        self._order: list[str] = []  # oldest first
        self._queue: list[str] = []
        self._running: set[str] = set()
        self._last_emit: dict[str, float] = {}
        self._threads: list[threading.Thread] = []
        self._stopping = False
        self._concurrency = 2
        self._batch_done = 0
        self._batch_failed = 0
        self._history_lock = threading.Lock()

    # --- lifecycle -----------------------------------------------------------------------------------
    def start(self) -> None:
        self._concurrency = int(self.get_settings().get("concurrency", 2))
        for i in range(self.MAX_WORKERS):
            t = threading.Thread(target=self._worker, name=f"reels-job-{i}", daemon=True)
            t.start()
            self._threads.append(t)

    def shutdown(self, timeout: float = 2.5) -> None:
        with self._cond:
            self._stopping = True
            running = [self._jobs[j] for j in self._running if j in self._jobs]
            for job in running:
                job.cancel_event.set()
            self._cond.notify_all()
        deadline = time.monotonic() + timeout
        for t in self._threads:
            t.join(max(0.0, deadline - time.monotonic()))
        self.save_history(interrupted=True)

    def set_concurrency(self, n: int) -> None:
        with self._cond:
            self._concurrency = max(1, min(self.MAX_WORKERS, int(n)))
            self._cond.notify_all()

    def wake(self) -> None:
        with self._cond:
            self._cond.notify_all()

    # --- table ---------------------------------------------------------------------------------------
    def jobs(self) -> list[dict]:
        with self._lock:
            return [self._jobs[j].to_dict() for j in reversed(self._order)]

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def active_shortcodes(self) -> set[str]:
        with self._lock:
            return {j.shortcode for j in self._jobs.values() if j.status in ACTIVE}

    def add(self, url: str, shortcode: str) -> dict:
        job = Job(id=uuid.uuid4().hex[:8], url=url, shortcode=shortcode)
        with self._cond:
            while job.id in self._jobs:
                job.id = uuid.uuid4().hex[:8]
            self._jobs[job.id] = job
            self._order.append(job.id)
            self._queue.append(job.id)
            if not self.usable():
                job.stage_text = "Waiting for engine update"
            self._cond.notify_all()
        self._emit_job(job, force=True)
        self._save_soon()
        return job.to_dict()

    def cancel(self, job_id: str) -> bool:
        with self._cond:
            job = self._jobs.get(job_id)
            if job is None or job.status not in ACTIVE:
                return False
            job.cancel_event.set()
            if job.status == "queued":
                if job_id in self._queue:
                    self._queue.remove(job_id)
                self._finish_canceled(job)
        self._emit_job(job, force=True)
        self._save_soon()
        return True

    def retry(self, job_id: str) -> bool:
        with self._cond:
            job = self._jobs.get(job_id)
            if job is None or job.status not in ("error", "canceled"):
                return False
            if any(j.shortcode == job.shortcode and j.status in ACTIVE for j in self._jobs.values()):
                return False
            job.reset_for_retry()
            if not self.usable():
                job.stage_text = "Waiting for engine update"
            self._queue.append(job_id)
            self._order.remove(job_id)
            self._order.append(job_id)  # retried job moves to the top of the list
            self._cond.notify_all()
        self._emit_job(job, force=True)
        return True

    def remove(self, job_id: str) -> bool:
        with self._cond:
            job = self._jobs.pop(job_id, None)
            if job is None:
                return False
            job.removed = True
            job.cancel_event.set()
            if job_id in self._queue:
                self._queue.remove(job_id)
            self._order.remove(job_id)
        self._safe_emit("job_removed", {"id": job_id})
        self._save_soon()
        return True

    def clear_finished(self) -> int:
        with self._cond:
            ids = [j for j in self._order if self._jobs[j].status in FINISHED]
            for j in ids:
                self._jobs.pop(j).removed = True
                self._order.remove(j)
        for j in ids:
            self._safe_emit("job_removed", {"id": j})
        if ids:
            self._save_soon()
        return len(ids)

    def job_file(self, job_id: str) -> str | None:
        job = self.get(job_id)
        if job and job.status == "done" and job.filepath and os.path.isfile(job.filepath):
            return job.filepath
        return None

    def mark_waiting(self) -> None:
        """Updater state changed: refresh the stage text of queued jobs."""
        text = "Queued" if self.usable() else "Waiting for engine update"
        changed = []
        with self._cond:
            for j in self._queue:
                job = self._jobs.get(j)
                if job and job.stage_text != text:
                    job.stage_text = text
                    changed.append(job)
            self._cond.notify_all()
        for job in changed:
            self._emit_job(job, force=True)

    # --- history -------------------------------------------------------------------------------------
    def load_history(self) -> None:
        try:
            with open(self.paths.history_file, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except FileNotFoundError:
            return
        except (OSError, ValueError) as exc:
            log.warning("history unreadable (%s); starting empty", exc)
            return
        items = data.get("jobs") if isinstance(data, dict) else data
        if not isinstance(items, list):
            return
        restored: list[Job] = []
        for d in items[-HISTORY_LIMIT:]:
            if isinstance(d, dict):
                job = Job.from_history(d)
                if job is not None:
                    if job.thumb_path:
                        job.thumbnail = data_url_jpeg(job.thumb_path)
                    restored.append(job)
        restored.sort(key=lambda j: j.created_at or 0)
        with self._lock:
            for job in restored:
                if job.id not in self._jobs:
                    self._jobs[job.id] = job
                    self._order.append(job.id)

    def save_history(self, interrupted: bool = False) -> None:
        with self._lock:
            items = []
            for j in self._order:
                job = self._jobs[j]
                d = job.to_history()
                if interrupted and job.status in ACTIVE:
                    d["status"] = "canceled"
                    d["stage_text"] = "Interrupted when the app closed"
                items.append(d)
        finished = [d for d in items if d["status"] in FINISHED][-HISTORY_LIMIT:]
        active = [d for d in items if d["status"] not in FINISHED]
        with self._history_lock:
            try:
                atomic_write_json(self.paths.history_file, {"version": 1, "jobs": finished + active})
            except OSError as exc:
                log.warning("cannot save history: %s", exc)

    def _save_soon(self) -> None:
        threading.Thread(target=self.save_history, daemon=True).start()

    # --- emit ----------------------------------------------------------------------------------------
    def _safe_emit(self, event: str, payload: dict) -> None:
        try:
            self.emit(event, payload)
        except Exception:  # noqa: BLE001  a failing UI must never break the core
            log.exception("emit %s failed", event)

    def _emit_job(self, job: Job, force: bool = False) -> None:
        if job.removed:
            return
        now = time.monotonic()
        if not force and now - self._last_emit.get(job.id, 0.0) < self.EMIT_INTERVAL:
            return
        self._last_emit[job.id] = now
        with self._lock:
            payload = job.to_dict()
        self._safe_emit("job", payload)

    def _set(self, job: Job, force: bool = True, **fields: Any) -> None:
        with self._lock:
            for k, v in fields.items():
                setattr(job, k, v)
        self._emit_job(job, force=force)

    def _finish_canceled(self, job: Job) -> None:
        job.status, job.stage_text = "canceled", "Canceled"
        job.speed_bps = job.eta_s = None
        job.finished_at = time.time()
        job.error = {"code": "canceled", "message": MESSAGES["canceled"], "can_login": False, "log_path": None}

    # --- workers -------------------------------------------------------------------------------------
    def _next_job(self) -> Job | None:
        with self._cond:
            while True:
                if self._stopping:
                    return None
                if self._queue and len(self._running) < self._concurrency and self.usable():
                    job = self._jobs.get(self._queue.pop(0))
                    if job is None or job.status != "queued":
                        continue
                    self._running.add(job.id)
                    return job
                self._cond.wait(0.5)

    def _worker(self) -> None:
        while True:
            job = self._next_job()
            if job is None:
                return
            try:
                self._run_job(job)
            except Exception as exc:  # noqa: BLE001
                log.exception("job %s crashed", job.id)
                self._fail(job, Failure(Rung("yt-dlp", "anonymous"), "unknown", MESSAGES["unknown"]),
                           f"internal error: {exc!r}")
            finally:
                drained = False
                with self._cond:
                    self._running.discard(job.id)
                    if job.status == "done":
                        self._batch_done += 1
                    elif job.status == "error":
                        self._batch_failed += 1
                    if not self._queue and not self._running and not self._stopping:
                        drained = True
                        done, failed = self._batch_done, self._batch_failed
                        self._batch_done = self._batch_failed = 0
                    self._cond.notify_all()
                self._save_soon()
                if drained:
                    try:
                        self.on_queue_drained(done, failed)
                    except Exception:  # noqa: BLE001
                        log.exception("on_queue_drained failed")

    # --- pipeline ------------------------------------------------------------------------------------
    def _check_cancel(self, job: Job) -> None:
        if job.cancel_event.is_set():
            raise JobCanceled()

    def _run_job(self, job: Job) -> None:
        settings = self.get_settings()
        job_dir = self.paths.job_cache(job.id)
        logs: list[str] = []
        cookies = self.paths.cookies_file
        logged_in = authmod.has_session(cookies)
        ladder = plan_ladder(settings["auth_mode"], logged_in, settings.get("browser_cookies") or "")
        failures: list[Failure] = []
        rate_retries = len(self.RATE_BACKOFF)
        healed = False
        idx = 0
        try:
            while idx < len(ladder):
                rung = ladder[idx]
                self._check_cancel(job)
                try:
                    if rung.engine == "yt-dlp":
                        self._attempt_ytdlp(job, rung, settings, job_dir, logs)
                    else:
                        self._attempt_gallerydl(job, rung, settings, job_dir, logs)
                    return
                except JobFailed as fail:
                    if job.cancel_event.is_set():
                        raise JobCanceled() from None
                    failures.append(Failure(rung, fail.code, fail.message))
                    remaining = ladder[idx + 1:]
                    action = ladder_action(fail.code, rung, remaining, rate_retries_left=rate_retries,
                                           self_heal_available=not healed)
                    log.info("job %s rung %s/%s failed: %s -> %s", job.id, rung.engine, rung.auth, fail.code, action)
                    if action == "backoff":
                        delay = self.RATE_BACKOFF[len(self.RATE_BACKOFF) - rate_retries]
                        rate_retries -= 1
                        self._set(job, status="probing", stage_text=f"Instagram is limiting requests, waiting {int(delay)} s",
                                  speed_bps=None, eta_s=None)
                        if self.sleep(job.cancel_event, delay):
                            raise JobCanceled()
                        continue
                    if action == "self_heal":
                        healed = True
                        self._set(job, status="probing", stage_text="Updating the engine and retrying")
                        if self.self_heal():
                            continue
                        action = "next" if remaining else "stop"
                    if action == "next":
                        idx += 1
                        continue
                    break
            final = pick_final_error(failures) if failures else Failure(ladder[0], "unknown", MESSAGES["unknown"])
            self._fail(job, final, "\n".join(logs), logged_in=logged_in)
        except JobCanceled:
            with self._lock:
                self._finish_canceled(job)
                if self._stopping:
                    job.stage_text = "Interrupted when the app closed"
                    job.error = {"code": "canceled", "message": "Download interrupted when the app closed.",
                                 "can_login": False, "log_path": None}
            self._emit_job(job, force=True)
        finally:
            shutil.rmtree(job_dir, ignore_errors=True)

    def _fail(self, job: Job, failure: Failure, log_text: str, logged_in: bool | None = None) -> None:
        if logged_in is None:
            logged_in = authmod.has_session(self.paths.cookies_file)
        log_path = self.paths.job_log(job.id)
        try:
            with open(log_path, "w", encoding="utf-8") as fh:
                fh.write(f"job {job.id} {job.url}\nfinal error: {failure.code} ({failure.rung.engine}/{failure.rung.auth})\n\n")
                fh.write(log_text)
        except OSError:
            log_path = None
        message = failure.message
        if failure.code == "login_required" and logged_in:
            message = "Instagram rejected the saved sign-in. Sign in again."
        with self._lock:
            job.status = "error"
            job.stage_text = message
            job.speed_bps = job.eta_s = None
            job.finished_at = time.time()
            job.attempt = {"engine": failure.rung.engine, "auth": failure.rung.auth}
            job.error = {"code": failure.code, "message": message,
                         "can_login": can_login_for(failure.code, logged_in), "log_path": log_path}
        self._emit_job(job, force=True)

    # --- yt-dlp --------------------------------------------------------------------------------------
    def _bin(self, key: str) -> str:
        path = self.binaries().get(key)
        if not path or not os.path.exists(path):
            code = "ffmpeg_missing" if key in ("ffmpeg", "ffprobe") else "engine_missing"
            raise JobFailed(code, MESSAGES[code])
        return path

    def _run_tool(self, job: Job, argv: list[str], logs: list[str], *, label: str, timeout: float,
                  on_stdout=None, on_stderr=None, capture_stdout=True, gated: bool = True) -> ProcessResult:
        if gated and not self.gate.acquire_shared(job.cancel_event):
            raise JobCanceled()
        try:
            res = self.runner.run(argv, cwd=self.paths.job_cache(job.id), on_stdout=on_stdout,
                                  on_stderr=on_stderr, capture_stdout=capture_stdout,
                                  cancel=job.cancel_event, timeout=timeout)
        finally:
            if gated:
                self.gate.release_shared()
        logs.append(f"$ [{label}] " + " ".join(argv) + f"\nexit: {res.returncode}"
                    f"{' (timeout)' if res.timed_out else ''}\n{res.stderr}\n")
        if res.canceled or job.cancel_event.is_set():
            raise JobCanceled()
        if res.timed_out:
            raise JobFailed("network", MESSAGES["network"], res.stderr)
        return res

    def _ytdlp_base(self, rung: Rung, settings: dict, job_dir: str) -> list[str]:
        argv = [self._bin("yt-dlp"), "--no-config", "--encoding", "utf-8", "--color", "never",
                "--socket-timeout", "20", "--retries", "3", "--extractor-retries", "2", "--no-mtime"]
        if rung.auth == "session":
            jar = os.path.join(job_dir, "cookies.txt")
            if not os.path.exists(jar) and not authmod.copy_for_job(self.paths.cookies_file, jar):
                raise JobFailed("login_required", MESSAGES["login_required"])
            argv += ["--cookies", jar]
        elif rung.auth == "browser":
            argv += ["--cookies-from-browser", settings.get("browser_cookies") or "firefox"]
        return argv

    def _after_session_run(self, job_dir: str, stderr: str) -> None:
        jar = os.path.join(job_dir, "cookies.txt")
        if session_rejected(stderr) or (os.path.exists(jar) and not authmod.jar_has_session(jar)):
            if authmod.has_session(self.paths.cookies_file):
                authmod.mark_invalid(self.paths.cookies_file)
                self._safe_emit("toast", {"level": "warning", "text": MESSAGES["session_invalid"]})
                try:
                    self.on_auth_invalid()
                except Exception:  # noqa: BLE001
                    log.exception("on_auth_invalid failed")

    def _attempt_ytdlp(self, job: Job, rung: Rung, settings: dict, job_dir: str, logs: list[str]) -> None:
        job.attempt = {"engine": "yt-dlp", "auth": rung.auth}
        stage = {"anonymous": "Fetching info", "session": "Trying with your account",
                 "browser": "Trying with browser cookies"}[rung.auth]
        self._set(job, status="probing", stage_text=stage, progress=max(job.progress, 0.01),
                  speed_bps=None, eta_s=None)
        base = self._ytdlp_base(rung, settings, job_dir)
        res = self._run_tool(job, base + ["-J", job.url], logs, label="probe", timeout=self.PROBE_TIMEOUT)
        if rung.auth == "session":
            self._after_session_run(job_dir, res.stderr)
        if res.returncode != 0 or not res.stdout.strip():
            code, msg = classify_error(res.stderr)
            raise JobFailed(code, msg, res.stderr)
        try:
            info = json.loads(res.stdout)
        except ValueError:
            raise JobFailed("extractor", MESSAGES["extractor"], res.stderr) from None
        info = self._pick_entry(info)
        if info is None:
            raise JobFailed("not_found", MESSAGES["no_video"], res.stderr)
        info_path = os.path.join(job_dir, "info.json")
        with open(info_path, "w", encoding="utf-8") as fh:
            json.dump(info, fh, ensure_ascii=False)
        meta = meta_from_info(info)
        self._set(job, title=meta["title"], author=meta["author"], duration_s=meta["duration_s"])
        self._make_thumbnail(job, info, job_dir)
        self._check_cancel(job)

        target = self._target_path(job, settings, meta)
        if target is None:
            return  # skip_existing hit, job finished
        choice = select_formats(info, settings["quality"], settings["h264_compat"])
        model = ProgressModel(stage_weights(choice.has_audio or choice.video is None, choice.needs_transcode))
        self._set(job, status="downloading", stage_text="Downloading video", progress=model.set("probe", 1.0))

        out_tmpl = os.path.join(job_dir, "media.%(ext)s")
        argv = base + ["--load-info-json", info_path, "-f", choice.spec, *choice.sort_args,
                       "--merge-output-format", "mp4", "--ffmpeg-location", self._bin("ffmpeg"),
                       "-o", out_tmpl, "--no-part", "--force-overwrites", *progress_args(),
                       "--print", "after_move:" + FILE_MARK + "%(filepath)j", "--no-simulate"]
        state = {"file": None, "stage": "video"}
        audio_id = choice.audio["format_id"] if choice.audio else None

        def on_line(line: str) -> None:
            parsed = parse_marker_line(line)
            if parsed is None:
                return
            kind, data = parsed
            if kind == "file":
                state["file"] = data
                return
            if kind == "download":
                is_audio = data.get("vc") == "none" or (audio_id is not None and data.get("f") == audio_id)
                stage_name = "audio" if (is_audio and "audio" in model.weights) else "video"
                total = data.get("t") or data.get("te")
                done = data.get("d") or 0
                frac = (done / total) if total else 0.0
                if data.get("s") == "finished":
                    frac = 1.0
                text = "Downloading audio" if stage_name == "audio" else "Downloading video"
                prog = model.set(stage_name, frac)
                force = job.stage_text != text
                self._set(job, force=force, status="downloading", stage_text=text, progress=prog,
                          speed_bps=data.get("sp"), eta_s=data.get("eta"))
            elif kind == "postprocess":
                pp = data.get("pp") or ""
                if pp == "Merger":
                    frac = 1.0 if data.get("s") == "finished" else 0.3
                    self._set(job, status="processing", stage_text="Merging tracks",
                              progress=model.set("merge", frac), speed_bps=None, eta_s=None)

        res = self._run_tool(job, argv, logs, label="download", timeout=self.DOWNLOAD_TIMEOUT,
                             on_stdout=on_line, on_stderr=on_line)
        if rung.auth == "session":
            self._after_session_run(job_dir, res.stderr)
        media = state["file"] if state["file"] and os.path.exists(state["file"]) else self._find_media(job_dir)
        if res.returncode != 0 or not media:
            code, msg = classify_error(res.stderr)
            if code == "unknown" and not media:
                code, msg = "extractor", MESSAGES["extractor"]
            raise JobFailed(code, msg, res.stderr)
        self._finalize(job, media, target, settings, model, logs)

    @staticmethod
    def _pick_entry(info: dict) -> dict | None:
        """A carousel post (/p/) is a playlist; take the first entry that has video formats."""
        if info.get("_type") in ("playlist", "multi_video"):
            for entry in info.get("entries") or []:
                if isinstance(entry, dict) and any(_is_video(f) or f.get("vcodec") is None
                                                   for f in entry.get("formats") or []):
                    for k in ("channel", "uploader", "uploader_id", "description", "upload_date", "timestamp"):
                        entry.setdefault(k, info.get(k))
                    return entry
            return None
        return info if (info.get("formats") or info.get("url")) else None

    @staticmethod
    def _find_media(job_dir: str) -> str | None:
        best = None
        for name in os.listdir(job_dir):
            if name.startswith("media") and name.endswith((".mp4", ".mkv", ".webm", ".mov", ".m4v")):
                path = os.path.join(job_dir, name)
                if best is None or os.path.getsize(path) > os.path.getsize(best):
                    best = path
        return best

    def _target_path(self, job: Job, settings: dict, meta: dict) -> str | None:
        """Final output path, or None when skip_existing found the file (job is then finished as done)."""
        out_dir = settings["output_dir"]
        try:
            os.makedirs(out_dir, exist_ok=True)
        except OSError as exc:
            raise JobFailed("disk", MESSAGES["disk"], str(exc)) from None
        name = render_filename(settings["filename_template"], job.shortcode, meta["author"], meta["title"], meta["date"])
        path = os.path.join(out_dir, name + ".mp4")
        if settings["skip_existing"] and os.path.isfile(path) and os.path.getsize(path) > 0:
            fields = self._probe_media(path) or {}
            with self._lock:
                job.status, job.stage_text, job.progress = "done", "Already downloaded", 1.0
                job.already_existed = True
                job.filepath = path
                job.finished_at = time.time()
                job.speed_bps = job.eta_s = None
                for k in ("width", "height", "fps", "vcodec", "acodec", "filesize"):
                    setattr(job, k, fields.get(k))
                job.filesize = fields.get("filesize") or os.path.getsize(path)
            self._emit_job(job, force=True)
            return None
        return path

    def _probe_media(self, path: str) -> dict | None:
        ffprobe = self.binaries().get("ffprobe")
        if not ffprobe or not os.path.exists(ffprobe):
            return None
        res = self.runner.run([ffprobe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path],
                              timeout=60)
        if res.returncode != 0:
            return None
        try:
            return parse_ffprobe_json(res.stdout, path)
        except ValueError:
            return None

    def _finalize(self, job: Job, media: str, target: str, settings: dict, model: ProgressModel,
                  logs: list[str]) -> None:
        fields = self._probe_media(media) or {}
        transcoded = False
        if settings["h264_compat"] and fields.get("vcodec") and fields["vcodec"] != "h264":
            if "transcode" not in model.weights:  # the stream metadata did not announce a non-AVC codec
                model = ProgressModel(stage_weights(True, True))
                model.set("merge", 1.0)
            media = self._transcode(job, media, fields, model, logs)
            transcoded = True
            fields = self._probe_media(media) or {}
        self._set(job, status="processing", stage_text="Saving file", progress=model.set("finalize", 0.5),
                  speed_bps=None, eta_s=None)
        final = unique_path(target)  # never overwrite a file of the user
        try:
            tmp = final + ".part"
            shutil.move(media, tmp)
            os.replace(tmp, final)
        except OSError as exc:
            raise JobFailed("disk", MESSAGES["disk"], str(exc)) from None
        if not fields:
            fields = self._probe_media(final) or {}
        with self._lock:
            job.status, job.stage_text, job.progress = "done", "Done", 1.0
            job.filepath = final
            job.transcoded = transcoded
            job.finished_at = time.time()
            job.speed_bps = job.eta_s = None
            for k in ("width", "height", "fps", "vcodec", "acodec"):
                setattr(job, k, fields.get(k))
            job.filesize = os.path.getsize(final)
            if fields.get("duration") and not job.duration_s:
                job.duration_s = fields["duration"]
        self._emit_job(job, force=True)

    def _transcode(self, job: Job, src: str, fields: dict, model: ProgressModel, logs: list[str]) -> str:
        ffmpeg = self._bin("ffmpeg")
        dst = os.path.join(os.path.dirname(src), "h264.mp4")
        duration = fields.get("duration") or job.duration_s or 0
        argv = [ffmpeg, "-hide_banner", "-nostdin", "-y", "-loglevel", "error", "-i", src,
                "-map", "0:v:0", "-map", "0:a:0?", "-c:v", "libx264", "-crf", "16", "-preset", "slow",
                "-pix_fmt", "yuv420p", "-profile:v", "high", "-movflags", "+faststart"]
        acodec = fields.get("acodec")
        argv += ["-c:a", "copy"] if acodec in (None, "aac") else ["-c:a", "aac", "-b:a", "256k"]
        argv += ["-progress", "pipe:1", "-nostats", dst]
        self._set(job, status="processing", stage_text="Converting to H.264", progress=model.set("transcode", 0.0),
                  speed_bps=None, eta_s=None)
        started = time.monotonic()

        def on_line(line: str) -> None:
            kv = parse_ffmpeg_progress(line)
            if not kv or not duration:
                return
            key, value = kv
            if key in ("out_time_us", "out_time_ms") and value.isdigit():
                done_s = int(value) / 1_000_000
                frac = min(1.0, done_s / duration)
                elapsed = time.monotonic() - started
                eta = int(elapsed / frac - elapsed) if frac > 0.02 else None
                self._set(job, force=False, progress=model.set("transcode", frac), eta_s=eta)

        res = self._run_tool(job, argv, logs, label="transcode", timeout=self.DOWNLOAD_TIMEOUT,
                             on_stdout=on_line, gated=False)
        if res.returncode != 0 or not os.path.exists(dst):
            code, msg = classify_error(res.stderr)
            if code in ("unknown", "extractor"):
                code, msg = "unknown", "Could not convert the video to H.264. See the log for details."
            raise JobFailed(code, msg, res.stderr)
        try:
            os.remove(src)
        except OSError:
            pass
        return dst

    def _make_thumbnail(self, job: Job, info: dict, job_dir: str) -> None:
        """Fetch the poster, scale to <= 480 px wide, keep in cache/thumbs and send as a data URL."""
        url = info.get("thumbnail")
        if not url:
            thumbs = [t for t in info.get("thumbnails") or [] if t.get("url")]
            url = thumbs[-1]["url"] if thumbs else None
        if not url:
            return
        dest = self.paths.thumb_file(sanitize_filename(job.shortcode, 60))
        try:
            headers = info.get("http_headers") or {}
            raw = self.fetch(url, headers)
            src = os.path.join(job_dir, "thumb_src")
            with open(src, "wb") as fh:
                fh.write(raw)
            ffmpeg = self.binaries().get("ffmpeg")
            ok = False
            if ffmpeg and os.path.exists(ffmpeg):
                res = self.runner.run([ffmpeg, "-hide_banner", "-nostdin", "-y", "-loglevel", "error", "-i", src,
                                       "-vf", "scale='min(480,iw)':-2", "-frames:v", "1", "-q:v", "4",
                                       "-f", "image2", "-c:v", "mjpeg", dest], timeout=30)
                ok = res.returncode == 0 and os.path.exists(dest)
            if not ok:
                if len(raw) > 400_000:
                    return
                shutil.copyfile(src, dest)
        except Exception as exc:  # noqa: BLE001  the thumbnail is cosmetic
            log.info("thumbnail for %s failed: %s", job.id, exc)
            return
        self._set(job, thumbnail=data_url_jpeg(dest), thumb_path=dest)

    # --- gallery-dl ----------------------------------------------------------------------------------
    def _attempt_gallerydl(self, job: Job, rung: Rung, settings: dict, job_dir: str, logs: list[str]) -> None:
        job.attempt = {"engine": "gallery-dl", "auth": rung.auth}
        self._set(job, status="probing", stage_text="Trying fallback engine", speed_bps=None, eta_s=None)
        exe = self._bin("gallery-dl")
        jar = os.path.join(job_dir, "cookies-gdl.txt")
        if not authmod.copy_for_job(self.paths.cookies_file, jar):
            raise JobFailed("login_required", MESSAGES["login_required"])
        base = [exe, "--config-ignore", "--cookies", jar, "-o", "videos=true"]
        res = self._run_tool(job, base + ["-j", job.url], logs, label="gallery-dl probe", timeout=self.PROBE_TIMEOUT)
        kw = self._gdl_video_kwdict(res.stdout)
        if kw is None:
            text = res.stderr + "\n" + self._gdl_errors(res.stdout)
            code, msg = classify_error(text)
            if res.returncode == 0 and code == "unknown":
                code, msg = "not_found", MESSAGES["no_video"]
            raise JobFailed(code, msg, text)
        info = {"channel": kw.get("username"), "uploader": kw.get("fullname"), "description": kw.get("description"),
                "thumbnail": kw.get("display_url")}
        date = kw.get("date") or kw.get("post_date")
        if isinstance(date, str) and re.match(r"\d{4}-\d{2}-\d{2}", date):
            info["upload_date"] = date[:10].replace("-", "")
        meta = meta_from_info(info)
        self._set(job, title=meta["title"], author=meta["author"])
        if not job.thumbnail:
            self._make_thumbnail(job, info, job_dir)
        target = self._target_path(job, settings, meta)
        if target is None:
            return
        model = ProgressModel(stage_weights(False, settings["h264_compat"]))
        self._set(job, status="downloading", stage_text="Downloading video (fallback engine)",
                  progress=model.set("probe", 1.0))
        out_dir = os.path.join(job_dir, "gdl")
        files: list[str] = []

        def on_out(line: str) -> None:
            path = line.strip().lstrip("# ").strip()
            if path.lower().endswith(".mp4") and os.path.isabs(path):
                files.append(path)
                self._set(job, progress=model.set("video", 1.0))

        res = self._run_tool(job, base + ["-D", out_dir, "-f", "{shortcode}_{num}.{extension}", "--no-mtime",
                                          "--filter", "extension == 'mp4'", job.url], logs,
                             label="gallery-dl download", timeout=self.DOWNLOAD_TIMEOUT, on_stdout=on_out)
        if not files and os.path.isdir(out_dir):
            files = sorted(os.path.join(out_dir, n) for n in os.listdir(out_dir) if n.lower().endswith(".mp4"))
        files = [f for f in files if os.path.exists(f)]
        if not files:
            code, msg = classify_error(res.stderr)
            raise JobFailed(code, msg, res.stderr)
        self._finalize(job, sorted(files)[0], target, settings, model, logs)

    @staticmethod
    def _gdl_messages(stdout: str) -> list:
        try:
            data = json.loads(stdout or "[]")
        except ValueError:
            return []
        return data if isinstance(data, list) else []

    def _gdl_video_kwdict(self, stdout: str) -> dict | None:
        for msg in self._gdl_messages(stdout):
            # Message.Url == 3: [3, url, kwdict]
            if isinstance(msg, list) and len(msg) >= 3 and msg[0] == 3 and isinstance(msg[2], dict):
                kw = msg[2]
                if kw.get("extension") == "mp4" or kw.get("video_url"):
                    return kw
        return None

    def _gdl_errors(self, stdout: str) -> str:
        out = []
        for msg in self._gdl_messages(stdout):
            if isinstance(msg, list) and len(msg) >= 2 and msg[0] == -1 and isinstance(msg[1], dict):
                out.append(f"[error] {msg[1].get('error')}: {msg[1].get('message')}")
        return "\n".join(out)


def iter_terminal(jobs: Iterable[dict]) -> bool:
    return all(j["status"] in FINISHED for j in jobs)
