"""Job queue, preview worker, subprocess control, progress parsing, error classification and the auth ladder.

Pipeline per job (ARCHITECTURE.md 5.2): probe with `yt-dlp -J` (or reuse the preview's info json) -> info json in
cache/jobs/<id>/ -> thumbnail -> download with `--load-info-json` -> mp4: merge + optional H.264 pass, mp3: extract
audio -> ffprobe the final file. Auth ladder per platform: Instagram walks anonymous -> session cookies -> browser
cookies -> gallery-dl with the session; YouTube, TikTok and X walk anonymous -> browser cookies.

Preview (ARCHITECTURE.md 5.4): one daemon thread fetches the anonymous info json of queued jobs, one at a time,
fills title / author / duration / thumbnail and sets `previewed`.

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

log = logging.getLogger("mediasaver.core.downloader")

EmitFn = Callable[[str, dict], None]

CREATE_NO_WINDOW = 0x08000000
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/150.0.0.0 Safari/537.36")

# =====================================================================================================
# Subprocess control
# =====================================================================================================


# Extra variables for every engine subprocess; the Downloader sets DENO_DIR here so the deno cache that yt-dlp's
# challenge solver creates stays inside the portable data folder instead of %LOCALAPPDATA%\deno.
SUBPROCESS_ENV: dict[str, str] = {}


def subprocess_env() -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env.pop("PYTHONHOME", None)  # a frozen parent must not leak its Python into the standalone engines
    env.pop("PYTHONPATH", None)
    env.update(SUBPROCESS_ENV)
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


def mp3_stage_weights() -> dict[str, float]:
    """mp3 jobs: probe, audio download, MP3 encode (ExtractAudio postprocessor), finalize."""
    return {"probe": 0.06, "audio": 0.72, "convert": 0.20, "finalize": 0.02}


# =====================================================================================================
# Error classification
# =====================================================================================================

ERROR_CODES = ("login_required", "private", "not_found", "rate_limited", "network", "engine_missing",
               "ffmpeg_missing", "extractor", "disk", "canceled", "unknown")

PLATFORM_NAMES = {"instagram": "Instagram", "youtube": "YouTube", "tiktok": "TikTok", "twitter": "X"}

# Instagram texts and the platform-neutral ones. Jobs of another platform take that platform's table first
# (PLATFORM_MESSAGES, see message_for), so no message of a job names a different site.
MESSAGES = {
    "login_required": "Instagram won't show this video without signing in. Sign in to Instagram in the app.",
    "private": "This account is private: only followers can see the video.",
    "not_found": "Post not found: it was deleted or the link is wrong.",
    "no_video": "This post has no video.",
    "no_audio": "This video has no sound, so there is nothing to save as MP3.",
    "live": "Live streams and premieres are not supported.",
    "rate_limited": "Instagram has temporarily limited requests. Wait a while or sign in.",
    "network": "Can't reach Instagram. Check your internet connection.",
    "engine_missing": "The download engine is not installed. Check your internet connection and update the engine.",
    "ffmpeg_missing": ("ffmpeg is missing: it is needed to merge video and audio and to make MP3 files. "
                       "Update the engine to fetch it."),
    "extractor": "Instagram changed its site and the engine can't read it yet. Try again later.",
    "disk": "Could not write the file: the disk is full or the folder is not accessible.",
    "canceled": "Download canceled.",
    "unknown": "Unknown error. See the log for details.",
    "session_invalid": "Your Instagram session has expired. Sign in again.",
    "session_rejected": "Instagram rejected the saved sign-in. Sign in again.",
}

YOUTUBE_MESSAGES = {
    "login_required": ("YouTube wants a signed-in browser for this video. "
                       "Choose a browser in Settings > Browser cookies."),
    "private": "This video is private or for channel members only.",
    "not_found": "Video not found: it was deleted, is unavailable or the link is wrong.",
    "no_video": "This video has nothing to download.",
    "live": "Live streams and premieres are not supported.",
    "rate_limited": ("YouTube has temporarily limited requests. Wait a while or choose a browser in "
                     "Settings > Browser cookies."),
    "network": "Can't reach YouTube. Check your internet connection.",
    "extractor": "YouTube changed its site and the engine can't read it yet. Try again later.",
}


TIKTOK_MESSAGES = {
    "login_required": ("TikTok wants a signed-in browser for this video. "
                       "Choose a browser in Settings > Browser cookies."),
    "private": "This video is private or limited to the creator's friends.",
    "not_found": "Video not found: it was deleted, is unavailable or the link is wrong.",
    "no_video": "This TikTok is a photo post or has no video.",
    "blocked": "TikTok does not serve this video to your network (region or IP block). Try another connection.",
    "live": "TikTok LIVE streams are not supported.",
    "rate_limited": ("TikTok has temporarily limited requests. Wait a while or choose a browser in "
                     "Settings > Browser cookies."),
    "network": "Can't reach TikTok. Check your internet connection.",
    "extractor": "TikTok changed its site and the engine can't read it yet. Try again later.",
}

TWITTER_MESSAGES = {
    "login_required": ("X shows this post only to signed-in users (age-restricted or sensitive content). "
                       "Choose a browser in Settings > Browser cookies."),
    "private": "This account is protected or suspended: the post is not public.",
    "not_found": "Post not found: it was deleted, is unavailable or the link is wrong.",
    "no_video": "This post has no video.",
    "live": "X live broadcasts and Spaces are not supported.",
    "rate_limited": ("X has temporarily limited requests. Wait a while or choose a browser in "
                     "Settings > Browser cookies."),
    "network": "Can't reach X. Check your internet connection.",
    "extractor": "X changed its site and the engine can't read it yet. Try again later.",
}

PLATFORM_MESSAGES = {"youtube": YOUTUBE_MESSAGES, "tiktok": TIKTOK_MESSAGES, "twitter": TWITTER_MESSAGES}


def message_for(key: str, platform: str | None = "instagram") -> str:
    """User-facing text of a message key for a job of `platform`; never names another platform."""
    table = PLATFORM_MESSAGES.get(platform or "")
    if table and key in table:
        return table[key]
    return MESSAGES[key]


# YouTube-only patterns (code, message key, pattern), checked after disk / ffmpeg and before the shared table.
YOUTUBE_PATTERNS: list[tuple[str, str, re.Pattern[str]]] = [(c, m, re.compile(p, re.I)) for c, m, p in [
    ("private", "private", r"Private video|members-only|Join this channel|available to this channel's members"
                           r"|members on level"),
    ("login_required", "login_required", r"Sign in to confirm you.re not a bot|Sign in to confirm your age"
                                         r"|age.restricted|inappropriate for some users|requires login"
                                         r"|confirm you.re not a bot"),
    ("rate_limited", "rate_limited", r"rate-limited by YouTube|has been rate.limited|try again later"),
    ("not_found", "live", r"live event|Premieres? in |Premiere will begin|live stream|is live now"
                          r"|This live|is a live"),
    ("not_found", "not_found", r"Video unavailable|This video is not available|This video is unavailable"
                               r"|has been removed|no longer available|has been terminated"
                               r"|not made this video available|is not available in your country"),
]]

# TikTok-only patterns, same position as the YouTube ones. Texts from yt-dlp's TikTok extractor (2026-10).
TIKTOK_PATTERNS: list[tuple[str, str, re.Pattern[str]]] = [(c, m, re.compile(p, re.I)) for c, m, p in [
    ("not_found", "blocked", r"IP address is blocked|not available in your (country|region)|geo.?restricted"
                             r"|region.?lock"),
    ("private", "private", r"Video is private|This post is private|private video|friends.only|only visible to"),
    ("login_required", "login_required", r"requires? (a )?log.?in|log in to view|Sign in to|age.restricted"
                                         r"|login.required|Use --cookies"),
    ("not_found", "live", r"is a live|live stream|LIVE room"),
    ("not_found", "no_video", r"photo (post|slideshow)|image (post|slideshow)|no video in this post"
                              r"|Unsupported post type"),
    ("not_found", "not_found", r"Video not available|video has been removed|has been deleted|Unable to find video"
                               r"|This video is unavailable|item does not exist|status_code: 100"),
]]

# X-only patterns, same position. Texts from yt-dlp's Twitter extractor (2026-10).
TWITTER_PATTERNS: list[tuple[str, str, re.Pattern[str]]] = [(c, m, re.compile(p, re.I)) for c, m, p in [
    ("login_required", "login_required", r"NSFW tweet requires authentication|requires authentication"
                                         r"|age.restricted|Sign in to|login required|requires? (a )?log.?in"
                                         r"|Use --cookies"),
    ("private", "private", r"protected tweet|protected account|suspended account|account (is|has been) suspended"
                           r"|This account is private"),
    ("not_found", "live", r"broadcast|live stream|is live|Spaces? (is|are) not|audio space"),
    ("not_found", "no_video", r"No video could be found in this tweet|has no video|contains no video"
                              r"|tweet does not contain|only images"),
    ("not_found", "not_found", r"tweet is unavailable|Tweet not found|No status found|has been deleted|does not exist"
                               r"|Sorry, that page does not exist|Could not find tweet|no longer available"),
]]

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


PLATFORM_PATTERNS = {"youtube": YOUTUBE_PATTERNS, "tiktok": TIKTOK_PATTERNS, "twitter": TWITTER_PATTERNS}


def _patterns_for(platform: str | None) -> list[tuple[str, str, re.Pattern[str]]]:
    """Disk and ffmpeg rows first, then the platform's own rows, then the shared table."""
    own = PLATFORM_PATTERNS.get(platform or "")
    if own:
        return ERROR_PATTERNS[:2] + own + ERROR_PATTERNS[2:]
    return ERROR_PATTERNS


def classify_error(text: str, platform: str | None = "instagram") -> tuple[str, str]:
    """Map yt-dlp / gallery-dl stderr to (ErrorCode, English message for a job of `platform`)."""
    if not text:
        return "unknown", message_for("unknown", platform)
    error_lines = "\n".join(line for line in text.splitlines() if _ERROR_LINE_RE.match(line.strip()))
    for haystack in (error_lines, text) if error_lines else (text,):
        for code, msg_key, pattern in _patterns_for(platform):
            if pattern.search(haystack):
                return code, message_for(msg_key, platform)
    return "unknown", message_for("unknown", platform)


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


_TCO_RE = re.compile(r"\s*https?://t\.co/\S+")


def _info_platform(info: dict[str, Any]) -> str:
    key = str(info.get("extractor_key") or info.get("extractor") or "").lower()
    for prefix, platform in (("youtube", "youtube"), ("tiktok", "tiktok"), ("twitter", "twitter")):
        if key.startswith(prefix):
            return platform
    return "instagram"


def meta_from_info(info: dict[str, Any], platform: str | None = None) -> dict[str, Any]:
    """Author, title, date, duration from a yt-dlp info dict.

    Instagram: author = username (`channel`), title = first caption line. YouTube: author = channel name, title =
    the video title (the description is free text there). TikTok: author = the @handle (`uploader`; `channel` is
    the display name, `uploader_id` a number), title = first caption line. X: author = the @handle
    (`uploader_id`; `uploader` is the display name), title = the post text without its trailing t.co link
    (yt-dlp's `title` is "<display name> - <text>").
    """
    platform = platform or _info_platform(info)
    if platform == "tiktok":
        author = info.get("uploader") or info.get("channel") or info.get("uploader_id") or "tiktok"
    elif platform == "twitter":
        author = info.get("uploader_id") or info.get("uploader") or info.get("channel") or "x"
    else:
        author = (info.get("channel") or info.get("uploader_id") or info.get("uploader")
                  or ("youtube" if platform == "youtube" else "instagram"))
    if isinstance(author, str) and author.isdigit() and info.get("uploader"):
        author = info.get("uploader")
    if platform == "youtube":
        title = _first_line(info.get("title")) or _first_line(info.get("fulltitle")) or ""
    elif platform == "twitter":
        text = _TCO_RE.sub("", str(info.get("description") or "")).strip()
        title = _first_line(text) or _first_line(info.get("title")) or ""
    else:
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


def render_filename(template: str, shortcode: str, author: str | None, title: str | None, date: str | None,
                    fallback_author: str = "instagram") -> str:
    """Base name (no extension) from the template; the caller adds ".mp4" / ".mp3" by the job format."""
    values = {
        "author": sanitize_filename(author or "", 60, fallback_author),
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

# Short-side caps per platform setting (`quality_<platform>`); None = best available.
QUALITY_CAPS = {
    "instagram": {"low": 480, "medium": 720, "max": None},
    "youtube": {"360": 360, "720": 720, "max": None},
    "tiktok": {"low": 480, "medium": 720, "max": None},
    "twitter": {"low": 480, "medium": 720, "max": None},
}
QUALITY_SETTING = {"instagram": "quality_instagram", "youtube": "quality_youtube", "tiktok": "quality_tiktok",
                   "twitter": "quality_twitter"}
CAP_TOLERANCE = 1.02  # a cap keeps near-cap streams (Instagram serves 716x1274 next to 720x1280, 1088x1936 next to 1080x1920)


def quality_cap(platform: str, quality: str) -> int | None:
    """Short-side cap in pixels for a platform's quality value; None for "max" and for unknown values."""
    return QUALITY_CAPS.get(platform, {}).get(quality)


def _is_avc(vcodec: str | None) -> bool:
    return bool(vcodec) and vcodec.lower().startswith(("avc1", "avc3", "h264"))


def _usable_format(f: dict) -> bool:
    """Skips DRM formats, YouTube storyboards (mhtml image sheets) and TikTok's watermarked copy."""
    note = str(f.get("format_note") or "").lower()
    return (bool(f.get("format_id")) and not f.get("has_drm") and f.get("ext") != "mhtml"
            and "storyboard" not in note and "watermark" not in note)


def _is_video(f: dict) -> bool:
    """A format with a video codec, or one with a picture size whose codecs are unknown (X's progressive `http-*`
    mp4 files report neither `vcodec` nor `acodec`; they are muxed H.264 + AAC)."""
    if not (bool(f.get("height") or f.get("width")) and _usable_format(f)):
        return False
    vcodec = f.get("vcodec")
    return vcodec not in (None, "none") or (vcodec is None and f.get("acodec") is None)


def _is_audio_only(f: dict) -> bool:
    return f.get("vcodec") == "none" and f.get("acodec") not in (None, "none") and _usable_format(f)


def _has_audio(f: dict) -> bool:
    """Unknown codecs on both sides (X's `http-*` files) count as a muxed file with audio."""
    acodec = f.get("acodec")
    return acodec not in (None, "none") or (acodec is None and f.get("vcodec") is None)


def _direct(f: dict) -> int:
    """1 for a plain https file (DASH on YouTube and Instagram), 0 for HLS / fragmented manifests.

    HLS `tbr` comes from the manifest's peak BANDWIDTH and is not comparable with the average bitrate of the https
    formats, and fragment downloads are slower, so at the same size and fps the direct file wins.
    """
    proto = str(f.get("protocol") or "https")
    return 1 if proto in ("https", "http") else 0


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
    return (_area(f), f.get("fps") or 0, _direct(f), 0 if _has_audio(f) else 1, f.get("vbr") or f.get("tbr") or 0)


def _akey(f: dict, h264_compat: bool) -> tuple:
    """Original / default language track first, then non-DRC (YouTube's dynamic range compressed copies), then
    stereo / mono over surround (YouTube lists 5.1 AAC / AC-3 / E-AC-3 tracks once a JavaScript runtime solves the
    player challenge; a 6-channel track in an MP4 or downmixed into an MP3 is not what a stereo source deserves),
    then AAC when the H.264 pass may follow, then direct files, then bitrate and sample rate."""
    lang = f.get("language_preference")
    drc = "drc" in str(f.get("format_id") or "").lower() or "drc" in str(f.get("format_note") or "").lower()
    channels = f.get("audio_channels")
    stereo = 0 if isinstance(channels, (int, float)) and channels > 2 else 1
    aac = 1 if (h264_compat and (f.get("acodec") or "").startswith("mp4a")) else 0
    return (lang if isinstance(lang, (int, float)) else -1, 0 if drc else 1, stereo, aac, _direct(f),
            f.get("abr") or f.get("tbr") or 0, f.get("asr") or 0)


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


def select_formats(info: dict[str, Any], platform: str, quality: str, h264_compat: bool) -> FormatChoice:
    """Pick video + audio: highest resolution, then fps, then bitrate; AVC at the same size when h264_compat.

    `quality` is the value of the platform's own setting (`quality_instagram` / `quality_youtube`).
    """
    formats = info.get("formats") or []
    cap = quality_cap(platform, quality)
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
    # An unknown codec (X's progressive files, H.264 in practice) is not swapped for an HLS AVC rendition and
    # announces no H.264 pass: `_finalize` probes the downloaded file and converts only when it is not AVC.
    if h264_compat and best.get("vcodec") is not None and not _is_avc(best.get("vcodec")):
        same = [f for f in pool if _is_avc(f.get("vcodec"))
                and _area(f) >= _area(best) * 0.98 and (f.get("fps") or 0) >= (best.get("fps") or 0) - 1]
        if same:
            chosen = max(same, key=_vkey)
    audio = None
    if audios and not _has_audio(chosen):  # a muxed format (YouTube 18) keeps its own audio: no second track
        audio = max(audios, key=lambda f: _akey(f, h264_compat))
    vid = chosen["format_id"]
    spec = f"{vid}+{audio['format_id']}/{vid}" if audio else vid
    spec += "/bv*+ba/b"
    needs = bool(h264_compat and chosen.get("vcodec") is not None and not _is_avc(chosen.get("vcodec")))
    return FormatChoice(spec, [], chosen, audio, needs)


# =====================================================================================================
# Auth ladder
# =====================================================================================================

@dataclass(frozen=True)
class Rung:
    engine: str  # "yt-dlp" | "gallery-dl"
    auth: str    # "anonymous" | "session" | "browser"


def plan_ladder(auth_mode: str, has_session: bool, browser: str, platform: str = "instagram") -> list[Rung]:
    """Rungs to try in order. YouTube, TikTok and X: anonymous, then browser cookies when a browser is chosen
    (`auth_mode` and the Instagram session do not apply). Instagram: see docs/ENGINE.md section 5."""
    if platform != "instagram":
        return [Rung("yt-dlp", "anonymous")] + ([Rung("yt-dlp", "browser")] if browser else [])
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


def can_login_for(code: str, logged_in: bool, platform: str = "instagram") -> bool:
    """True when the UI should offer the in-app sign-in (Instagram only)."""
    if platform != "instagram":
        return False
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

VIDEO_EXTS = (".mp4", ".mkv", ".webm", ".mov", ".m4v")
ACTIVE = ("queued", "probing", "downloading", "processing")
RUNNING = ("probing", "downloading", "processing")
FINISHED = ("done", "error", "canceled")
HISTORY_LIMIT = 300
HISTORY_VERSION = 2
JOB_PLATFORMS = ("instagram", "youtube", "tiktok", "twitter")
JOB_FORMATS = ("mp4", "mp3")
JOB_KINDS = {"instagram": ("reel", "p"), "youtube": ("video", "shorts"), "tiktok": ("video", "share"),
             "twitter": ("post",)}

PUBLIC_FIELDS = ("id", "url", "shortcode", "platform", "kind", "format", "status", "stage_text", "progress",
                 "speed_bps", "eta_s", "previewed", "title", "author", "duration_s", "thumbnail", "width", "height",
                 "fps", "vcodec", "acodec", "filesize", "filepath", "already_existed", "transcoded", "attempt",
                 "error", "created_at", "finished_at")


def _kind_from_url(url: str, platform: str) -> str:
    if platform == "youtube":
        return "shorts" if "/shorts/" in url else "video"
    if platform == "tiktok":
        return "video" if "/video/" in url else "share"
    if platform == "twitter":
        return "post"
    return "p" if "/p/" in url else "reel"


@dataclass
class Job:
    id: str
    url: str
    shortcode: str
    platform: str = "instagram"
    kind: str = "reel"
    format: str = "mp4"
    status: str = "queued"
    stage_text: str = "Queued"
    progress: float = 0.0
    speed_bps: float | None = None
    eta_s: float | None = None
    previewed: bool = False
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
    preview_path: str | None = None   # cache/jobs/<id>/preview.json written by the preview worker
    preview_at: float | None = None   # time.time() of that fetch; the download reuses it for PREVIEW_MAX_AGE

    @property
    def platform_name(self) -> str:
        return PLATFORM_NAMES.get(self.platform, "the site")

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
        """Restore a history entry. Version 1 entries (Instagram only) have no platform / kind / format /
        previewed: they come back as instagram, kind from the URL, mp4, previewed."""
        try:
            job = cls(id=str(d["id"]), url=str(d["url"]), shortcode=str(d["shortcode"]))
        except (KeyError, TypeError):
            return None
        for k in PUBLIC_FIELDS:
            if k in d and k not in ("id", "url", "shortcode", "thumbnail"):
                setattr(job, k, d[k])
        if job.platform not in JOB_PLATFORMS:
            job.platform = "instagram"
        if "kind" not in d or job.kind not in JOB_KINDS[job.platform]:
            job.kind = _kind_from_url(job.url, job.platform)
        if job.format not in JOB_FORMATS:
            job.format = "mp4"
        job.thumb_path = d.get("thumb_path")
        if job.status not in FINISHED:
            job.status = "canceled"
            job.stage_text = "Interrupted when the app closed"
            job.speed_bps = job.eta_s = None
            job.finished_at = job.finished_at or time.time()
            job.error = {"code": "canceled", "message": "Download interrupted when the app closed.",
                         "can_login": False, "log_path": None}
        job.previewed = True  # restored jobs are finished: no preview fetch is pending for them
        return job

    def reset_for_retry(self) -> None:
        self.status, self.stage_text, self.progress = "queued", "Queued", 0.0
        self.speed_bps = self.eta_s = None
        self.error = None
        self.finished_at = None
        self.already_existed = self.transcoded = False
        self.filesize = None
        self.filepath = None
        self.width = self.height = self.fps = None
        self.vcodec = self.acodec = None
        self.preview_path = self.preview_at = None
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
    PREVIEW_TIMEOUT = 60.0
    PREVIEW_MAX_AGE = 600.0  # the download reuses the preview's info json while it is younger than this

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
        SUBPROCESS_ENV["DENO_DIR"] = os.path.join(paths.cache_dir, "deno")
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
        self._preview_id: str | None = None             # job whose preview fetch runs now
        self._preview_cancel = threading.Event()        # stops that fetch (job started, canceled, removed, shutdown)

    # --- lifecycle -----------------------------------------------------------------------------------
    def start(self) -> None:
        self._concurrency = int(self.get_settings().get("concurrency", 2))
        for i in range(self.MAX_WORKERS):
            t = threading.Thread(target=self._worker, name=f"ms-job-{i}", daemon=True)
            t.start()
            self._threads.append(t)
        t = threading.Thread(target=self._preview_worker, name="ms-preview", daemon=True)
        t.start()
        self._threads.append(t)

    def shutdown(self, timeout: float = 2.5) -> None:
        with self._cond:
            self._stopping = True
            running = [self._jobs[j] for j in self._running if j in self._jobs]
            for job in running:
                job.cancel_event.set()
            self._preview_cancel.set()
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

    def active_keys(self) -> set[str]:
        """"<platform>:<shortcode>" of every queued or running job (duplicate detection per platform)."""
        with self._lock:
            return {f"{j.platform}:{j.shortcode}" for j in self._jobs.values() if j.status in ACTIVE}

    def add(self, url: str, shortcode: str, platform: str, kind: str, fmt: str) -> dict:
        """Queue a cleaned link; the preview worker picks it up. Returns the Job dict."""
        if platform not in JOB_PLATFORMS:
            raise ValueError(f"Unknown platform: {platform!r}")
        if kind not in JOB_KINDS[platform]:
            raise ValueError(f"Unknown kind for {platform}: {kind!r}")
        if fmt not in JOB_FORMATS:
            raise ValueError(f"Unknown format: {fmt!r}")
        job = Job(id=uuid.uuid4().hex[:8], url=url, shortcode=shortcode, platform=platform, kind=kind, format=fmt)
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

    def set_format(self, job_id: str, fmt: str) -> dict | None:
        """Per-job format override, only while the job is queued. Returns the updated Job dict or None."""
        if fmt not in JOB_FORMATS:
            raise ValueError(f"Unknown format: {fmt!r}")
        with self._cond:
            job = self._jobs.get(job_id)
            if job is None or job.status != "queued":
                return None
            changed = job.format != fmt
            job.format = fmt
        if changed:
            self._emit_job(job, force=True)
            self._save_soon()
        with self._lock:
            return job.to_dict()

    def _stop_preview_of(self, job_id: str) -> None:
        """Caller holds the lock. Kills the preview subprocess when it works on this job."""
        if self._preview_id == job_id:
            self._preview_cancel.set()

    def _drop_job_cache(self, job: Job) -> None:
        """A queued job that ends without running may still own a preview.json in its cache folder."""
        if job.id in self._running:
            return
        shutil.rmtree(os.path.join(self.paths.jobs_cache_dir, job.id), ignore_errors=True)

    def cancel(self, job_id: str) -> bool:
        with self._cond:
            job = self._jobs.get(job_id)
            if job is None or job.status not in ACTIVE:
                return False
            job.cancel_event.set()
            self._stop_preview_of(job_id)
            was_queued = job.status == "queued"
            if was_queued:
                if job_id in self._queue:
                    self._queue.remove(job_id)
                self._finish_canceled(job)
                self._drop_job_cache(job)
        self._emit_job(job, force=True)
        self._save_soon()
        return True

    def retry(self, job_id: str) -> bool:
        with self._cond:
            job = self._jobs.get(job_id)
            if job is None or job.status not in ("error", "canceled"):
                return False
            if any(j.platform == job.platform and j.shortcode == job.shortcode and j.status in ACTIVE
                   for j in self._jobs.values()):
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
            self._stop_preview_of(job_id)
            if job_id in self._queue:
                self._queue.remove(job_id)
                self._drop_job_cache(job)
            self._order.remove(job_id)
        self._safe_emit("job_removed", {"id": job_id})
        self._save_soon()
        return True

    def clear_finished(self, platform: str | None = None) -> int:
        """Drop done / error / canceled jobs of `platform` (None = both) from the list; files stay."""
        with self._cond:
            ids = [j for j in self._order if self._jobs[j].status in FINISHED
                   and (platform is None or self._jobs[j].platform == platform)]
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
                atomic_write_json(self.paths.history_file, {"version": HISTORY_VERSION, "jobs": finished + active})
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
        job.previewed = True
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
                    # leaving "queued" here freezes the format and tells the preview worker to let go: the
                    # download probes by itself instead of waiting for an in-flight preview
                    job.status, job.stage_text = "probing", "Starting"
                    self._stop_preview_of(job.id)
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
        # the in-app session is an Instagram one; it never applies to YouTube jobs
        logged_in = job.platform == "instagram" and authmod.has_session(self.paths.cookies_file)
        ladder = plan_ladder(settings["auth_mode"], logged_in, settings.get("browser_cookies") or "", job.platform)
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
                        self._set(job, status="probing",
                                  stage_text=f"{job.platform_name} is limiting requests, waiting {int(delay)} s",
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
            final = (pick_final_error(failures) if failures
                     else Failure(ladder[0], "unknown", message_for("unknown", job.platform)))
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
            logged_in = job.platform == "instagram" and authmod.has_session(self.paths.cookies_file)
        log_path = self.paths.job_log(job.id)
        try:
            with open(log_path, "w", encoding="utf-8") as fh:
                fh.write(f"job {job.id} {job.url} ({job.platform}, {job.format})\n"
                         f"final error: {failure.code} ({failure.rung.engine}/{failure.rung.auth})\n\n")
                fh.write(log_text)
        except OSError:
            log_path = None
        message = failure.message
        if failure.code == "login_required" and logged_in and job.platform == "instagram":
            message = MESSAGES["session_rejected"]
        with self._lock:
            job.status = "error"
            job.stage_text = message
            job.speed_bps = job.eta_s = None
            job.previewed = True
            job.finished_at = time.time()
            job.attempt = {"engine": failure.rung.engine, "auth": failure.rung.auth}
            job.error = {"code": failure.code, "message": message,
                         "can_login": can_login_for(failure.code, logged_in, job.platform), "log_path": log_path}
        self._emit_job(job, force=True)

    # --- preview -------------------------------------------------------------------------------------
    def _next_preview(self) -> Job | None:
        """Oldest queued job without a preview; waits while the engine is not usable or being replaced."""
        with self._cond:
            while True:
                if self._stopping:
                    return None
                if self.usable() and not self.gate.replacing:
                    for jid in self._order:
                        job = self._jobs.get(jid)
                        if job is not None and job.status == "queued" and not job.previewed and not job.removed:
                            self._preview_id = job.id
                            self._preview_cancel = threading.Event()
                            return job
                self._cond.wait(0.5)

    def _preview_worker(self) -> None:
        while True:
            job = self._next_preview()
            if job is None:
                return
            try:
                self._run_preview(job, self._preview_cancel)
            except Exception:  # noqa: BLE001  the preview is cosmetic
                log.exception("preview of job %s crashed", job.id)
                self._preview_done(job, None)
            finally:
                with self._cond:
                    self._preview_id = None
                    self._cond.notify_all()

    def _preview_done(self, job: Job, reason: str | None) -> None:
        """Mark the preview as finished (found or not); removes a stray cache folder of a job that left the queue."""
        with self._lock:
            if job.removed:
                return
            job.previewed = True
            if job.status != "queued" and job.id not in self._running:
                shutil.rmtree(os.path.join(self.paths.jobs_cache_dir, job.id), ignore_errors=True)
        if reason:
            log.info("preview of job %s: %s", job.id, reason)
        self._emit_job(job, force=True)
        self._save_soon()

    def _run_preview(self, job: Job, cancel: threading.Event) -> None:
        """Anonymous `yt-dlp -J` of one queued job; on success stores preview.json and fills the preview fields.

        Never uses cookies. Any failure only sets `previewed`. Stops (no field change) when the job leaves the
        queue: a download that starts meanwhile probes by itself, cancel / remove / shutdown kill the subprocess.
        """
        started = time.monotonic()
        exe = self.binaries().get("yt-dlp")
        if not exe or not os.path.exists(exe):
            self._preview_done(job, "engine missing")
            return
        argv = self._ytdlp_base(Rung("yt-dlp", "anonymous"), {}, "", job.platform) + ["-J", job.url]
        if not self.gate.acquire_shared(cancel):
            return
        try:
            res = self.runner.run(argv, cwd=self.paths.jobs_cache_dir, cancel=cancel, timeout=self.PREVIEW_TIMEOUT)
        finally:
            self.gate.release_shared()
        elapsed = time.monotonic() - started
        if res.canceled or cancel.is_set():
            log.info("preview of job %s stopped after %.1f s (job left the queue)", job.id, elapsed)
            return
        if res.timed_out or res.returncode != 0 or not res.stdout.strip():
            code = "timeout" if res.timed_out else classify_error(res.stderr, job.platform)[0]
            self._preview_done(job, f"failed after {elapsed:.1f} s ({code})")
            return
        try:
            info = self._pick_entry(json.loads(res.stdout))
        except ValueError:
            info = None
        if info is None:
            self._preview_done(job, f"no usable info json after {elapsed:.1f} s")
            return
        meta = meta_from_info(info, job.platform)
        job_dir = self.paths.job_cache(job.id)
        path = os.path.join(job_dir, "preview.json")
        try:
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(info, fh, ensure_ascii=False)
        except OSError as exc:
            self._preview_done(job, f"cannot write preview.json: {exc}")
            return
        with self._lock:
            if job.status != "queued" or job.removed or cancel.is_set():
                if job.id not in self._running:
                    shutil.rmtree(job_dir, ignore_errors=True)
                return
            job.preview_path, job.preview_at = path, time.time()
            job.title, job.author, job.duration_s = meta["title"], meta["author"], meta["duration_s"]
        self._emit_job(job, force=True)
        if not job.thumbnail and job.status == "queued" and not cancel.is_set():
            self._make_thumbnail(job, info, job_dir, src_name="preview_thumb_src")
        self._preview_done(job, None)
        log.info("preview of job %s done in %.1f s", job.id, time.monotonic() - started)

    def _take_preview(self, job: Job, rung: Rung, logs: list[str]) -> dict | None:
        """The preview's info json when it is fresh and this is the anonymous rung. Used once: a retried rung
        (self-heal, rate-limit backoff) probes again."""
        with self._lock:
            path, at = job.preview_path, job.preview_at
            job.preview_path = job.preview_at = None
        if rung.auth != "anonymous" or not path or at is None:
            return None
        age = time.time() - at
        if age > self.PREVIEW_MAX_AGE:
            return None
        try:
            with open(path, "r", encoding="utf-8") as fh:
                info = json.load(fh)
        except (OSError, ValueError):
            return None
        if not isinstance(info, dict):
            return None
        logs.append(f"$ [probe] reused the preview info json ({age:.0f} s old)\n")
        return info

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
            raise JobFailed("network", message_for("network", job.platform), res.stderr)
        return res

    def _ytdlp_base(self, rung: Rung, settings: dict, job_dir: str, platform: str = "instagram") -> list[str]:
        argv = [self._bin("yt-dlp"), "--no-config", "--encoding", "utf-8", "--color", "never",
                "--socket-timeout", "20", "--retries", "3", "--extractor-retries", "2", "--no-mtime"]
        if platform == "youtube":
            argv.append("--no-playlist")  # a watch URL never pulls a playlist, even when one is attached
        deno = self.binaries().get("deno")
        if deno and os.path.exists(deno):
            argv += ["--js-runtimes", f"deno:{deno}"]  # explicit path: the bundled runtime is not on PATH
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
        base = self._ytdlp_base(rung, settings, job_dir, job.platform)
        info = self._take_preview(job, rung, logs)
        if info is None:
            res = self._run_tool(job, base + ["-J", job.url], logs, label="probe", timeout=self.PROBE_TIMEOUT)
            if rung.auth == "session":
                self._after_session_run(job_dir, res.stderr)
            if res.returncode != 0 or not res.stdout.strip():
                code, msg = classify_error(res.stderr, job.platform)
                raise JobFailed(code, msg, res.stderr)
            try:
                info = json.loads(res.stdout)
            except ValueError:
                raise JobFailed("extractor", message_for("extractor", job.platform), res.stderr) from None
            info = self._pick_entry(info)
            if info is None:
                raise JobFailed("not_found", message_for("no_video", job.platform), res.stderr)
        if info.get("is_live") or info.get("live_status") in ("is_live", "is_upcoming", "post_live"):
            raise JobFailed("not_found", message_for("live", job.platform))
        info_path = os.path.join(job_dir, "info.json")
        with open(info_path, "w", encoding="utf-8") as fh:
            json.dump(info, fh, ensure_ascii=False)
        meta = meta_from_info(info, job.platform)
        self._set(job, title=meta["title"], author=meta["author"], duration_s=meta["duration_s"], previewed=True)
        if not job.thumbnail:
            self._make_thumbnail(job, info, job_dir)
        self._check_cancel(job)

        target = self._target_path(job, settings, meta)
        if target is None:
            return  # skip_existing hit, job finished
        if job.format == "mp3":
            self._download_mp3(job, rung, base, info, info_path, target, settings, job_dir, logs)
        else:
            self._download_mp4(job, rung, base, info, info_path, target, settings, job_dir, logs)

    def _download_mp4(self, job: Job, rung: Rung, base: list[str], info: dict, info_path: str, target: str,
                      settings: dict, job_dir: str, logs: list[str]) -> None:
        choice = select_formats(info, job.platform, settings[QUALITY_SETTING.get(job.platform, "quality_instagram")],
                                settings["h264_compat"])
        model = ProgressModel(stage_weights(choice.has_audio or choice.video is None, choice.needs_transcode))
        self._set(job, status="downloading", stage_text="Downloading video", progress=model.set("probe", 1.0))

        out_tmpl = os.path.join(job_dir, "media.%(ext)s")
        argv = base + ["--load-info-json", info_path, "-f", choice.spec, *choice.sort_args,
                       "--merge-output-format", "mp4", "--ffmpeg-location", self._bin("ffmpeg"),
                       "-o", out_tmpl, "--no-part", "--force-overwrites", *progress_args(),
                       "--print", "after_move:" + FILE_MARK + "%(filepath)j", "--no-simulate"]
        state = {"file": None}
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
        media = (state["file"] if state["file"] and os.path.exists(state["file"])
                 else self._find_media(job_dir, VIDEO_EXTS))
        if res.returncode != 0 or not media:
            code, msg = classify_error(res.stderr, job.platform)
            if code == "unknown" and not media:
                code, msg = "extractor", message_for("extractor", job.platform)
            raise JobFailed(code, msg, res.stderr)
        self._finalize(job, media, target, settings, model, logs)

    @staticmethod
    def _audio_missing(info: dict) -> bool:
        """True only when every format says it has no audio (unknown acodec counts as maybe-audio)."""
        formats = [f for f in info.get("formats") or [] if _usable_format(f)]
        return bool(formats) and all(f.get("acodec") == "none" for f in formats)

    def _download_mp3(self, job: Job, rung: Rung, base: list[str], info: dict, info_path: str, target: str,
                      settings: dict, job_dir: str, logs: list[str]) -> None:
        """Best audio -> MP3 (VBR quality 0) by yt-dlp's ExtractAudio postprocessor; no merge, no H.264 pass."""
        if self._audio_missing(info):
            raise JobFailed("not_found", message_for("no_audio", job.platform))
        ffmpeg = self._bin("ffmpeg")  # ffmpeg_missing before any subprocess starts
        model = ProgressModel(mp3_stage_weights())
        self._set(job, status="downloading", stage_text="Downloading audio", progress=model.set("probe", 1.0))
        out_tmpl = os.path.join(job_dir, "media.%(ext)s")
        # `-S channels:2`: stereo / mono before the 5.1 tracks YouTube lists with a JavaScript runtime (yt-dlp's
        # default order ranks more channels higher and would downmix a 6-channel AAC / AC-3 source into the MP3).
        argv = base + ["--load-info-json", info_path, "-f", "ba/b", "-S", "channels:2", "-x", "--audio-format", "mp3",
                       "--audio-quality", "0", "--ffmpeg-location", ffmpeg,
                       "-o", out_tmpl, "--no-part", "--force-overwrites", *progress_args(),
                       "--print", "after_move:" + FILE_MARK + "%(filepath)j", "--no-simulate"]
        state = {"file": None}

        def on_line(line: str) -> None:
            parsed = parse_marker_line(line)
            if parsed is None:
                return
            kind, data = parsed
            if kind == "file":
                state["file"] = data
            elif kind == "download":
                total = data.get("t") or data.get("te")
                done = data.get("d") or 0
                frac = 1.0 if data.get("s") == "finished" else ((done / total) if total else 0.0)
                force = job.stage_text != "Downloading audio"
                self._set(job, force=force, status="downloading", stage_text="Downloading audio",
                          progress=model.set("audio", frac), speed_bps=data.get("sp"), eta_s=data.get("eta"))
            elif kind == "postprocess" and data.get("pp") == "ExtractAudio":
                frac = 1.0 if data.get("s") == "finished" else 0.1
                self._set(job, status="processing", stage_text="Converting to MP3",
                          progress=model.set("convert", frac), speed_bps=None, eta_s=None)

        res = self._run_tool(job, argv, logs, label="download mp3", timeout=self.DOWNLOAD_TIMEOUT,
                             on_stdout=on_line, on_stderr=on_line)
        if rung.auth == "session":
            self._after_session_run(job_dir, res.stderr)
        media = (state["file"] if state["file"] and os.path.exists(state["file"])
                 else self._find_media(job_dir, (".mp3",)))
        if res.returncode != 0 or not media or not media.lower().endswith(".mp3"):
            if "unable to obtain file audio codec" in res.stderr:
                raise JobFailed("not_found", message_for("no_audio", job.platform), res.stderr)
            code, msg = classify_error(res.stderr, job.platform)
            if code == "unknown" and not media:
                code, msg = "extractor", message_for("extractor", job.platform)
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
    def _find_media(job_dir: str, exts: tuple[str, ...]) -> str | None:
        best = None
        for name in os.listdir(job_dir):
            if name.startswith("media") and name.lower().endswith(exts):
                path = os.path.join(job_dir, name)
                if best is None or os.path.getsize(path) > os.path.getsize(best):
                    best = path
        return best

    def _target_path(self, job: Job, settings: dict, meta: dict) -> str | None:
        """Final output path (".mp4" / ".mp3" by the job format), or None when skip_existing found that exact
        file (the job is then finished as done)."""
        out_dir = settings["output_dir"]
        try:
            os.makedirs(out_dir, exist_ok=True)
        except OSError as exc:
            raise JobFailed("disk", MESSAGES["disk"], str(exc)) from None
        name = render_filename(settings["filename_template"], job.shortcode, meta["author"], meta["title"],
                               meta["date"], fallback_author=job.platform)
        path = os.path.join(out_dir, name + "." + job.format)
        if settings["skip_existing"] and os.path.isfile(path) and os.path.getsize(path) > 0:
            fields = self._media_fields(job, self._probe_media(path) or {})
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

    @staticmethod
    def _media_fields(job: Job, fields: dict) -> dict:
        """mp3 files report no video fields (an embedded cover would otherwise look like a video stream)."""
        if job.format == "mp3":
            fields = dict(fields, width=None, height=None, fps=None, vcodec=None)
            fields["acodec"] = fields.get("acodec") or "mp3"
        return fields

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
        if (job.format == "mp4" and settings["h264_compat"] and fields.get("vcodec")
                and fields["vcodec"] != "h264"):
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
        fields = self._media_fields(job, fields)
        with self._lock:
            job.status, job.stage_text, job.progress = "done", "Done", 1.0
            job.filepath = final
            job.transcoded = transcoded
            job.previewed = True
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
            code, msg = classify_error(res.stderr, job.platform)
            if code in ("unknown", "extractor"):
                code, msg = "unknown", "Could not convert the video to H.264. See the log for details."
            raise JobFailed(code, msg, res.stderr)
        try:
            os.remove(src)
        except OSError:
            pass
        return dst

    def _make_thumbnail(self, job: Job, info: dict, job_dir: str, src_name: str = "thumb_src") -> None:
        """Fetch the poster (Instagram jpg, YouTube webp / jpg, TikTok and X jpg), scale to <= 480 px wide as JPEG,
        keep it in
        cache/thumbs and send it as a data URL. Without ffmpeg a raw poster under 400 KB is kept as is."""
        url = info.get("thumbnail")
        if not url:
            thumbs = [t for t in info.get("thumbnails") or [] if t.get("url")]
            url = thumbs[-1]["url"] if thumbs else None
        if not url:
            return
        key = sanitize_filename(job.shortcode, 60)
        dest = self.paths.thumb_file(key if job.platform == "instagram" else f"{job.platform}_{key}")
        try:
            headers = info.get("http_headers") or {}
            raw = self.fetch(url, headers)
            src = os.path.join(job_dir, src_name)
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
        mp3 = job.format == "mp3"
        if mp3:
            self._bin("ffmpeg")  # ffmpeg_missing before the download starts
        model = ProgressModel(mp3_stage_weights() if mp3 else stage_weights(False, settings["h264_compat"]))
        self._set(job, status="downloading", stage_text="Downloading video (fallback engine)",
                  progress=model.set("probe", 1.0))
        out_dir = os.path.join(job_dir, "gdl")
        files: list[str] = []

        def on_out(line: str) -> None:
            path = line.strip().lstrip("# ").strip()
            if path.lower().endswith(".mp4") and os.path.isabs(path):
                files.append(path)
                self._set(job, progress=model.set("audio" if mp3 else "video", 1.0))

        res = self._run_tool(job, base + ["-D", out_dir, "-f", "{shortcode}_{num}.{extension}", "--no-mtime",
                                          "--filter", "extension == 'mp4'", job.url], logs,
                             label="gallery-dl download", timeout=self.DOWNLOAD_TIMEOUT, on_stdout=on_out)
        if not files and os.path.isdir(out_dir):
            files = sorted(os.path.join(out_dir, n) for n in os.listdir(out_dir) if n.lower().endswith(".mp4"))
        files = [f for f in files if os.path.exists(f)]
        if not files:
            code, msg = classify_error(res.stderr)
            raise JobFailed(code, msg, res.stderr)
        media = sorted(files)[0]
        if mp3:
            media = self._extract_mp3(job, media, model, logs)
        self._finalize(job, media, target, settings, model, logs)

    def _extract_mp3(self, job: Job, src: str, model: ProgressModel, logs: list[str]) -> str:
        """MP3 (libmp3lame VBR quality 0, as yt-dlp's `--audio-quality 0`) from a downloaded video file."""
        ffmpeg = self._bin("ffmpeg")
        dst = os.path.join(os.path.dirname(src), "media.mp3")
        argv = [ffmpeg, "-hide_banner", "-nostdin", "-y", "-loglevel", "error", "-i", src, "-vn",
                "-map", "0:a:0", "-c:a", "libmp3lame", "-q:a", "0", dst]
        self._set(job, status="processing", stage_text="Converting to MP3", progress=model.set("convert", 0.1),
                  speed_bps=None, eta_s=None)
        res = self._run_tool(job, argv, logs, label="mp3", timeout=self.DOWNLOAD_TIMEOUT, gated=False)
        if res.returncode != 0 or not os.path.exists(dst):
            if re.search(r"matches no streams|does not contain any stream", res.stderr, re.I):
                raise JobFailed("not_found", message_for("no_audio", job.platform), res.stderr)
            code, msg = classify_error(res.stderr, job.platform)
            if code in ("unknown", "extractor"):
                code, msg = "unknown", "Could not convert the audio to MP3. See the log for details."
            raise JobFailed(code, msg, res.stderr)
        self._set(job, progress=model.set("convert", 1.0))
        return dst

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
