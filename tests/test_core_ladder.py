"""Auth ladder decisions and the whole job pipeline with a fake process runner (no network)."""
import json
import os
import shutil
import tempfile
import threading
import time
import unittest

from app.core import auth
from app.core import settings as S
from app.core.downloader import (Downloader, Failure, ProcessResult, Rung, can_login_for, ladder_action,
                                 pick_final_error, plan_ladder, select_formats)
from app.core.paths import AppPaths

ANON, SESS, BROW, GDL = Rung("yt-dlp", "anonymous"), Rung("yt-dlp", "session"), Rung("yt-dlp", "browser"), Rung("gallery-dl", "session")

EMPTY_MEDIA = ("ERROR: [Instagram] X: Instagram sent an empty media response. Check if this post is accessible in "
               "your browser without being logged-in. If it is not, then use --cookies-from-browser or --cookies")
RATE = "ERROR: [Instagram] X: Unable to download JSON metadata: HTTP Error 429: Too Many Requests"
EXTRACT = "ERROR: [Instagram] X: Unable to extract shared data; please report this issue on https://github.com"

INFO = {
    "id": "DQCkNLtgqEe", "_type": "video", "extractor": "Instagram", "webpage_url": "https://www.instagram.com/reel/DQCkNLtgqEe",
    "channel": "teddysphotos", "uploader": "Ed Sheeran", "uploader_id": "185546187", "title": "Video by teddysphotos",
    "description": "Caption line", "upload_date": "20251020", "duration": 26.2,
    "thumbnail": "https://cdn.example/thumb.jpg",
    "formats": [
        {"format_id": "dash-a", "vcodec": "none", "acodec": "mp4a.40.5", "abr": 72.9},
        {"format_id": "1", "vcodec": None, "acodec": None},
        {"format_id": "dash-360", "vcodec": "vp09.00.21.08", "acodec": "none", "width": 360, "height": 640, "tbr": 372},
        {"format_id": "dash-720", "vcodec": "vp09.00.31.08", "acodec": "none", "width": 720, "height": 1280, "tbr": 1463},
        {"format_id": "dash-1080", "vcodec": "vp09.00.40.08", "acodec": "none", "width": 1080, "height": 1920, "tbr": 1938},
    ],
}


class LadderDecisionTest(unittest.TestCase):
    def test_plan(self):
        self.assertEqual(plan_ladder("auto", False, ""), [ANON])
        self.assertEqual(plan_ladder("auto", True, ""), [ANON, SESS, GDL])
        self.assertEqual(plan_ladder("auto", True, "firefox"), [ANON, SESS, BROW, GDL])
        self.assertEqual(plan_ladder("auto", False, "edge"), [ANON, BROW])
        self.assertEqual(plan_ladder("always", True, ""), [SESS, GDL])
        self.assertEqual(plan_ladder("always", False, ""), [ANON])
        self.assertEqual(plan_ladder("never", True, "chrome"), [ANON])

    def test_actions(self):
        kw = dict(rate_retries_left=2, self_heal_available=True)
        self.assertEqual(ladder_action("login_required", ANON, [SESS], **kw), "next")
        self.assertEqual(ladder_action("login_required", ANON, [], **kw), "stop")
        self.assertEqual(ladder_action("private", ANON, [SESS], **kw), "next")
        self.assertEqual(ladder_action("network", ANON, [SESS], **kw), "stop")
        self.assertEqual(ladder_action("not_found", ANON, [SESS], **kw), "stop")
        self.assertEqual(ladder_action("disk", SESS, [GDL], **kw), "stop")
        self.assertEqual(ladder_action("rate_limited", ANON, [SESS], **kw), "next")
        self.assertEqual(ladder_action("rate_limited", ANON, [], **kw), "backoff")
        self.assertEqual(ladder_action("rate_limited", SESS, [GDL], **kw), "backoff")
        self.assertEqual(ladder_action("rate_limited", ANON, [], rate_retries_left=0, self_heal_available=True), "stop")
        self.assertEqual(ladder_action("extractor", ANON, [SESS], **kw), "self_heal")
        self.assertEqual(ladder_action("extractor", ANON, [SESS], rate_retries_left=2, self_heal_available=False), "next")
        self.assertEqual(ladder_action("extractor", GDL, [], **kw), "stop")
        self.assertEqual(ladder_action("unknown", SESS, [GDL], **kw), "next")

    def test_final_error(self):
        f = pick_final_error([Failure(ANON, "login_required", "a"), Failure(SESS, "extractor", "b"),
                              Failure(GDL, "unknown", "c")])
        self.assertEqual(f.code, "login_required")
        f = pick_final_error([Failure(ANON, "login_required", "a"), Failure(SESS, "network", "b")])
        self.assertEqual(f.code, "network")
        f = pick_final_error([Failure(ANON, "login_required", "a"), Failure(SESS, "not_found", "b")])
        self.assertEqual(f.code, "not_found")

    def test_can_login(self):
        self.assertTrue(can_login_for("login_required", False))
        self.assertTrue(can_login_for("login_required", True))  # dead session -> sign in again
        self.assertTrue(can_login_for("private", False))
        self.assertFalse(can_login_for("private", True))
        self.assertFalse(can_login_for("network", False))


class FakeRunner:
    """Emulates yt-dlp / ffmpeg / ffprobe / gallery-dl. `script` maps a rung auth to a list of probe outcomes."""

    def __init__(self, probe_outcomes, transcode=True):
        self.probe_outcomes = list(probe_outcomes)  # each: ("ok", None) | ("fail", stderr) | ("block", None)
        self.calls = []
        self.transcode = transcode

    def run(self, argv, *, cwd=None, on_stdout=None, on_stderr=None, capture_stdout=True, cancel=None, timeout=None):
        self.calls.append(list(argv))
        exe = os.path.basename(argv[0])
        if exe.startswith("ffprobe"):
            path = argv[-1]
            in_cache = os.path.join("cache", "jobs") in path
            vcodec = "vp9" if (in_cache and self.transcode and not os.path.basename(path).startswith("h264")) else "h264"
            data = {"streams": [{"codec_type": "video", "codec_name": vcodec, "width": 1080, "height": 1920,
                                 "avg_frame_rate": "30/1"}, {"codec_type": "audio", "codec_name": "aac"}],
                    "format": {"duration": "26.2"}}
            return ProcessResult(0, json.dumps(data), "")
        if exe.startswith("ffmpeg"):
            dst = argv[-1]
            with open(dst, "wb") as fh:
                fh.write(b"H264" * 10)
            if on_stdout and "-progress" in argv:
                for us in (5_000_000, 13_100_000, 26_200_000):
                    on_stdout(f"out_time_us={us}")
                on_stdout("progress=end")
            return ProcessResult(0, "", "")
        if exe.startswith("gallery-dl"):
            return ProcessResult(4, "", "[instagram][error] HTTP redirect to login page (https://www.instagram.com/accounts/login/)")
        # yt-dlp
        if "-J" in argv:
            kind, stderr = self.probe_outcomes.pop(0) if self.probe_outcomes else ("ok", None)
            if kind == "block":
                while not cancel.is_set():
                    time.sleep(0.02)
                return ProcessResult(None, "", "", canceled=True)
            if kind == "fail":
                return ProcessResult(1, "", stderr)
            return ProcessResult(0, json.dumps(INFO), "")
        if "--load-info-json" in argv:
            out = argv[argv.index("-o") + 1].replace("%(ext)s", "mp4")
            for line in ('[[IDLP]]{"s":"downloading","d":100,"t":1000,"te":NA,"sp":5000,"eta":1,"f":"dash-1080","vc":"vp09"}',
                         '[[IDLP]]{"s":"finished","d":1000,"t":1000,"te":NA,"sp":5000,"eta":NA,"f":"dash-1080","vc":"vp09"}',
                         '[[IDLP]]{"s":"finished","d":50,"t":50,"te":NA,"sp":NA,"eta":NA,"f":"dash-a","vc":"none"}'):
                on_stdout(line)
            on_stderr('[[IDLPP]]{"s":"started","pp":"Merger"}')
            with open(out, "wb") as fh:
                fh.write(b"VP9" * 100)
            on_stderr('[[IDLPP]]{"s":"finished","pp":"Merger"}')
            on_stdout('[[IDLFILE]]' + json.dumps(out))
            return ProcessResult(0, "", "")
        return ProcessResult(1, "", "unexpected call")


class PipelineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="idl_pipe_")
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
        self.heals = 0

    def tearDown(self):
        os.environ.pop("MEDIA_SAVER_HOME", None)
        for _ in range(10):  # a background history save may re-create home/ right after the removal
            shutil.rmtree(self.tmp, ignore_errors=True)
            time.sleep(0.1)
            if not os.path.exists(self.tmp):
                break

    def make(self, runner, **patch):
        self.settings.update(patch)

        def heal():
            self.heals += 1
            return self.heals == 1

        def sleep(ev, s):
            self.sleeps.append(s)
            return ev.is_set()

        return Downloader(self.paths, lambda: dict(self.settings), lambda t, p: self.events.append((t, p)),
                          binaries=lambda: dict(self.bins), usable=lambda: True, self_heal=heal,
                          runner=runner, fetch=lambda url, headers=None: b"\xff\xd8jpeg", sleep=sleep)

    def run_one(self, dl, url="https://www.instagram.com/reel/DQCkNLtgqEe", code="DQCkNLtgqEe", platform="instagram",
                kind="reel", fmt="mp4"):
        jd = dl.add(url, code, platform, kind, fmt)
        job = dl.get(jd["id"])
        dl._run_job(job)
        return job

    def write_session(self):
        auth.write_netscape_cookies([{"name": "sessionid", "value": "S", "domain": ".instagram.com", "path": "/",
                                      "secure": True, "http_only": True, "expires": time.time() + 9999},
                                     {"name": "ds_user_id", "value": "9", "domain": ".instagram.com", "path": "/",
                                      "secure": True, "http_only": False, "expires": None}], self.paths.cookies_file)

    def test_anonymous_success_with_transcode(self):
        runner = FakeRunner([("ok", None)])
        dl = self.make(runner)
        job = self.run_one(dl)
        self.assertEqual(job.status, "done", job.error)
        self.assertEqual(job.filepath, os.path.join(self.out, "teddysphotos_DQCkNLtgqEe.mp4"))
        self.assertTrue(os.path.isfile(job.filepath))
        self.assertTrue(job.transcoded)
        self.assertEqual((job.width, job.height, job.fps, job.vcodec, job.acodec), (1080, 1920, 30, "h264", "aac"))
        self.assertEqual(job.attempt, {"engine": "yt-dlp", "auth": "anonymous"})
        self.assertTrue(job.thumbnail.startswith("data:image/jpeg;base64,"))
        self.assertEqual(job.title, "Caption line")
        # chosen formats: best VP9 + audio, overall progress monotonic, public payload shape
        dl_call = next(c for c in runner.calls if "--load-info-json" in c)
        self.assertTrue(dl_call[dl_call.index("-f") + 1].startswith("dash-1080+dash-a/"))
        progress = [p["progress"] for t, p in self.events if t == "job"]
        self.assertEqual(progress, sorted(progress))
        payload = [p for t, p in self.events if t == "job"][-1]
        self.assertEqual(set(payload), {"id", "url", "shortcode", "platform", "kind", "format", "status", "stage_text",
                                        "progress", "speed_bps", "eta_s", "previewed", "title", "author", "duration_s",
                                        "thumbnail", "width", "height", "fps", "vcodec", "acodec", "filesize",
                                        "filepath", "already_existed", "transcoded", "attempt", "error", "created_at",
                                        "finished_at"})
        self.assertTrue(payload["previewed"])
        self.assertFalse(os.path.exists(self.paths.job_cache(job.id) + "x"))

    def test_skip_existing(self):
        os.makedirs(self.out)
        existing = os.path.join(self.out, "teddysphotos_DQCkNLtgqEe.mp4")
        with open(existing, "wb") as fh:
            fh.write(b"old")
        runner = FakeRunner([("ok", None)])
        job = self.run_one(self.make(runner))
        self.assertEqual((job.status, job.already_existed, job.stage_text), ("done", True, "Already downloaded"))
        self.assertFalse(any("--load-info-json" in c for c in runner.calls))

    def test_no_skip_makes_unique_name(self):
        os.makedirs(self.out)
        with open(os.path.join(self.out, "teddysphotos_DQCkNLtgqEe.mp4"), "wb") as fh:
            fh.write(b"old")
        job = self.run_one(self.make(FakeRunner([("ok", None)]), skip_existing=False))
        self.assertTrue(job.filepath.endswith("teddysphotos_DQCkNLtgqEe (2).mp4"))

    def test_login_required_without_session(self):
        runner = FakeRunner([("fail", EMPTY_MEDIA)])
        job = self.run_one(self.make(runner))
        self.assertEqual(job.status, "error")
        self.assertEqual(job.error["code"], "login_required")
        self.assertTrue(job.error["can_login"])
        self.assertTrue(os.path.isfile(job.error["log_path"]))
        with open(job.error["log_path"], encoding="utf-8") as fh:
            self.assertIn("empty media response", fh.read())
        self.assertEqual(sum(1 for c in runner.calls if "-J" in c), 1)

    def test_session_rung_after_anonymous(self):
        self.write_session()
        runner = FakeRunner([("fail", EMPTY_MEDIA), ("ok", None)])
        job = self.run_one(self.make(runner))
        self.assertEqual(job.status, "done", job.error)
        self.assertEqual(job.attempt, {"engine": "yt-dlp", "auth": "session"})
        probes = [c for c in runner.calls if "-J" in c]
        self.assertNotIn("--cookies", probes[0])
        jar = probes[1][probes[1].index("--cookies") + 1]
        self.assertNotEqual(os.path.abspath(jar), os.path.abspath(self.paths.cookies_file))  # per-job copy
        self.assertIn(os.path.join("cache", "jobs"), jar)

    def test_ladder_down_to_gallerydl(self):
        self.write_session()
        runner = FakeRunner([("fail", EMPTY_MEDIA), ("fail", EMPTY_MEDIA)])
        job = self.run_one(self.make(runner))
        self.assertEqual(job.error["code"], "login_required")
        self.assertTrue(any(os.path.basename(c[0]).startswith("gallery-dl") for c in runner.calls))
        self.assertIn("again", job.error["message"])

    def test_rate_limit_backoff(self):
        runner = FakeRunner([("fail", RATE), ("fail", RATE), ("fail", RATE)])
        job = self.run_one(self.make(runner))
        self.assertEqual(self.sleeps, [30.0, 90.0])
        self.assertEqual(job.error["code"], "rate_limited")
        self.assertTrue(job.error["can_login"])

    def test_rate_limit_then_success(self):
        job = self.run_one(self.make(FakeRunner([("fail", RATE), ("ok", None)])))
        self.assertEqual(job.status, "done")
        self.assertEqual(self.sleeps, [30.0])

    def test_self_heal_once(self):
        runner = FakeRunner([("fail", EXTRACT), ("ok", None)])
        job = self.run_one(self.make(runner))
        self.assertEqual(job.status, "done")
        self.assertEqual(self.heals, 1)
        # a second job with an extractor error: the session update is spent, no retry
        runner2 = FakeRunner([("fail", EXTRACT), ("ok", None)])
        dl2 = self.make(runner2)
        job2 = self.run_one(dl2, "https://www.instagram.com/reel/AAA", "AAA")
        self.assertEqual(job2.error["code"], "extractor")
        self.assertEqual(self.heals, 2)

    def test_engine_missing(self):
        self.bins["yt-dlp"] = None
        job = self.run_one(self.make(FakeRunner([])))
        self.assertEqual(job.error["code"], "engine_missing")

    def test_cancel_running_job(self):
        runner = FakeRunner([("block", None)])
        dl = self.make(runner)
        jd = dl.add("https://www.instagram.com/reel/DQCkNLtgqEe", "DQCkNLtgqEe", "instagram", "reel", "mp4")
        job = dl.get(jd["id"])
        t = threading.Thread(target=dl._run_job, args=(job,))
        t.start()
        time.sleep(0.2)
        self.assertTrue(dl.cancel(job.id))
        t.join(5)
        self.assertEqual(job.status, "canceled")
        self.assertEqual(job.error["code"], "canceled")
        self.assertTrue(dl.retry(job.id))
        self.assertEqual(job.status, "queued")

    def test_queue_workers_and_drain(self):
        drained = threading.Event()
        runner = FakeRunner([("ok", None), ("ok", None)])
        dl = self.make(runner)
        dl.on_queue_drained = lambda done, failed: drained.set()
        dl.start()
        dl.add("https://www.instagram.com/reel/DQCkNLtgqEe", "DQCkNLtgqEe", "instagram", "reel", "mp4")
        dl.add("https://www.instagram.com/reel/BBB", "BBB", "instagram", "reel", "mp4")
        self.assertTrue(drained.wait(10))
        statuses = sorted(j["status"] for j in dl.jobs())
        self.assertEqual(statuses, ["done", "done"])
        dl.shutdown(1)


class GateTest(unittest.TestCase):
    def test_replacement_waits_for_users_and_blocks_new_ones(self):
        from app.core.downloader import BinaryGate
        gate = BinaryGate()
        self.assertTrue(gate.acquire_shared())
        got = threading.Event()
        t = threading.Thread(target=lambda: (gate.acquire_exclusive(5) and got.set()))
        t.start()
        time.sleep(0.2)
        self.assertFalse(got.is_set())       # a job still runs yt-dlp
        self.assertTrue(gate.replacing)      # new jobs are held back meanwhile
        cancel = threading.Event()
        cancel.set()
        self.assertFalse(gate.acquire_shared(cancel))
        gate.release_shared()
        t.join(2)
        self.assertTrue(got.is_set())
        gate.release_exclusive()
        self.assertTrue(gate.acquire_shared())
        gate.release_shared()

    def test_exclusive_timeout(self):
        from app.core.downloader import BinaryGate
        gate = BinaryGate()
        gate.acquire_shared()
        self.assertFalse(gate.acquire_exclusive(0.3))
        self.assertFalse(gate.replacing)


class FormatSelectionTest(unittest.TestCase):
    def test_max_prefers_resolution_then_fps_then_bitrate(self):
        c = select_formats(INFO, "instagram", "max", False)
        self.assertEqual(c.video["format_id"], "dash-1080")
        self.assertEqual(c.audio["format_id"], "dash-a")
        self.assertFalse(c.needs_transcode)
        c = select_formats(INFO, "instagram", "max", True)
        self.assertEqual(c.video["format_id"], "dash-1080")
        self.assertTrue(c.needs_transcode)

    def test_caps(self):
        self.assertEqual(select_formats(INFO, "instagram", "low", False).video["format_id"], "dash-360")
        self.assertEqual(select_formats(INFO, "instagram", "medium", False).video["format_id"], "dash-720")
        self.assertEqual(select_formats(INFO, "instagram", "max", False).video["format_id"], "dash-1080")
        # tolerance: 724x1288 (short side 724 <= 720 x 1.02) still passes "medium"
        info = dict(INFO, formats=INFO["formats"] + [
            {"format_id": "dash-724", "vcodec": "vp09", "acodec": "none", "width": 724, "height": 1288, "tbr": 100}])
        self.assertEqual(select_formats(info, "instagram", "medium", False).video["format_id"], "dash-724")
        # 1088x1936 is the top stream of some reels and wins "max"
        info = dict(INFO, formats=INFO["formats"] + [
            {"format_id": "dash-1088", "vcodec": "vp09", "acodec": "none", "width": 1088, "height": 1936, "tbr": 100}])
        self.assertEqual(select_formats(info, "instagram", "max", False).video["format_id"], "dash-1088")

    def test_avc_at_same_resolution_wins_with_h264(self):
        info = dict(INFO, formats=INFO["formats"] + [
            {"format_id": "avc-1080", "vcodec": "avc1.64001F", "acodec": "none", "width": 1080, "height": 1920, "tbr": 900}])
        c = select_formats(info, "instagram", "max", True)
        self.assertEqual(c.video["format_id"], "avc-1080")
        self.assertFalse(c.needs_transcode)
        self.assertEqual(select_formats(info, "instagram", "max", False).video["format_id"], "dash-1080")  # higher bitrate

    def test_lower_avc_is_not_preferred(self):
        info = dict(INFO, formats=INFO["formats"] + [
            {"format_id": "avc-720", "vcodec": "avc1.4d401f", "acodec": "none", "width": 720, "height": 1280, "tbr": 3000}])
        self.assertEqual(select_formats(info, "instagram", "max", True).video["format_id"], "dash-1080")

    def test_no_audio_and_no_metadata(self):
        info = {"formats": [{"format_id": "v", "vcodec": "avc1", "acodec": "none", "width": 720, "height": 1280}]}
        c = select_formats(info, "instagram", "max", True)
        self.assertIsNone(c.audio)
        self.assertTrue(c.spec.startswith("v/"))
        c = select_formats({"formats": [{"format_id": "0", "vcodec": None}]}, "instagram", "medium", True)
        self.assertEqual(c.spec, "bv*+ba/b")
        self.assertIn("res:720", c.sort_args[1])
        c = select_formats({"formats": [{"format_id": "0", "vcodec": None}]}, "instagram", "low", True)
        self.assertIn("res:480", c.sort_args[1])


if __name__ == "__main__":
    unittest.main()
