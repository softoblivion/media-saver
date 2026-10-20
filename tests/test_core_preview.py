"""Preview worker (ARCHITECTURE.md 5.4) with a scripted runner: fetch, reuse by the download, failure, cancel."""
import os
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core_fakes import IG_INFO, YT_INFO, CoreTestBase, ScriptedRunner  # noqa: E402

YT_URL = "https://www.youtube.com/watch?v=aqz-KE-bpKQ"
IG_URL = "https://www.instagram.com/reel/DQCkNLtgqEe"


class PreviewTest(CoreTestBase, unittest.TestCase):
    def add_yt(self, dl, fmt="mp4"):
        return dl.get(dl.add(YT_URL, "aqz-KE-bpKQ", "youtube", "video", fmt)["id"])

    def preview_once(self, dl):
        job = dl._next_preview()
        dl._run_preview(job, dl._preview_cancel)
        dl._preview_id = None
        return job

    def preview_file(self, job):
        return os.path.join(self.paths.jobs_cache_dir, job.id, "preview.json")

    def test_success_fills_fields_and_download_reuses_it(self):
        runner = ScriptedRunner(YT_INFO)
        dl = self.make(runner, browser_cookies="firefox")
        job = self.add_yt(dl)
        self.assertFalse(job.previewed)
        self.assertIs(self.preview_once(dl), job)
        self.assertTrue(job.previewed)
        self.assertEqual(job.status, "queued")
        self.assertEqual((job.title, job.author, job.duration_s),
                         ("Big Buck Bunny 60fps 4K - Official Blender Foundation Short Film", "Blender", 635.0))
        self.assertTrue(job.thumbnail.startswith("data:image/jpeg;base64,"))
        self.assertTrue(os.path.isfile(self.preview_file(job)))
        probe = runner.ytdlp_calls("-J")[0]
        self.assertIn("--no-playlist", probe)
        self.assertNotIn("--cookies-from-browser", probe)  # the preview never uses cookies
        self.assertNotIn("--cookies", probe)
        events = self.job_events(job.id)
        self.assertTrue(any(p["title"] and not p["previewed"] for p in events))  # fields arrive first
        self.assertTrue(events[-1]["previewed"])

        thumb_fetches = len([c for c in runner.calls if os.path.basename(c[0]).startswith("ffmpeg")])
        dl._run_job(job)
        self.assertEqual(job.status, "done", job.error)
        self.assertEqual(len(runner.ytdlp_calls("-J")), 1)  # the download did not probe again
        self.assertEqual(len(runner.ytdlp_calls("--load-info-json")), 1)
        self.assertEqual(len([c for c in runner.calls if os.path.basename(c[0]).startswith("ffmpeg")]),
                         thumb_fetches)  # thumbnail not fetched twice
        self.assertFalse(os.path.exists(os.path.join(self.paths.jobs_cache_dir, job.id)))  # cache removed

    def test_stale_preview_is_not_reused(self):
        runner = ScriptedRunner(YT_INFO)
        dl = self.make(runner)
        job = self.add_yt(dl)
        self.preview_once(dl)
        job.preview_at = time.time() - dl.PREVIEW_MAX_AGE - 5
        dl._run_job(job)
        self.assertEqual(job.status, "done", job.error)
        self.assertEqual(len(runner.ytdlp_calls("-J")), 2)

    def test_failure_is_silent(self):
        runner = ScriptedRunner(YT_INFO, probes=[("fail", "ERROR: [youtube] aqz-KE-bpKQ: Video unavailable")])
        dl = self.make(runner)
        job = self.add_yt(dl)
        self.preview_once(dl)
        self.assertTrue(job.previewed)
        self.assertEqual(job.status, "queued")
        self.assertIsNone(job.error)
        self.assertEqual((job.title, job.author, job.thumbnail), (None, None, None))
        self.assertFalse(os.path.exists(self.preview_file(job)))
        self.assertFalse([t for t, p in self.events if t == "toast"])
        dl._run_job(job)  # the download probes by itself
        self.assertEqual(job.status, "done", job.error)
        self.assertEqual(len(runner.ytdlp_calls("-J")), 2)

    def test_timeout_is_silent(self):
        runner = ScriptedRunner(YT_INFO)
        dl = self.make(runner)
        job = self.add_yt(dl)
        orig = runner.run

        def timed_out(argv, **kw):
            if "-J" in argv:
                self.assertEqual(kw.get("timeout"), dl.PREVIEW_TIMEOUT)
                from app.core.downloader import ProcessResult
                return ProcessResult(None, "", "", timed_out=True)
            return orig(argv, **kw)

        runner.run = timed_out
        self.preview_once(dl)
        self.assertTrue(job.previewed)
        self.assertIsNone(job.title)

    def test_cancel_kills_the_preview(self):
        runner = ScriptedRunner(YT_INFO, probes=[("block", None)])
        dl = self.make(runner)
        job = self.add_yt(dl)
        picked = dl._next_preview()
        t = threading.Thread(target=dl._run_preview, args=(picked, dl._preview_cancel))
        t.start()
        self.assertTrue(runner.probe_started.wait(5))
        self.assertTrue(dl.cancel(job.id))
        t.join(5)
        self.assertFalse(t.is_alive())
        self.assertEqual(job.status, "canceled")
        self.assertTrue(job.previewed)
        self.assertIsNone(job.title)
        self.assertFalse(os.path.exists(os.path.join(self.paths.jobs_cache_dir, job.id)))

    def test_remove_kills_the_preview(self):
        runner = ScriptedRunner(YT_INFO, probes=[("block", None)])
        dl = self.make(runner)
        job = self.add_yt(dl)
        picked = dl._next_preview()
        t = threading.Thread(target=dl._run_preview, args=(picked, dl._preview_cancel))
        t.start()
        self.assertTrue(runner.probe_started.wait(5))
        self.assertTrue(dl.remove(job.id))
        t.join(5)
        self.assertFalse(t.is_alive())
        self.assertFalse(os.path.exists(os.path.join(self.paths.jobs_cache_dir, job.id)))

    def test_download_start_does_not_wait_for_the_preview(self):
        runner = ScriptedRunner(YT_INFO, probes=[("block", None), ("ok", None)])
        dl = self.make(runner)
        job = self.add_yt(dl)
        picked = dl._next_preview()
        t = threading.Thread(target=dl._run_preview, args=(picked, dl._preview_cancel))
        t.start()
        self.assertTrue(runner.probe_started.wait(5))
        self.assertIs(dl._next_job(), job)  # the worker takes the job: the preview is stopped
        t.join(5)
        self.assertFalse(t.is_alive())
        self.assertIsNone(job.title)  # the stopped preview changed nothing
        dl._run_job(job)
        self.assertEqual(job.status, "done", job.error)
        self.assertTrue(job.previewed)
        self.assertEqual(len(runner.ytdlp_calls("-J")), 2)

    def test_worker_thread_waits_for_engine_and_gate(self):
        runner = ScriptedRunner(IG_INFO)
        dl = self.make(runner, concurrency=1)
        self.usable = False
        job = dl.get(dl.add(IG_URL, "DQCkNLtgqEe", "instagram", "reel", "mp4")["id"])
        t = threading.Thread(target=dl._preview_worker, daemon=True)
        t.start()
        time.sleep(0.4)
        self.assertEqual(runner.ytdlp_calls("-J"), [])  # engine not usable yet
        self.assertTrue(dl.gate.acquire_exclusive(1))  # the updater replaces binaries
        self.usable = True
        with dl._cond:
            dl._cond.notify_all()
        time.sleep(0.4)
        self.assertEqual(runner.ytdlp_calls("-J"), [])
        dl.gate.release_exclusive()
        deadline = time.monotonic() + 5
        while not job.previewed and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertTrue(job.previewed)
        self.assertEqual(job.author, "teddysphotos")
        self.assertEqual(job.title, "Caption line")
        self.assertEqual(len(runner.ytdlp_calls("-J")), 1)
        self.assertNotIn("--no-playlist", runner.ytdlp_calls("-J")[0])  # YouTube only
        dl.shutdown(1)
        t.join(2)
        self.assertFalse(t.is_alive())

    def test_serial_one_job_at_a_time(self):
        runner = ScriptedRunner(YT_INFO, probes=[("slow", 0.2), ("slow", 0.2), ("slow", 0.2)])
        active = []
        peak = []
        orig = runner.run

        def counting(argv, **kw):
            if "-J" in argv:
                active.append(1)
                peak.append(len(active))
                try:
                    return orig(argv, **kw)
                finally:
                    active.pop()
            return orig(argv, **kw)

        runner.run = counting
        dl = self.make(runner)
        jobs = [dl.get(dl.add(f"https://www.youtube.com/watch?v=aqz-KE-bpK{i}", f"aqz-KE-bpK{i}", "youtube",
                              "video", "mp4")["id"]) for i in range(3)]
        t = threading.Thread(target=dl._preview_worker, daemon=True)
        t.start()
        deadline = time.monotonic() + 5
        while not all(j.previewed for j in jobs) and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertTrue(all(j.previewed for j in jobs))
        self.assertEqual(max(peak), 1)
        order = [c[-1] for c in runner.ytdlp_calls("-J")]
        self.assertEqual(order, [j.url for j in jobs])  # oldest first
        dl.shutdown(1)


if __name__ == "__main__":
    unittest.main()
