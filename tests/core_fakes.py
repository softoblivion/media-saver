"""Shared fakes for the core pipeline tests: a scripted ProcessRunner and info json samples (no network).

Import from a test module after putting this folder on sys.path (see the top of test_core_formats.py).
"""
import json
import os
import shutil
import tempfile
import threading
import time

from app.core import settings as S
from app.core.downloader import Downloader, ProcessResult
from app.core.paths import AppPaths

# Trimmed real `yt-dlp -J` of https://www.youtube.com/watch?v=aqz-KE-bpKQ (anonymous, 2026-10-20, no JS runtime).
YT_INFO = {
    "id": "aqz-KE-bpKQ", "_type": "video", "extractor": "youtube", "extractor_key": "Youtube",
    "webpage_url": "https://www.youtube.com/watch?v=aqz-KE-bpKQ",
    "title": "Big Buck Bunny 60fps 4K - Official Blender Foundation Short Film", "channel": "Blender",
    "uploader": "Blender", "uploader_id": "@BlenderOfficial", "description": "Long text\nwith lines",
    "upload_date": "20141110", "duration": 635, "live_status": "not_live",
    "thumbnail": "https://i.ytimg.com/vi_webp/aqz-KE-bpKQ/maxresdefault.webp",
    "formats": [
        {"format_id": "sb0", "ext": "mhtml", "vcodec": "none", "acodec": "none", "width": 320, "height": 180,
         "format_note": "storyboard", "protocol": "mhtml"},
        {"format_id": "233", "ext": "mp4", "vcodec": "none", "acodec": None, "protocol": "m3u8_native"},
        {"format_id": "140-drc", "ext": "m4a", "vcodec": "none", "acodec": "mp4a.40.2", "abr": 129.481,
         "format_note": "medium, DRC", "protocol": "https", "language_preference": -1},
        {"format_id": "251-drc", "ext": "webm", "vcodec": "none", "acodec": "opus", "abr": 129.327,
         "format_note": "medium, DRC", "protocol": "https", "language_preference": -1},
        {"format_id": "140", "ext": "m4a", "vcodec": "none", "acodec": "mp4a.40.2", "abr": 129.481,
         "format_note": "medium", "protocol": "https", "language_preference": -1},
        {"format_id": "251", "ext": "webm", "vcodec": "none", "acodec": "opus", "abr": 128.612,
         "format_note": "medium", "protocol": "https", "language_preference": -1},
        {"format_id": "18", "ext": "mp4", "vcodec": "avc1.42001E", "acodec": "mp4a.40.2", "width": 640,
         "height": 360, "fps": 30, "tbr": 600, "protocol": "https"},
        {"format_id": "298", "ext": "mp4", "vcodec": "avc1.4D4020", "acodec": "none", "width": 1280, "height": 720,
         "fps": 60, "tbr": 1897.673, "protocol": "https"},
        {"format_id": "302", "ext": "webm", "vcodec": "vp9", "acodec": "none", "width": 1280, "height": 720,
         "fps": 60, "tbr": 1420.515, "protocol": "https"},
        {"format_id": "312", "ext": "mp4", "vcodec": "avc1.64002A", "acodec": "none", "width": 1920, "height": 1080,
         "fps": 60.0, "tbr": 7417.746, "protocol": "m3u8_native"},
        {"format_id": "299", "ext": "mp4", "vcodec": "avc1.64002A", "acodec": "none", "width": 1920, "height": 1080,
         "fps": 60, "tbr": 3247.821, "protocol": "https"},
        {"format_id": "303", "ext": "webm", "vcodec": "vp9", "acodec": "none", "width": 1920, "height": 1080,
         "fps": 60, "tbr": 2127.264, "protocol": "https"},
        {"format_id": "399", "ext": "mp4", "vcodec": "av01.0.09M.08", "acodec": "none", "width": 1920,
         "height": 1080, "fps": 60, "tbr": 1568.15, "protocol": "https"},
        {"format_id": "308", "ext": "webm", "vcodec": "vp9", "acodec": "none", "width": 2560, "height": 1440,
         "fps": 60, "tbr": 5967.715, "protocol": "https"},
        {"format_id": "400", "ext": "mp4", "vcodec": "av01.0.12M.08", "acodec": "none", "width": 2560,
         "height": 1440, "fps": 60, "tbr": 3940.385, "protocol": "https"},
        {"format_id": "628", "ext": "mp4", "vcodec": "vp09.00.51.08", "acodec": "none", "width": 3840,
         "height": 2160, "fps": 60.0, "tbr": 27987.109, "protocol": "m3u8_native"},
        {"format_id": "315", "ext": "webm", "vcodec": "vp9", "acodec": "none", "width": 3840, "height": 2160,
         "fps": 60, "tbr": 17174.188, "protocol": "https"},
        {"format_id": "401", "ext": "mp4", "vcodec": "av01.0.13M.08", "acodec": "none", "width": 3840,
         "height": 2160, "fps": 60, "tbr": 8981.827, "protocol": "https"},
    ],
}

IG_INFO = {
    "id": "DQCkNLtgqEe", "_type": "video", "extractor": "Instagram", "extractor_key": "Instagram",
    "webpage_url": "https://www.instagram.com/reel/DQCkNLtgqEe",
    "channel": "teddysphotos", "uploader": "Ed Sheeran", "uploader_id": "185546187", "title": "Video by teddysphotos",
    "description": "Caption line", "upload_date": "20251020", "duration": 26.2,
    "thumbnail": "https://cdn.example/thumb.jpg",
    "formats": [
        {"format_id": "dash-a", "vcodec": "none", "acodec": "mp4a.40.5", "abr": 72.9},
        {"format_id": "1", "vcodec": None, "acodec": None},
        {"format_id": "dash-720", "vcodec": "vp09.00.31.08", "acodec": "none", "width": 720, "height": 1280, "tbr": 1463},
        {"format_id": "dash-1080", "vcodec": "vp09.00.40.08", "acodec": "none", "width": 1080, "height": 1920, "tbr": 1938},
    ],
}

TT_INFO = {
    "id": "7106594312292453675", "_type": "video", "extractor": "TikTok", "extractor_key": "TikTok",
    "webpage_url": "https://www.tiktok.com/@tiktok/video/7106594312292453675",
    "title": "how many frogs did you find? check out tiktok's #Minecraft communit...",
    "description": "how many frogs did you find? check out tiktok's #Minecraft community today! @Gorillo",
    "uploader": "tiktok", "uploader_id": "107955", "channel": "TikTok", "upload_date": "20220607",
    "timestamp": 1654632929, "duration": 24, "thumbnail": "https://p16.tiktokcdn.example/thumb.jpeg",
    "http_headers": {"Referer": "https://www.tiktok.com/@tiktok/video/7106594312292453675"},
    "formats": [
        {"format_id": "audio", "ext": "mp4", "protocol": "https", "vcodec": "none", "acodec": "mp4", "vbr": 0},
        {"format_id": "download", "ext": "mp4", "protocol": "https", "vcodec": "h264", "acodec": "aac",
         "format_note": "watermarked", "preference": -2},
        {"format_id": "h264_540p_377700-0", "ext": "mp4", "protocol": "https", "vcodec": "h264", "acodec": "aac",
         "width": 576, "height": 1024, "tbr": 377, "filesize": 1159730, "quality": 1, "preference": -1},
        {"format_id": "h264_540p_1071120-0", "ext": "mp4", "protocol": "https", "vcodec": "h264", "acodec": "aac",
         "width": 576, "height": 1024, "tbr": 1071, "filesize": 3288875, "quality": 1, "preference": -1},
        {"format_id": "h264_540p_1071120-1", "ext": "mp4", "protocol": "https", "vcodec": "h264", "acodec": "aac",
         "width": 576, "height": 1024, "tbr": 1071, "filesize": 3288875, "quality": 1, "preference": -1},
        {"format_id": "bytevc1_540p_555965-0", "ext": "mp4", "protocol": "https", "vcodec": "h265", "acodec": "aac",
         "width": 576, "height": 1024, "tbr": 555, "filesize": 1707091, "quality": 1, "preference": -1},
        {"format_id": "bytevc1_720p_640712-0", "ext": "mp4", "protocol": "https", "vcodec": "h265", "acodec": "aac",
         "width": 720, "height": 1280, "tbr": 640, "filesize": 1967308, "quality": 2, "preference": -1},
        {"format_id": "bytevc1_1080p_1094429-0", "ext": "mp4", "protocol": "https", "vcodec": "h265", "acodec": "aac",
         "width": 1080, "height": 1920, "tbr": 1094, "filesize": 3360445, "quality": 3, "preference": -1},
        {"format_id": "bytevc1_1080p_1094429-1", "ext": "mp4", "protocol": "https", "vcodec": "h265", "acodec": "aac",
         "width": 1080, "height": 1920, "tbr": 1094, "filesize": 3360445, "quality": 3, "preference": -1},
    ],
}

X_INFO = {
    "id": "1577855447914409984", "_type": "video", "extractor": "twitter", "extractor_key": "Twitter",
    "webpage_url": "https://twitter.com/i/web/status/1577855540407197696",
    "title": "Oshtru - gm now I can post image and video. nice update.",
    "description": "gm now I can post image and video. nice update. https://t.co/cG7XgiINOm",
    "uploader": "Oshtru", "uploader_id": "oshtru", "channel": None, "channel_id": "143077138",
    "upload_date": "20221006", "timestamp": 1665025050, "duration": 30.03,
    "thumbnail": "https://pbs.twimg.example/ext_tw_video_thumb/1577855447914409984/pu/img/x.jpg?name=orig",
    "formats": [
        {"format_id": "hls-audio-32000-Audio", "ext": "mp4", "protocol": "m3u8_native", "vcodec": "none",
         "acodec": None, "tbr": 32, "abr": 32, "format_note": "Audio, low"},
        {"format_id": "hls-audio-128000-Audio", "ext": "mp4", "protocol": "m3u8_native", "vcodec": "none",
         "acodec": None, "tbr": 128, "abr": 128, "format_note": "Audio, high"},
        {"format_id": "http-632", "ext": "mp4", "protocol": "https", "vcodec": None, "acodec": None,
         "width": 320, "height": 400, "tbr": 632},
        {"format_id": "hls-138", "ext": "mp4", "protocol": "m3u8_native", "vcodec": "avc1.4D4015", "acodec": "none",
         "width": 320, "height": 400, "tbr": 138.267},
        {"format_id": "http-950", "ext": "mp4", "protocol": "https", "vcodec": None, "acodec": None,
         "width": 480, "height": 600, "tbr": 950},
        {"format_id": "hls-253", "ext": "mp4", "protocol": "m3u8_native", "vcodec": "avc1.4D401F", "acodec": "none",
         "width": 480, "height": 600, "tbr": 253.995},
        {"format_id": "http-2176", "ext": "mp4", "protocol": "https", "vcodec": None, "acodec": None,
         "width": 720, "height": 900, "tbr": 2176},
        {"format_id": "hls-473", "ext": "mp4", "protocol": "m3u8_native", "vcodec": "avc1.640028", "acodec": "none",
         "width": 720, "height": 900, "tbr": 473.664},
    ],
}

YT_BOT = ("ERROR: [youtube] aqz-KE-bpKQ: Sign in to confirm you’re not a bot. Use --cookies-from-browser or "
          "--cookies for the authentication. See  https://github.com/yt-dlp/yt-dlp/wiki/FAQ#how-do-i-pass-cookies-to-"
          "yt-dlp  for how to manually pass cookies. Also see  https://github.com/yt-dlp/yt-dlp/wiki/Extractors"
          "#exporting-youtube-cookies  for tips on effectively exporting YouTube cookies")
YT_RATE = "ERROR: [youtube] aqz-KE-bpKQ: Unable to download API page: HTTP Error 429: Too Many Requests"


class ScriptedRunner:
    """Emulates yt-dlp / ffmpeg / ffprobe. `probes` is a list of outcomes for the `-J` calls, in order:
    ("ok", info) | ("fail", stderr) | ("block", None) (waits for cancel) | ("slow", seconds) |
    ("wait", threading.Event) (returns the info once the event is set, or canceled when cancel comes first)."""

    def __init__(self, info, probes=(), transcode=False, mp3_codec="mp3"):
        self.info = info
        self.probes = list(probes)
        self.transcode = transcode
        self.mp3_codec = mp3_codec
        self.calls = []
        self.lock = threading.Lock()
        self.probe_started = threading.Event()

    def ytdlp_calls(self, flag):
        with self.lock:
            return [c for c in self.calls if os.path.basename(c[0]).startswith("yt-dlp") and flag in c]

    def run(self, argv, *, cwd=None, on_stdout=None, on_stderr=None, capture_stdout=True, cancel=None, timeout=None):
        with self.lock:
            self.calls.append(list(argv))
        exe = os.path.basename(argv[0])
        if exe.startswith("ffprobe"):
            path = argv[-1]
            if path.lower().endswith(".mp3"):
                data = {"streams": [{"codec_type": "audio", "codec_name": self.mp3_codec}],
                        "format": {"duration": "635.0"}}
            else:
                in_cache = os.path.join("cache", "jobs") in path
                vcodec = ("vp9" if (in_cache and self.transcode and not os.path.basename(path).startswith("h264"))
                          else "h264")
                data = {"streams": [{"codec_type": "video", "codec_name": vcodec, "width": 1920, "height": 1080,
                                     "avg_frame_rate": "60/1"}, {"codec_type": "audio", "codec_name": "aac"}],
                        "format": {"duration": "635.0"}}
            return ProcessResult(0, json.dumps(data), "")
        if exe.startswith("ffmpeg"):
            with open(argv[-1], "wb") as fh:
                fh.write(b"OUT" * 10)
            if on_stdout and "-progress" in argv:
                for us in (100_000_000, 635_000_000):
                    on_stdout(f"out_time_us={us}")
            return ProcessResult(0, "", "")
        if "-J" in argv:
            with self.lock:
                kind, value = self.probes.pop(0) if self.probes else ("ok", self.info)
            self.probe_started.set()
            if kind == "block":
                while not cancel.is_set():
                    time.sleep(0.02)
                return ProcessResult(None, "", "", canceled=True)
            if kind == "slow":
                time.sleep(value)
                return ProcessResult(0, json.dumps(self.info), "")
            if kind == "wait":
                while not value.is_set():
                    if cancel is not None and cancel.is_set():
                        return ProcessResult(None, "", "", canceled=True)
                    time.sleep(0.02)
                return ProcessResult(0, json.dumps(self.info), "")
            if kind == "fail":
                return ProcessResult(1, "", value)
            return ProcessResult(0, json.dumps(value or self.info), "")
        if "--load-info-json" in argv:
            out = argv[argv.index("-o") + 1]
            if "-x" in argv:
                on_stdout('[[IDLP]]{"s":"downloading","d":10,"t":100,"te":NA,"sp":500,"eta":2,"f":"251","vc":"none"}')
                on_stdout('[[IDLP]]{"s":"finished","d":100,"t":100,"te":NA,"sp":500,"eta":NA,"f":"251","vc":"none"}')
                on_stderr('[[IDLPP]]{"s":"started","pp":"ExtractAudio"}')
                path = out.replace("%(ext)s", "mp3")
                with open(path, "wb") as fh:
                    fh.write(b"ID3" * 50)
                on_stderr('[[IDLPP]]{"s":"finished","pp":"ExtractAudio"}')
            else:
                for line in ('[[IDLP]]{"s":"downloading","d":100,"t":1000,"te":NA,"sp":5000,"eta":1,"f":"299","vc":"avc1"}',
                             '[[IDLP]]{"s":"finished","d":1000,"t":1000,"te":NA,"sp":5000,"eta":NA,"f":"299","vc":"avc1"}',
                             '[[IDLP]]{"s":"finished","d":50,"t":50,"te":NA,"sp":NA,"eta":NA,"f":"140","vc":"none"}'):
                    on_stdout(line)
                on_stderr('[[IDLPP]]{"s":"started","pp":"Merger"}')
                path = out.replace("%(ext)s", "mp4")
                with open(path, "wb") as fh:
                    fh.write(b"MP4" * 100)
                on_stderr('[[IDLPP]]{"s":"finished","pp":"Merger"}')
            on_stdout('[[IDLFILE]]' + json.dumps(path))
            return ProcessResult(0, "", "")
        return ProcessResult(1, "", "unexpected call")


def remove_tree(path, attempts=10):
    """rmtree that survives the Downloader's background history save re-creating `home/history.json` right
    after the first removal."""
    for _ in range(attempts):
        shutil.rmtree(path, ignore_errors=True)
        time.sleep(0.1)
        if not os.path.exists(path):
            return


class CoreTestBase:
    """Mixin for unittest.TestCase: scratch home, fake binaries, a Downloader factory."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ms_core_")
        os.environ["MEDIA_SAVER_HOME"] = os.path.join(self.tmp, "home")
        self.paths = AppPaths()
        self.out = os.path.join(self.tmp, "out")
        self.settings = S.apply_patch(S.defaults(), {"output_dir": self.out})
        for name in ("yt-dlp.exe", "gallery-dl.exe", "ffmpeg.exe", "ffprobe.exe"):
            open(self.paths.binary(name), "wb").close()
        self.bins = {"yt-dlp": self.paths.binary("yt-dlp.exe"), "gallery-dl": self.paths.binary("gallery-dl.exe"),
                     "ffmpeg": self.paths.binary("ffmpeg.exe"), "ffprobe": self.paths.binary("ffprobe.exe")}
        self.events = []
        self.sleeps = []
        self.usable = True

    def tearDown(self):
        os.environ.pop("MEDIA_SAVER_HOME", None)
        remove_tree(self.tmp)

    def make(self, runner, **patch):
        self.settings.update(patch)

        def sleep(ev, s):
            self.sleeps.append(s)
            return ev.is_set()

        return Downloader(self.paths, lambda: dict(self.settings), lambda t, p: self.events.append((t, p)),
                          binaries=lambda: dict(self.bins), usable=lambda: self.usable, self_heal=lambda: False,
                          runner=runner, fetch=lambda url, headers=None: b"\xff\xd8jpeg", sleep=sleep)

    def job_events(self, job_id=None):
        return [p for t, p in self.events if t == "job" and (job_id is None or p["id"] == job_id)]
