"""YouTube jobs: error classification, auth ladder, can_login, platform-specific texts (no network)."""
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core_fakes import YT_BOT, YT_INFO, YT_RATE, CoreTestBase, ScriptedRunner  # noqa: E402

from app.core import auth, downloader  # noqa: E402
from app.core.downloader import (MESSAGES, YOUTUBE_MESSAGES, Rung, can_login_for, classify_error,  # noqa: E402
                                 message_for, plan_ladder)

YT_URL = "https://www.youtube.com/watch?v=aqz-KE-bpKQ"

# Message texts of yt-dlp's YouTube extractor (the ones marked "seen" were observed on 2026-10-20, docs/ENGINE.md 8).
SAMPLES = [
    (YT_BOT, "login_required"),
    ("ERROR: [youtube] XXXXXXXXXXX: Sign in to confirm your age. This video may be inappropriate for some users. "
     "Use --cookies-from-browser or --cookies for the authentication.", "login_required"),
    ("ERROR: [youtube] XXXXXXXXXXX: This video is age-restricted and only available on YouTube.", "login_required"),
    ("ERROR: [youtube] XXXXXXXXXXX: Private video. Sign in if you've been granted access to this video. Use "
     "--cookies-from-browser or --cookies for the authentication.", "private"),
    ("ERROR: [youtube] XXXXXXXXXXX: Join this channel to get access to members-only content like this video, and "
     "other exclusive perks.", "private"),
    ("ERROR: [youtube] XXXXXXXXXXX: This video is available to this channel's members on level: Fan (or any higher "
     "level). Join this channel to get access to members-only content and other exclusive perks.", "private"),
    ("ERROR: [youtube] xxxxxxxxxxx: This video is unavailable", "not_found"),  # seen: non-existent id
    ("WARNING: [youtube] No supported JavaScript runtime could be found. Only deno is enabled by default; to use "
     "another runtime add  --js-runtimes RUNTIME[:PATH]  to your command/config. YouTube extraction without a JS "
     "runtime has been deprecated, and some formats may be missing.\n"
     "ERROR: [youtube] jfKfPfyJRdk: This live stream recording is not available.", "not_found"),  # seen
    ("ERROR: [youtube] XXXXXXXXXXX: Video unavailable", "not_found"),
    ("ERROR: [youtube] XXXXXXXXXXX: Video unavailable. This video has been removed by the uploader", "not_found"),
    ("ERROR: [youtube] XXXXXXXXXXX: Video unavailable. This video is no longer available because the YouTube "
     "account associated with this video has been terminated.", "not_found"),
    ("ERROR: [youtube] XXXXXXXXXXX: This video is not available", "not_found"),
    ("ERROR: [youtube] XXXXXXXXXXX: Video unavailable. The uploader has not made this video available in your "
     "country", "not_found"),
    ("ERROR: [youtube] XXXXXXXXXXX: This live event will begin in 3 hours.", "not_found"),
    ("ERROR: [youtube] XXXXXXXXXXX: Premieres in 2 hours", "not_found"),
    ("ERROR: [youtube] XXXXXXXXXXX: This content isn't available, try again later. The current session has been "
     "rate-limited by YouTube for up to an hour.", "rate_limited"),
    (YT_RATE, "rate_limited"),
    ("ERROR: [youtube] XXXXXXXXXXX: Requested format is not available. Use --list-formats for a list of available "
     "formats", "extractor"),
    ("ERROR: [youtube] XXXXXXXXXXX: Unable to download API page: [Errno 11001] getaddrinfo failed (caused by "
     "TransportError('[Errno 11001] getaddrinfo failed'))", "network"),
    ("ERROR: Postprocessing: ffprobe and ffmpeg not found. Please install or provide the path using "
     "--ffmpeg-location", "ffmpeg_missing"),
    ("ERROR: unable to write data: [Errno 28] No space left on device", "disk"),
]


class ClassifyYouTubeTest(unittest.TestCase):
    def test_samples(self):
        for text, code in SAMPLES:
            with self.subTest(text=text[:70]):
                got, msg = classify_error(text, "youtube")
                self.assertEqual(got, code)
                self.assertNotIn("Instagram", msg)

    def test_live_message(self):
        _, msg = classify_error("ERROR: [youtube] X: This live event will begin in 3 hours.", "youtube")
        self.assertIn("Live streams and premieres", msg)

    def test_messages_never_name_instagram(self):
        for key in set(MESSAGES) - {"session_invalid", "session_rejected"}:
            with self.subTest(key=key):
                self.assertNotIn("Instagram", message_for(key, "youtube"))
        for key in YOUTUBE_MESSAGES:
            self.assertNotIn("Instagram", YOUTUBE_MESSAGES[key])
        self.assertIn("Browser cookies", message_for("login_required", "youtube"))
        self.assertIn("Instagram", message_for("login_required", "instagram"))

    def test_instagram_unchanged_by_default(self):
        code, msg = classify_error("ERROR: [Instagram] X: HTTP Error 429: Too Many Requests")
        self.assertEqual(code, "rate_limited")
        self.assertIn("Instagram", msg)


class YouTubeLadderTest(unittest.TestCase):
    def test_plan(self):
        anon, brow = Rung("yt-dlp", "anonymous"), Rung("yt-dlp", "browser")
        for mode in ("auto", "always", "never"):
            self.assertEqual(plan_ladder(mode, True, "", "youtube"), [anon])
            self.assertEqual(plan_ladder(mode, True, "edge", "youtube"), [anon, brow])

    def test_can_login_never_for_youtube(self):
        for code in ("login_required", "private", "rate_limited"):
            self.assertFalse(can_login_for(code, False, "youtube"))
        self.assertTrue(can_login_for("login_required", False, "instagram"))


class YouTubePipelineTest(CoreTestBase, unittest.TestCase):
    def run_one(self, dl):
        job = dl.get(dl.add(YT_URL, "aqz-KE-bpKQ", "youtube", "video", "mp4")["id"])
        dl._run_job(job)
        return job

    def write_session(self):
        auth.write_netscape_cookies([{"name": "sessionid", "value": "S", "domain": ".instagram.com", "path": "/",
                                      "secure": True, "http_only": True, "expires": time.time() + 9999}],
                                    self.paths.cookies_file)

    def test_bot_check_without_browser_stops(self):
        self.write_session()  # an Instagram session must not matter for YouTube
        runner = ScriptedRunner(YT_INFO, probes=[("fail", YT_BOT)])
        job = self.run_one(self.make(runner))
        self.assertEqual(job.error["code"], "login_required")
        self.assertFalse(job.error["can_login"])
        self.assertIn("Browser cookies", job.error["message"])
        self.assertEqual(job.attempt, {"engine": "yt-dlp", "auth": "anonymous"})
        probes = runner.ytdlp_calls("-J")
        self.assertEqual(len(probes), 1)
        self.assertNotIn("--cookies", probes[0])
        self.assertFalse(any(os.path.basename(c[0]).startswith("gallery-dl") for c in runner.calls))

    def test_bot_check_then_browser_cookies(self):
        runner = ScriptedRunner(YT_INFO, probes=[("fail", YT_BOT), ("ok", None)])
        job = self.run_one(self.make(runner, browser_cookies="firefox"))
        self.assertEqual(job.status, "done", job.error)
        self.assertEqual(job.attempt, {"engine": "yt-dlp", "auth": "browser"})
        probes = runner.ytdlp_calls("-J")
        self.assertNotIn("--cookies-from-browser", probes[0])
        self.assertEqual(probes[1][probes[1].index("--cookies-from-browser") + 1], "firefox")
        self.assertIn("--no-playlist", probes[1])
        self.assertIn("Trying with browser cookies", [p["stage_text"] for p in self.job_events(job.id)])

    def test_bundled_deno_is_passed_to_ytdlp(self):
        deno = self.paths.binary("deno.exe")
        open(deno, "wb").close()
        self.bins["deno"] = deno
        runner = ScriptedRunner(YT_INFO)
        job = self.run_one(self.make(runner))
        self.assertEqual(job.status, "done", job.error)
        for argv in runner.ytdlp_calls("-J") + runner.ytdlp_calls("--load-info-json"):
            self.assertEqual(argv[argv.index("--js-runtimes") + 1], f"deno:{deno}")
        self.assertEqual(os.path.basename(downloader.subprocess_env()["DENO_DIR"]), "deno")
        self.assertTrue(downloader.subprocess_env()["DENO_DIR"].startswith(self.paths.cache_dir))

    def test_without_deno_no_runtime_flag(self):
        runner = ScriptedRunner(YT_INFO)
        job = self.run_one(self.make(runner))
        self.assertEqual(job.status, "done", job.error)
        self.assertNotIn("--js-runtimes", runner.ytdlp_calls("-J")[0])
        self.bins["deno"] = self.paths.binary("deno.exe")  # recorded but no longer on disk
        runner2 = ScriptedRunner(YT_INFO)
        self.run_one(self.make(runner2))
        self.assertNotIn("--js-runtimes", runner2.ytdlp_calls("-J")[0])

    def test_rate_limit_backoff_names_youtube(self):
        runner = ScriptedRunner(YT_INFO, probes=[("fail", YT_RATE), ("ok", None)])
        job = self.run_one(self.make(runner))
        self.assertEqual(job.status, "done", job.error)
        self.assertEqual(self.sleeps, [30.0])
        texts = [p["stage_text"] for p in self.job_events(job.id)]
        self.assertIn("YouTube is limiting requests, waiting 30 s", texts)
        self.assertFalse(any("Instagram" in t for t in texts))

    def test_rate_limit_with_browser_goes_to_browser(self):
        runner = ScriptedRunner(YT_INFO, probes=[("fail", YT_RATE), ("ok", None)])
        job = self.run_one(self.make(runner, browser_cookies="edge"))
        self.assertEqual(job.status, "done", job.error)
        self.assertEqual(self.sleeps, [])
        self.assertEqual(job.attempt["auth"], "browser")

    def test_live_info_is_refused(self):
        runner = ScriptedRunner(dict(YT_INFO, live_status="is_live", is_live=True))
        job = self.run_one(self.make(runner))
        self.assertEqual(job.error["code"], "not_found")
        self.assertIn("Live streams", job.error["message"])
        self.assertFalse(runner.ytdlp_calls("--load-info-json"))

    def test_network_message_names_youtube(self):
        runner = ScriptedRunner(YT_INFO, probes=[("fail", "ERROR: [youtube] X: Unable to download API page: "
                                                           "[Errno 11001] getaddrinfo failed")])
        job = self.run_one(self.make(runner))
        self.assertEqual(job.error["code"], "network")
        self.assertIn("YouTube", job.error["message"])


if __name__ == "__main__":
    unittest.main()
