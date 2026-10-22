"""TikTok and X in the core: messages, error classification, metadata, format choice, auth ladder, job table and
the mp4 / mp3 pipelines with a scripted runner (no network). Fixtures are trimmed real `yt-dlp -J` output."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core_fakes import TT_INFO, X_INFO, CoreTestBase, ScriptedRunner  # noqa: E402

from app.core import settings as S  # noqa: E402
from app.core.downloader import (JOB_KINDS, PLATFORM_NAMES, Job, Rung, can_login_for, classify_error,  # noqa: E402
                                 message_for, meta_from_info, plan_ladder, select_formats)

TT_URL = "https://www.tiktok.com/@tiktok/video/7106594312292453675"
TT_ID = "7106594312292453675"
TT_SHARE_URL = "https://vm.tiktok.com/ZMabcdef1"
X_URL = "https://x.com/oshtru/status/1577855540407197696"
X_ID = "1577855540407197696"

ANON, BROW = Rung("yt-dlp", "anonymous"), Rung("yt-dlp", "browser")

TT_BLOCKED = "ERROR: [TikTok] 6728965813137410310: Your IP address is blocked from accessing this post"
TT_PRIVATE = "ERROR: [TikTok] 7000000000000000001: Video is private"
TT_PHOTO = "ERROR: [TikTok] 7000000000000000002: Unsupported post type: photo slideshow"
TT_GONE = "ERROR: [TikTok] 7000000000000000003: Video not available"
TT_LOGIN = "ERROR: [TikTok] 7000000000000000004: This video requires login. Use --cookies-from-browser or --cookies"
X_NSFW = ("ERROR: [twitter] 1000000000000000001: NSFW tweet requires authentication. Use --cookies-from-browser or "
          "--cookies for the authentication")
X_PROTECTED = "ERROR: [twitter] 1000000000000000002: Unable to download webpage: protected tweet"
X_NO_VIDEO = "ERROR: [twitter] 1000000000000000003: No video could be found in this tweet"
X_GONE = "ERROR: [twitter] 1000000000000000004: This tweet is unavailable"
NET = "ERROR: [twitter] 1000000000000000005: Unable to download JSON metadata: <urlopen error timed out>"


class MessagesTest(unittest.TestCase):
    def test_names_and_messages_never_name_another_site(self):
        self.assertEqual(PLATFORM_NAMES["tiktok"], "TikTok")
        self.assertEqual(PLATFORM_NAMES["twitter"], "X")
        for platform, own, others in (("tiktok", "TikTok", ("Instagram", "YouTube", " X ")),
                                      ("twitter", "X", ("Instagram", "YouTube", "TikTok"))):
            for key in ("login_required", "private", "not_found", "no_video", "live", "rate_limited", "network",
                        "extractor", "disk", "canceled", "unknown", "ffmpeg_missing", "engine_missing"):
                text = message_for(key, platform)
                with self.subTest(platform=platform, key=key):
                    for other in others:
                        self.assertNotIn(other, text)
        self.assertIn("TikTok", message_for("network", "tiktok"))
        self.assertIn("X", message_for("network", "twitter"))
        self.assertIn("Browser cookies", message_for("login_required", "twitter"))

    def test_classification(self):
        cases = [
            (TT_BLOCKED, "tiktok", "not_found", "network"),
            (TT_PRIVATE, "tiktok", "private", "private"),
            (TT_PHOTO, "tiktok", "not_found", "photo"),
            (TT_GONE, "tiktok", "not_found", "not found"),
            (TT_LOGIN, "tiktok", "login_required", "Browser cookies"),
            (X_NSFW, "twitter", "login_required", "signed-in"),
            (X_PROTECTED, "twitter", "private", "protected"),
            (X_NO_VIDEO, "twitter", "not_found", "no video"),
            (X_GONE, "twitter", "not_found", "Post not found"),
            (NET, "twitter", "network", "reach X"),
            ("", "tiktok", "unknown", "Unknown"),
        ]
        for text, platform, code, fragment in cases:
            with self.subTest(text=text[:40]):
                got_code, msg = classify_error(text, platform)
                self.assertEqual(got_code, code)
                self.assertIn(fragment, msg)

    def test_shared_rows_keep_working_with_platform_texts(self):
        code, msg = classify_error("ERROR: No space left on device", "tiktok")
        self.assertEqual(code, "disk")
        code, msg = classify_error("ERROR: [twitter] x: HTTP Error 429: Too Many Requests", "twitter")
        self.assertEqual((code, "X has" in msg), ("rate_limited", True))


class MetaTest(unittest.TestCase):
    def test_tiktok_handle_and_caption(self):
        m = meta_from_info(TT_INFO)
        self.assertEqual(m["author"], "tiktok")  # the @handle, not the display name "TikTok" or the numeric id
        self.assertTrue(m["title"].startswith("how many frogs did you find?"))
        self.assertEqual((m["date"], m["duration_s"]), ("2022-06-07", 24.0))
        self.assertEqual(meta_from_info(TT_INFO, "tiktok"), m)

    def test_x_handle_and_text_without_tco(self):
        m = meta_from_info(X_INFO)
        self.assertEqual(m["author"], "oshtru")
        self.assertEqual(m["title"], "gm now I can post image and video. nice update.")
        self.assertEqual((m["date"], m["duration_s"]), ("2022-10-06", 30.03))

    def test_x_falls_back_to_title_without_description(self):
        info = dict(X_INFO, description="https://t.co/abc")
        self.assertEqual(meta_from_info(info)["title"], X_INFO["title"])


class FormatChoiceTest(unittest.TestCase):
    def test_tiktok_max_is_the_1080_hevc_file_and_the_watermarked_copy_is_skipped(self):
        c = select_formats(TT_INFO, "tiktok", "max", h264_compat=False)
        self.assertEqual(c.video["format_id"], "bytevc1_1080p_1094429-0")
        self.assertIsNone(c.audio)  # muxed: no second audio track from the "audio" format
        self.assertEqual(c.spec, "bytevc1_1080p_1094429-0/bv*+ba/b")
        self.assertFalse(c.needs_transcode)
        for q in ("low", "medium", "max"):
            self.assertNotEqual(select_formats(TT_INFO, "tiktok", q, False).video["format_id"], "download")

    def test_tiktok_h264_pass_only_when_no_avc_at_that_size(self):
        c = select_formats(TT_INFO, "tiktok", "max", h264_compat=True)
        self.assertEqual(c.video["format_id"], "bytevc1_1080p_1094429-0")  # no AVC at 1080p: keep the best, transcode
        self.assertTrue(c.needs_transcode)
        c = select_formats(TT_INFO, "tiktok", "low", h264_compat=True)
        self.assertEqual(c.video["format_id"], "h264_540p_377700-0")  # nothing fits 480: the smallest file, AVC
        self.assertFalse(c.needs_transcode)

    def test_tiktok_caps(self):
        self.assertEqual(select_formats(TT_INFO, "tiktok", "medium", False).video["format_id"],
                         "bytevc1_720p_640712-0")
        # "low" = short side <= 480: nothing fits (540p is the smallest), so the smallest available video
        self.assertEqual(select_formats(TT_INFO, "tiktok", "low", False).video["height"], 1024)

    def test_x_progressive_file_beats_hls_and_needs_no_audio_track(self):
        c = select_formats(X_INFO, "twitter", "max", h264_compat=True)
        self.assertEqual(c.video["format_id"], "http-2176")
        self.assertIsNone(c.audio)  # unknown codecs = a muxed file; the HLS audio renditions are not usable
        self.assertEqual(c.spec, "http-2176/bv*+ba/b")
        self.assertEqual(select_formats(X_INFO, "twitter", "medium", False).video["format_id"], "http-2176")
        self.assertEqual(select_formats(X_INFO, "twitter", "low", False).video["format_id"], "http-950")


class LadderTest(unittest.TestCase):
    def test_plan_and_login(self):
        for platform in ("tiktok", "twitter"):
            with self.subTest(platform=platform):
                self.assertEqual(plan_ladder("auto", True, "", platform), [ANON])
                self.assertEqual(plan_ladder("always", True, "edge", platform), [ANON, BROW])
                self.assertEqual(plan_ladder("never", False, "firefox", platform), [ANON, BROW])
                for code in ("login_required", "private", "rate_limited"):
                    self.assertFalse(can_login_for(code, False, platform))


class PipelineTest(CoreTestBase, unittest.TestCase):
    def run_job(self, runner, url, code, platform, kind, fmt="mp4", **patch):
        dl = self.make(runner, **patch)
        job = dl.get(dl.add(url, code, platform, kind, fmt)["id"])
        dl._run_job(job)
        return job

    def video_id(self, runner):
        call = runner.ytdlp_calls("--load-info-json")[0]
        return call[call.index("-f") + 1].split("+")[0].split("/")[0]

    def test_tiktok_mp4_reads_quality_tiktok(self):
        runner = ScriptedRunner(TT_INFO)
        job = self.run_job(runner, TT_URL, TT_ID, "tiktok", "video", h264_compat=False, quality_tiktok="medium",
                           quality_instagram="low", quality_youtube="low", quality_twitter="low")
        self.assertEqual(job.status, "done", job.error)
        self.assertEqual(self.video_id(runner), "bytevc1_720p_640712-0")
        self.assertEqual(job.author, "tiktok")
        self.assertEqual(os.path.basename(job.filepath), f"tiktok_{TT_ID}.mp4")
        probe = runner.ytdlp_calls("-J")[0]
        self.assertNotIn("--no-playlist", probe)  # YouTube-only flag
        self.assertEqual(probe[-1], TT_URL)

    def test_x_mp4_and_mp3(self):
        runner = ScriptedRunner(X_INFO)
        job = self.run_job(runner, X_URL, X_ID, "twitter", "post", quality_twitter="max")
        self.assertEqual(job.status, "done", job.error)
        self.assertEqual(self.video_id(runner), "http-2176")
        self.assertEqual(job.author, "oshtru")
        self.assertEqual(os.path.basename(job.filepath), f"oshtru_{X_ID}.mp4")
        runner = ScriptedRunner(X_INFO)
        job = self.run_job(runner, X_URL, X_ID, "twitter", "post", fmt="mp3")
        self.assertEqual(job.status, "done", job.error)
        self.assertTrue(job.filepath.endswith(".mp3"))

    def test_tiktok_share_link_is_passed_to_the_engine_as_is(self):
        runner = ScriptedRunner(TT_INFO)
        job = self.run_job(runner, TT_SHARE_URL, "ZMabcdef1", "tiktok", "share")
        self.assertEqual(job.status, "done", job.error)
        self.assertEqual(runner.ytdlp_calls("-J")[0][-1], TT_SHARE_URL)
        self.assertEqual(os.path.basename(job.filepath), "tiktok_ZMabcdef1.mp4")

    def test_blocked_tiktok_stops_the_ladder(self):
        runner = ScriptedRunner(TT_INFO, probes=[("fail", TT_BLOCKED), ("fail", TT_BLOCKED)])
        job = self.run_job(runner, TT_URL, TT_ID, "tiktok", "video", browser_cookies="edge")
        self.assertEqual(job.status, "error")
        self.assertEqual(job.error["code"], "not_found")
        self.assertIn("network", job.error["message"])
        self.assertFalse(job.error["can_login"])
        self.assertEqual(len(runner.ytdlp_calls("-J")), 1)  # not_found stops: no browser rung

    def test_x_login_required_goes_to_the_browser_rung(self):
        runner = ScriptedRunner(X_INFO, probes=[("fail", X_NSFW), ("ok", X_INFO)])
        job = self.run_job(runner, X_URL, X_ID, "twitter", "post", browser_cookies="firefox")
        self.assertEqual(job.status, "done", job.error)
        probes = runner.ytdlp_calls("-J")
        self.assertEqual(len(probes), 2)
        self.assertIn("--cookies-from-browser", probes[1])


class JobTableTest(CoreTestBase, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.usable = False
        self.dl = self.make(ScriptedRunner(TT_INFO))

    def test_kinds_per_platform(self):
        self.assertEqual(JOB_KINDS["tiktok"], ("video", "share"))
        self.assertEqual(JOB_KINDS["twitter"], ("post",))
        a = self.dl.add(TT_URL, TT_ID, "tiktok", "video", "mp4")
        b = self.dl.add(TT_SHARE_URL, "ZMabcdef1", "tiktok", "share", "mp3")
        c = self.dl.add(X_URL, X_ID, "twitter", "post", "mp4")
        self.assertEqual({a["platform"], b["platform"], c["platform"]}, {"tiktok", "twitter"})
        self.assertEqual(self.dl.active_keys(), {f"tiktok:{TT_ID}", "tiktok:ZMabcdef1", f"twitter:{X_ID}"})
        for platform, kind in (("tiktok", "post"), ("twitter", "video"), ("twitter", "shorts"), ("vimeo", "video")):
            with self.subTest(platform=platform, kind=kind):
                with self.assertRaises(ValueError):
                    self.dl.add(X_URL, X_ID, platform, kind, "mp4")

    def test_clear_finished_per_platform(self):
        a = self.dl.add(TT_URL, TT_ID, "tiktok", "video", "mp4")
        b = self.dl.add(X_URL, X_ID, "twitter", "post", "mp4")
        for jid in (a["id"], b["id"]):
            self.dl.cancel(jid)
        self.assertEqual(self.dl.clear_finished("twitter"), 1)
        self.assertEqual([j["platform"] for j in self.dl.jobs()], ["tiktok"])

    def test_history_round_trip_keeps_platform_and_kind(self):
        a = self.dl.add(TT_SHARE_URL, "ZMabcdef1", "tiktok", "share", "mp4")
        b = self.dl.add(X_URL, X_ID, "twitter", "post", "mp3")
        self.dl.cancel(a["id"])
        self.dl.cancel(b["id"])
        self.dl.save_history()
        other = self.make(ScriptedRunner(TT_INFO))
        other.load_history()
        restored = {j["id"]: j for j in other.jobs()}
        self.assertEqual((restored[a["id"]]["platform"], restored[a["id"]]["kind"]), ("tiktok", "share"))
        self.assertEqual((restored[b["id"]]["platform"], restored[b["id"]]["kind"], restored[b["id"]]["format"]),
                         ("twitter", "post", "mp3"))
        # a v2 entry with an unknown kind gets it back from the URL
        self.assertEqual(Job.from_history(dict(restored[a["id"]], kind="weird")).kind, "share")
        self.assertEqual(Job.from_history(dict(restored[b["id"]], kind="video")).kind, "post")


class SettingsTest(unittest.TestCase):
    def test_platforms_and_keys(self):
        self.assertEqual(S.PLATFORMS, ("instagram", "youtube", "tiktok", "twitter"))
        self.assertEqual(S.QUALITY_KEYS["tiktok"], "quality_tiktok")
        d = S.defaults()
        self.assertEqual((d["quality_tiktok"], d["quality_twitter"]), ("max", "max"))


if __name__ == "__main__":
    unittest.main()
