"""mp3 pipeline, per-job format rules, YouTube format selection and the job table per platform (no network)."""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core_fakes import IG_INFO, YT_INFO, CoreTestBase, ScriptedRunner  # noqa: E402

from app.core.downloader import Downloader, select_formats  # noqa: E402

YT_URL = "https://www.youtube.com/watch?v=aqz-KE-bpKQ"
IG_URL = "https://www.instagram.com/reel/DQCkNLtgqEe"


class Mp3PipelineTest(CoreTestBase, unittest.TestCase):
    def run_job(self, dl, url=YT_URL, code="aqz-KE-bpKQ", platform="youtube", kind="video", fmt="mp3"):
        job = dl.get(dl.add(url, code, platform, kind, fmt)["id"])
        dl._run_job(job)
        return job

    def test_youtube_mp3_end_to_end(self):
        runner = ScriptedRunner(YT_INFO)
        job = self.run_job(self.make(runner))
        self.assertEqual(job.status, "done", job.error)
        self.assertEqual(job.filepath, os.path.join(self.out, "Blender_aqz-KE-bpKQ.mp3"))
        self.assertTrue(os.path.isfile(job.filepath))
        self.assertEqual((job.width, job.height, job.fps, job.vcodec, job.acodec), (None, None, None, None, "mp3"))
        self.assertFalse(job.transcoded)
        self.assertEqual(job.duration_s, 635.0)
        self.assertEqual(job.filesize, os.path.getsize(job.filepath))
        self.assertEqual(job.title, "Big Buck Bunny 60fps 4K - Official Blender Foundation Short Film")
        call = runner.ytdlp_calls("--load-info-json")[0]
        for flag in ("-x", "--audio-format", "--audio-quality", "--no-playlist", "--ffmpeg-location"):
            self.assertIn(flag, call)
        self.assertEqual(call[call.index("-f") + 1], "ba/b")
        self.assertEqual(call[call.index("-S") + 1], "channels:2")  # stereo before the 5.1 tracks
        self.assertEqual(call[call.index("--audio-format") + 1], "mp3")
        self.assertEqual(call[call.index("--audio-quality") + 1], "0")
        self.assertNotIn("--merge-output-format", call)
        self.assertFalse(any("libx264" in c for c in runner.calls))  # no H.264 pass for audio
        texts = [p["stage_text"] for p in self.job_events(job.id)]
        self.assertIn("Downloading audio", texts)
        self.assertIn("Converting to MP3", texts)
        self.assertNotIn("Downloading video", texts)
        progress = [p["progress"] for p in self.job_events(job.id)]
        self.assertEqual(progress, sorted(progress))
        self.assertEqual(self.job_events(job.id)[-1]["format"], "mp3")

    def test_instagram_mp3_ignores_h264(self):
        runner = ScriptedRunner(IG_INFO, transcode=True)
        job = self.run_job(self.make(runner, h264_compat=True), IG_URL, "DQCkNLtgqEe", "instagram", "reel")
        self.assertEqual(job.status, "done", job.error)
        self.assertTrue(job.filepath.endswith("teddysphotos_DQCkNLtgqEe.mp3"))
        self.assertFalse(job.transcoded)
        self.assertIsNone(job.vcodec)

    def test_ffmpeg_missing_fails_before_download(self):
        self.bins["ffmpeg"] = None
        runner = ScriptedRunner(YT_INFO)
        job = self.run_job(self.make(runner))
        self.assertEqual(job.error["code"], "ffmpeg_missing")
        self.assertIn("MP3", job.error["message"])
        self.assertIn("Update the engine", job.error["message"])
        self.assertFalse(runner.ytdlp_calls("--load-info-json"))
        self.assertFalse(job.error["can_login"])

    def test_skip_existing_checks_the_exact_extension(self):
        os.makedirs(self.out)
        with open(os.path.join(self.out, "Blender_aqz-KE-bpKQ.mp4"), "wb") as fh:
            fh.write(b"video")
        job = self.run_job(self.make(ScriptedRunner(YT_INFO)))
        self.assertFalse(job.already_existed)  # an mp4 of the same name is not the mp3
        self.assertTrue(job.filepath.endswith(".mp3"))
        again = self.run_job(self.make(ScriptedRunner(YT_INFO)))
        self.assertEqual((again.status, again.already_existed, again.stage_text), ("done", True, "Already downloaded"))
        self.assertEqual((again.vcodec, again.acodec), (None, "mp3"))

    def test_silent_video_has_no_mp3(self):
        info = dict(YT_INFO, formats=[f for f in YT_INFO["formats"] if f["vcodec"] not in (None, "none")
                                      and f.get("acodec") == "none"])
        runner = ScriptedRunner(info)
        job = self.run_job(self.make(runner))
        self.assertEqual(job.error["code"], "not_found")
        self.assertIn("no sound", job.error["message"])
        self.assertFalse(runner.ytdlp_calls("--load-info-json"))


class YouTubeMp4Test(CoreTestBase, unittest.TestCase):
    def test_mp4_with_avc_at_720(self):
        runner = ScriptedRunner(YT_INFO)
        dl = self.make(runner, quality_youtube="720", h264_compat=True)
        job = dl.get(dl.add(YT_URL, "aqz-KE-bpKQ", "youtube", "video", "mp4")["id"])
        dl._run_job(job)
        self.assertEqual(job.status, "done", job.error)
        self.assertEqual(job.filepath, os.path.join(self.out, "Blender_aqz-KE-bpKQ.mp4"))
        self.assertFalse(job.transcoded)
        call = runner.ytdlp_calls("--load-info-json")[0]
        self.assertTrue(call[call.index("-f") + 1].startswith("298+140/"))
        self.assertIn("--no-playlist", call)
        self.assertIn("--no-playlist", runner.ytdlp_calls("-J")[0])
        self.assertTrue(os.path.basename(job.thumb_path).startswith("youtube_"))

    def test_max_4k_vp9_gets_the_h264_pass(self):
        runner = ScriptedRunner(YT_INFO, transcode=True)
        dl = self.make(runner, quality_youtube="max", h264_compat=True)
        job = dl.get(dl.add(YT_URL, "aqz-KE-bpKQ", "youtube", "video", "mp4")["id"])
        dl._run_job(job)
        self.assertEqual(job.status, "done", job.error)
        self.assertTrue(job.transcoded)
        call = runner.ytdlp_calls("--load-info-json")[0]
        self.assertTrue(call[call.index("-f") + 1].startswith("315+140/"))


class YouTubeFormatSelectionTest(unittest.TestCase):
    def test_max_without_h264_keeps_the_best_direct_stream(self):
        c = select_formats(YT_INFO, "youtube", "max", False)
        self.assertEqual(c.video["format_id"], "315")  # direct VP9 4K over HLS (628) and the lower-rate AV1 (401)
        self.assertEqual(c.audio["format_id"], "140")  # non-DRC, highest abr
        self.assertFalse(c.needs_transcode)

    def test_max_with_h264_has_no_avc_at_4k(self):
        c = select_formats(YT_INFO, "youtube", "max", True)
        self.assertEqual(c.video["format_id"], "315")
        self.assertTrue(c.needs_transcode)

    def test_caps_prefer_direct_avc(self):
        c = select_formats(YT_INFO, "youtube", "720", True)
        self.assertEqual((c.video["format_id"], c.audio["format_id"], c.needs_transcode), ("298", "140", False))
        self.assertEqual(select_formats(YT_INFO, "youtube", "720", False).video["format_id"], "298")
        upto_1080 = dict(YT_INFO, formats=[f for f in YT_INFO["formats"] if (f.get("height") or 0) <= 1080])
        c = select_formats(upto_1080, "youtube", "max", True)
        self.assertEqual((c.video["format_id"], c.audio["format_id"], c.needs_transcode), ("299", "140", False))

    def test_youtube_qualities(self):
        c = select_formats(YT_INFO, "youtube", "360", True)
        self.assertEqual((c.video["format_id"], c.video["height"]), ("18", 360))  # muxed 360p keeps its own audio
        self.assertIsNone(c.audio)
        self.assertEqual(select_formats(YT_INFO, "youtube", "720", True).video["height"], 720)
        c = select_formats(YT_INFO, "youtube", "max", False)
        self.assertEqual((c.video["height"], c.video["width"]), (2160, 3840))

    def test_youtube_360_falls_back_to_the_smallest_when_nothing_fits(self):
        info = {"formats": [f for f in YT_INFO["formats"] if f["format_id"] in ("298", "299", "140")]}
        self.assertEqual(select_formats(info, "youtube", "360", True).video["format_id"], "298")

    def test_1440_top_is_transcoded(self):
        info = dict(YT_INFO, formats=[f for f in YT_INFO["formats"] if (f.get("height") or 0) <= 1440])
        c = select_formats(info, "youtube", "max", True)
        self.assertEqual(c.video["format_id"], "308")
        self.assertTrue(c.needs_transcode)

    def test_shorts_vertical_cap_by_short_side(self):
        info = {"formats": [
            {"format_id": "137", "vcodec": "avc1.640028", "acodec": "none", "width": 1080, "height": 1920, "fps": 30,
             "tbr": 2000, "protocol": "https"},
            {"format_id": "136", "vcodec": "avc1.4d401f", "acodec": "none", "width": 720, "height": 1280, "fps": 30,
             "tbr": 1000, "protocol": "https"},
            {"format_id": "251", "vcodec": "none", "acodec": "opus", "abr": 120, "protocol": "https"},
            {"format_id": "140", "vcodec": "none", "acodec": "mp4a.40.2", "abr": 129, "protocol": "https"}]}
        self.assertEqual(select_formats(info, "youtube", "max", True).video["format_id"], "137")
        self.assertEqual(select_formats(info, "youtube", "720", True).video["format_id"], "136")

    def test_muxed_only_keeps_its_own_audio(self):
        info = {"formats": [f for f in YT_INFO["formats"] if f["format_id"] in ("18", "140", "sb0")]}
        c = select_formats(info, "youtube", "max", True)
        self.assertEqual(c.video["format_id"], "18")
        self.assertIsNone(c.audio)
        self.assertTrue(c.spec.startswith("18/"))

    def test_original_language_track_wins(self):
        info = {"formats": [
            {"format_id": "299", "vcodec": "avc1", "acodec": "none", "width": 1920, "height": 1080, "fps": 30},
            {"format_id": "140-1", "vcodec": "none", "acodec": "mp4a.40.2", "abr": 160, "language_preference": -1},
            {"format_id": "140-0", "vcodec": "none", "acodec": "mp4a.40.2", "abr": 128, "language_preference": 10}]}
        self.assertEqual(select_formats(info, "youtube", "max", True).audio["format_id"], "140-0")

    def test_stereo_beats_surround(self):
        # With a JavaScript runtime YouTube also lists 6-channel tracks (258 AAC 388 kbit/s, 380 AC-3, 328 E-AC-3).
        surround = [
            {"format_id": "258", "vcodec": "none", "acodec": "mp4a.40.2", "abr": 387.9, "asr": 48000,
             "audio_channels": 6, "protocol": "https", "language_preference": -1},
            {"format_id": "380", "vcodec": "none", "acodec": "ac-3", "abr": 384.1, "asr": 48000,
             "audio_channels": 6, "protocol": "https", "language_preference": -1},
            {"format_id": "328", "vcodec": "none", "acodec": "ec-3", "abr": 384.1, "asr": 48000,
             "audio_channels": 6, "protocol": "https", "language_preference": -1}]
        stereo = [dict(f, audio_channels=2) for f in YT_INFO["formats"] if f["format_id"] in ("140", "251")]
        video = [f for f in YT_INFO["formats"] if f["format_id"] == "299"]
        for compat in (True, False):
            with self.subTest(h264_compat=compat):
                c = select_formats({"formats": video + surround + stereo}, "youtube", "max", compat)
                self.assertEqual(c.audio["format_id"], "140")  # stereo AAC 129 kbit/s over 5.1 at 388
        # unknown channel count counts as stereo; a surround track is still taken when nothing else exists
        c = select_formats({"formats": video + surround}, "youtube", "max", True)
        self.assertEqual(c.audio["format_id"], "258")

    def test_no_video_metadata_and_storyboards_only(self):
        info = {"formats": [{"format_id": "sb0", "ext": "mhtml", "vcodec": "none", "acodec": "none",
                             "width": 320, "height": 180, "format_note": "storyboard"},
                            {"format_id": "x", "vcodec": None, "acodec": None}]}
        c = select_formats(info, "youtube", "720", True)
        self.assertEqual(c.spec, "bv*+ba/b")
        self.assertIn("res:720", c.sort_args[1])


IG_LADDER_INFO = dict(IG_INFO, formats=IG_INFO["formats"] + [
    {"format_id": "dash-480", "vcodec": "vp09.00.30.08", "acodec": "none", "width": 480, "height": 854, "tbr": 800}])


class InstagramQualityTest(unittest.TestCase):
    def test_low_medium_max(self):
        for quality, expected in (("low", "dash-480"), ("medium", "dash-720"), ("max", "dash-1080")):
            with self.subTest(quality=quality):
                c = select_formats(IG_LADDER_INFO, "instagram", quality, False)
                self.assertEqual(c.video["format_id"], expected)
                self.assertEqual(c.audio["format_id"], "dash-a")

    def test_low_falls_back_to_the_smallest_when_nothing_fits(self):
        info = dict(IG_INFO, formats=[f for f in IG_INFO["formats"] if f["format_id"] != "dash-720"])
        self.assertEqual(select_formats(info, "instagram", "low", False).video["format_id"], "dash-1080")

    def test_youtube_values_mean_nothing_on_instagram(self):
        # an unknown value for the platform behaves as "max" (settings validation keeps this from happening)
        self.assertEqual(select_formats(IG_LADDER_INFO, "instagram", "360", False).video["format_id"], "dash-1080")


class PlatformQualityTest(CoreTestBase, unittest.TestCase):
    """A job reads the quality setting of its own platform at job start; mp3 reads neither."""

    def video_id(self, runner):
        call = runner.ytdlp_calls("--load-info-json")[0]
        return call[call.index("-f") + 1].split("+")[0].split("/")[0]

    def run_job(self, runner, url, code, platform, kind, fmt="mp4", **patch):
        dl = self.make(runner, **patch)
        job = dl.get(dl.add(url, code, platform, kind, fmt)["id"])
        dl._run_job(job)
        self.assertEqual(job.status, "done", job.error)
        return job

    def test_instagram_job_uses_quality_instagram(self):
        runner = ScriptedRunner(IG_LADDER_INFO)
        self.run_job(runner, IG_URL, "DQCkNLtgqEe", "instagram", "reel", h264_compat=False,
                     quality_instagram="low", quality_youtube="max")
        self.assertEqual(self.video_id(runner), "dash-480")

    def test_instagram_job_ignores_quality_youtube(self):
        runner = ScriptedRunner(IG_LADDER_INFO)
        self.run_job(runner, IG_URL, "DQCkNLtgqEe", "instagram", "reel", h264_compat=False,
                     quality_instagram="max", quality_youtube="360")
        self.assertEqual(self.video_id(runner), "dash-1080")

    def test_youtube_job_uses_quality_youtube(self):
        runner = ScriptedRunner(YT_INFO)
        self.run_job(runner, YT_URL, "aqz-KE-bpKQ", "youtube", "video", h264_compat=False,
                     quality_instagram="low", quality_youtube="720")
        self.assertEqual(self.video_id(runner), "298")  # AVC 720p60 (same area and fps as VP9 302; bitrate wins)

    def test_youtube_job_ignores_quality_instagram(self):
        runner = ScriptedRunner(YT_INFO)
        self.run_job(runner, YT_URL, "aqz-KE-bpKQ", "youtube", "video", h264_compat=False,
                     quality_instagram="low", quality_youtube="max")
        self.assertEqual(self.video_id(runner), "315")

    def test_setting_is_read_at_job_start(self):
        runner = ScriptedRunner(YT_INFO)
        dl = self.make(runner, h264_compat=False, quality_youtube="max")
        job = dl.get(dl.add(YT_URL, "aqz-KE-bpKQ", "youtube", "video", "mp4")["id"])
        self.settings["quality_youtube"] = "360"  # changed while queued: the job starts with the new value
        dl._run_job(job)
        self.assertEqual(self.video_id(runner), "18")

    def test_mp3_ignores_both_settings(self):
        for platform, url, code, kind, info in (("youtube", YT_URL, "aqz-KE-bpKQ", "video", YT_INFO),
                                                ("instagram", IG_URL, "DQCkNLtgqEe", "reel", IG_LADDER_INFO)):
            with self.subTest(platform=platform):
                runner = ScriptedRunner(info)
                self.run_job(runner, url, code, platform, kind, fmt="mp3", quality_instagram="low",
                             quality_youtube="360")
                call = runner.ytdlp_calls("--load-info-json")[0]
                self.assertEqual(call[call.index("-f") + 1], "ba/b")
                self.assertEqual(call[call.index("-S") + 1], "channels:2")  # no resolution cap in the sort


class JobTableTest(CoreTestBase, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.usable = False  # no workers anyway; keeps "Waiting for engine update"
        self.dl = self.make(ScriptedRunner(YT_INFO))

    def add(self, platform, code, fmt="mp4"):
        url = IG_URL if platform == "instagram" else YT_URL
        kind = "reel" if platform == "instagram" else "video"
        return self.dl.get(self.dl.add(url, code, platform, kind, fmt)["id"])

    def test_add_validates(self):
        with self.assertRaises(ValueError):
            self.dl.add(YT_URL, "aqz-KE-bpKQ", "tiktok", "video", "mp4")
        with self.assertRaises(ValueError):
            self.dl.add(YT_URL, "aqz-KE-bpKQ", "youtube", "reel", "mp4")
        with self.assertRaises(ValueError):
            self.dl.add(YT_URL, "aqz-KE-bpKQ", "youtube", "video", "wav")
        d = self.dl.add(YT_URL, "aqz-KE-bpKQ", "youtube", "shorts", "mp3")
        self.assertEqual((d["platform"], d["kind"], d["format"], d["previewed"]), ("youtube", "shorts", "mp3", False))

    def test_set_format_only_while_queued(self):
        job = self.add("youtube", "aqz-KE-bpKQ")
        self.events.clear()
        d = self.dl.set_format(job.id, "mp3")
        self.assertEqual(d["format"], "mp3")
        self.assertEqual([p["format"] for p in self.job_events(job.id)], ["mp3"])
        self.assertEqual(self.dl.set_format(job.id, "mp3")["format"], "mp3")  # same value: no event
        self.assertEqual(len(self.job_events(job.id)), 1)
        with self.assertRaises(ValueError):
            self.dl.set_format(job.id, "flac")
        self.assertIsNone(self.dl.set_format("nope", "mp4"))
        job.status = "downloading"
        self.assertIsNone(self.dl.set_format(job.id, "mp4"))
        job.status = "queued"
        self.assertTrue(self.dl.cancel(job.id))
        self.assertIsNone(self.dl.set_format(job.id, "mp4"))
        self.assertEqual(job.format, "mp3")

    def test_starting_a_job_freezes_its_format(self):
        job = self.add("instagram", "DQCkNLtgqEe")
        self.usable = True
        picked = self.dl._next_job()
        self.assertIs(picked, job)
        self.assertNotEqual(job.status, "queued")
        self.assertIsNone(self.dl.set_format(job.id, "mp3"))

    def test_active_keys_per_platform(self):
        a = self.add("instagram", "SAMECODE123")
        self.add("youtube", "SAMECODE123")
        self.assertEqual(self.dl.active_keys(), {"instagram:SAMECODE123", "youtube:SAMECODE123"})
        self.dl.cancel(a.id)
        self.assertEqual(self.dl.active_keys(), {"youtube:SAMECODE123"})
        # retry: the active YouTube job with the same id does not block the Instagram one
        self.assertTrue(self.dl.retry(a.id))
        b = self.add("youtube", "aqz-KE-bpKQ")
        self.dl.cancel(b.id)
        self.add("youtube", "aqz-KE-bpKQ")
        self.assertFalse(self.dl.retry(b.id))  # same platform + id already queued

    def test_clear_finished_per_platform(self):
        ig = [self.add("instagram", f"IGCODE{i}") for i in range(2)]
        yt = [self.add("youtube", f"YTCODE{i}xxxx") for i in range(3)]
        for job in ig + yt[:2]:
            self.dl.cancel(job.id)
        self.assertEqual(self.dl.clear_finished("youtube"), 2)
        self.assertEqual({j["platform"] for j in self.dl.jobs()}, {"instagram", "youtube"})
        self.assertEqual(self.dl.clear_finished("youtube"), 0)  # the queued one stays
        self.assertEqual(self.dl.clear_finished(None), 2)
        self.assertEqual([j["id"] for j in self.dl.jobs()], [yt[2].id])

    def test_history_v2_round_trip(self):
        job = self.add("youtube", "aqz-KE-bpKQ", "mp3")
        job.status, job.previewed = "done", True
        self.dl.save_history()
        with open(self.paths.history_file, encoding="utf-8") as fh:
            raw = json.load(fh)
        self.assertEqual(raw["version"], 2)
        self.assertEqual((raw["jobs"][0]["platform"], raw["jobs"][0]["kind"], raw["jobs"][0]["format"]),
                         ("youtube", "video", "mp3"))
        dl2 = self.make(ScriptedRunner(YT_INFO))
        dl2.load_history()
        d = dl2.jobs()[0]
        self.assertEqual((d["platform"], d["kind"], d["format"], d["previewed"]), ("youtube", "video", "mp3", True))


if __name__ == "__main__":
    unittest.main()
