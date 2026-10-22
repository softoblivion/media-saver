"""Preview workers (ARCHITECTURE.md 5.4) with a scripted runner: fetch, reuse by the download, failure, cancel,
the parallel pool, its order and the rule that a preview never empties or replaces what the download's probe set."""
import os
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core_fakes import IG_INFO, YT_INFO, CoreTestBase, ScriptedRunner  # noqa: E402

YT_URL = "https://www.youtube.com/watch?v=aqz-KE-bpKQ"
IG_URL = "https://www.instagram.com/reel/DQCkNLtgqEe"
YT_TITLE = "Big Buck Bunny 60fps 4K - Official Blender Foundation Short Film"


class PreviewTest(CoreTestBase, unittest.TestCase):
    def add_yt(self, dl, fmt="mp4"):
        return dl.get(dl.add(YT_URL, "aqz-KE-bpKQ", "youtube", "video", fmt)["id"])

    def preview_once(self, dl):
        job, cancel = dl._next_preview()
        try:
            dl._run_preview(job, cancel)
        finally:
            dl._preview_release(job.id)
        return job

    def start_preview(self, dl):
        """Pick the next job and run its preview in a thread (as one pool worker does); returns (job, thread)."""
        job, cancel = dl._next_preview()

        def run():
            try:
                dl._run_preview(job, cancel)
            finally:
                dl._preview_release(job.id)

        t = threading.Thread(target=run, daemon=True)
        t.start()
        return job, t

    def start_pool(self, dl, n=None):
        threads = [threading.Thread(target=dl._preview_worker, daemon=True) for _ in range(n or dl.PREVIEW_WORKERS)]
        for t in threads:
            t.start()
        return threads

    def wait(self, predicate, timeout=5.0):
        deadline = time.monotonic() + timeout
        while not predicate() and time.monotonic() < deadline:
            time.sleep(0.02)
        return predicate()

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
        self.assertEqual((job.title, job.author, job.duration_s), (YT_TITLE, "Blender", 635.0))
        self.assertTrue(job.thumbnail.startswith("data:image/jpeg;base64,"))
        self.assertTrue(os.path.isfile(self.preview_file(job)))
        probe = runner.ytdlp_calls("-J")[0]
        self.assertIn("--no-playlist", probe)
        self.assertNotIn("--cookies-from-browser", probe)  # the preview never uses cookies
        self.assertNotIn("--cookies", probe)
        events = self.job_events(job.id)
        self.assertTrue(any(p["title"] and not p["previewed"] for p in events))  # fields arrive first
        self.assertTrue(events[-1]["previewed"])
        self.assertEqual(dl._previews, {})

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
        picked, t = self.start_preview(dl)
        self.assertIs(picked, job)
        self.assertTrue(runner.probe_started.wait(5))
        self.assertTrue(dl.cancel(job.id))
        t.join(5)
        self.assertFalse(t.is_alive())
        self.assertEqual(job.status, "canceled")
        self.assertTrue(job.previewed)
        self.assertIsNone(job.title)
        self.assertFalse(os.path.exists(os.path.join(self.paths.jobs_cache_dir, job.id)))
        self.assertEqual(dl._previews, {})

    def test_remove_kills_the_preview(self):
        runner = ScriptedRunner(YT_INFO, probes=[("block", None)])
        dl = self.make(runner)
        job = self.add_yt(dl)
        self.start_preview(dl)
        self.assertTrue(runner.probe_started.wait(5))
        self.assertTrue(dl.remove(job.id))
        self.assertTrue(self.wait(lambda: dl._previews == {}))
        self.assertFalse(os.path.exists(os.path.join(self.paths.jobs_cache_dir, job.id)))

    def test_download_start_drops_a_preview_that_has_not_started(self):
        runner = ScriptedRunner(YT_INFO)
        dl = self.make(runner)
        job = self.add_yt(dl)
        picked, cancel = dl._next_preview()  # picked, but its subprocess has not started yet
        self.assertIs(dl._next_job(), job)
        self.assertTrue(cancel.is_set())
        dl._run_preview(picked, cancel)
        dl._preview_release(picked.id)
        self.assertEqual(runner.ytdlp_calls("-J"), [])  # no preview subprocess at all
        self.assertIsNone(job.title)
        dl._run_job(job)
        self.assertEqual(job.status, "done", job.error)
        self.assertTrue(job.previewed)
        self.assertEqual(len(runner.ytdlp_calls("-J")), 1)

    def test_running_preview_finishes_when_the_download_starts(self):
        release = threading.Event()
        runner = ScriptedRunner(YT_INFO, probes=[("wait", release), ("ok", None)])
        dl = self.make(runner)
        job = self.add_yt(dl)
        _, t = self.start_preview(dl)
        self.assertTrue(runner.probe_started.wait(5))
        self.assertIs(dl._next_job(), job)  # the worker takes the job while the preview subprocess runs
        self.assertFalse(dl._previews[job.id]["cancel"].is_set())  # the fetch is not killed
        self.assertFalse(job.previewed)
        release.set()  # the preview result arrives before the download's own probe
        t.join(5)
        self.assertFalse(t.is_alive())
        self.assertEqual((job.title, job.author, job.duration_s), (YT_TITLE, "Blender", 635.0))
        self.assertTrue(job.thumbnail)
        self.assertTrue(job.previewed)
        self.assertIsNone(job.preview_path)  # the job had left the queue: the download probes by itself
        dl._run_job(job)
        self.assertEqual(job.status, "done", job.error)
        self.assertEqual(job.title, YT_TITLE)
        self.assertEqual(len(runner.ytdlp_calls("-J")), 2)

    def test_late_preview_does_not_replace_what_the_probe_set(self):
        release = threading.Event()
        runner = ScriptedRunner(YT_INFO, probes=[("wait", release)])
        dl = self.make(runner)
        job = self.add_yt(dl)
        _, t = self.start_preview(dl)
        self.assertTrue(runner.probe_started.wait(5))
        self.assertIs(dl._next_job(), job)
        with dl._lock:  # the download's probe answered first (a signed-in rung may see another title)
            dl._fill_meta(job, {"title": "Probe title", "author": None, "duration_s": 12.0}, overwrite=True)
        release.set()
        t.join(5)
        self.assertEqual(job.title, "Probe title")
        self.assertEqual(job.duration_s, 12.0)
        self.assertEqual(job.author, "Blender")  # the probe had no author: the preview fills it
        self.assertTrue(job.previewed)

    def test_probe_never_empties_the_preview_fields(self):
        bare = dict(YT_INFO, title="", fulltitle=None, duration=None)
        runner = ScriptedRunner(YT_INFO, probes=[("ok", None), ("ok", bare)])
        dl = self.make(runner)
        job = self.add_yt(dl)
        self.preview_once(dl)
        job.preview_at = time.time() - dl.PREVIEW_MAX_AGE - 5  # force the download's own probe
        dl._run_job(job)
        self.assertEqual(job.status, "done", job.error)
        self.assertEqual(len(runner.ytdlp_calls("-J")), 2)
        self.assertEqual((job.title, job.duration_s), (YT_TITLE, 635.0))
        self.assertTrue(all(p["title"] == YT_TITLE for p in self.job_events(job.id) if p["previewed"]))

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
        self.assertTrue(self.wait(lambda: job.previewed))
        self.assertEqual(job.author, "teddysphotos")
        self.assertEqual(job.title, "Caption line")
        self.assertEqual(len(runner.ytdlp_calls("-J")), 1)
        self.assertNotIn("--no-playlist", runner.ytdlp_calls("-J")[0])  # YouTube only
        dl.shutdown(1)
        t.join(2)
        self.assertFalse(t.is_alive())

    def add_many(self, dl, n):
        """n jobs from one paste; returns them top to bottom (paste order)."""
        items = [(f"https://www.youtube.com/watch?v=aqz-KE-bpK{i}", f"aqz-KE-bpK{i}", "youtube", "video", "mp4")
                 for i in range(n)]
        return [dl.get(d["id"]) for d in dl.add_batch(items)]

    def test_one_worker_goes_top_to_bottom(self):
        runner = ScriptedRunner(YT_INFO)
        dl = self.make(runner)
        older = dl.get(dl.add("https://www.youtube.com/watch?v=olderxxxxxx", "olderxxxxxx", "youtube", "video",
                              "mp4")["id"])
        batch = self.add_many(dl, 3)
        threads = self.start_pool(dl, 1)
        self.assertTrue(self.wait(lambda: older.previewed and all(j.previewed for j in batch)))
        order = [c[-1] for c in runner.ytdlp_calls("-J")]
        self.assertEqual(order, [j.url for j in batch] + [older.url])  # the list order, top first
        self.assertEqual(order, [j["url"] for j in dl.jobs()])
        dl.shutdown(1)
        for t in threads:
            t.join(2)

    def test_pool_fetches_in_parallel(self):
        runner = ScriptedRunner(YT_INFO, probes=[("slow", 0.4)] * 4)
        active, peak, lock = [0], [0], threading.Lock()
        orig = runner.run

        def counting(argv, **kw):
            if "-J" not in argv:
                return orig(argv, **kw)
            with lock:
                active[0] += 1
                peak[0] = max(peak[0], active[0])
            try:
                return orig(argv, **kw)
            finally:
                with lock:
                    active[0] -= 1

        runner.run = counting
        dl = self.make(runner)
        self.assertEqual(dl.PREVIEW_WORKERS, 3)
        jobs = self.add_many(dl, 4)
        started = time.monotonic()
        threads = self.start_pool(dl)
        self.assertTrue(self.wait(lambda: all(j.previewed for j in jobs)))
        elapsed = time.monotonic() - started
        self.assertEqual(peak[0], 3)            # three fetches at once, never more
        self.assertLess(elapsed, 1.5)           # serially four fetches would take 1.6 s and more
        self.assertEqual(len(runner.ytdlp_calls("-J")), 4)  # each job fetched once
        self.assertEqual({c[-1] for c in runner.ytdlp_calls("-J")[:3]}, {j.url for j in jobs[:3]})  # top three first
        self.assertTrue(all(j.title == YT_TITLE for j in jobs))
        dl.shutdown(1)
        for t in threads:
            t.join(2)
            self.assertFalse(t.is_alive())

    def test_shutdown_stops_the_pool_and_running_fetches(self):
        runner = ScriptedRunner(YT_INFO, probes=[("block", None)] * 3)
        dl = self.make(runner)
        jobs = self.add_many(dl, 3)
        self.usable = False  # the download workers of start() stay idle
        dl.start()
        names = [t.name for t in dl._threads]
        self.assertEqual(len([n for n in names if n.startswith("ms-preview-")]), dl.PREVIEW_WORKERS)
        with dl._cond:
            dl._concurrency = 0  # keep the job workers out of the way
        self.usable = True  # the previews start
        with dl._cond:
            dl._cond.notify_all()
        self.assertTrue(self.wait(lambda: len(runner.ytdlp_calls("-J")) == 3))
        dl.shutdown(2)
        for t in dl._threads:
            self.assertFalse(t.is_alive(), t.name)
        self.assertTrue(all(j.title is None for j in jobs))  # killed fetches change nothing


if __name__ == "__main__":
    unittest.main()
