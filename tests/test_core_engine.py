"""Engine facade shape and enqueue rules (no network: the updater is never started)."""
import importlib.util
import logging
import os
import shutil
import tempfile
import threading
import time
import unittest

from app.core.engine import Engine

HAS_LINKCLEAN = importlib.util.find_spec("app.core.linkclean") is not None


class EngineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ms_engine_")
        self._old_home = os.environ.get("MEDIA_SAVER_HOME")
        os.environ["MEDIA_SAVER_HOME"] = os.path.join(self.tmp, "home")
        self.events = []
        self.engine = Engine(lambda t, p: self.events.append((t, p)), "9.9.9", use_system_ffmpeg=False,
                             app_updates=False)

    def tearDown(self):
        self.engine.downloader.shutdown(0.5)
        if self._old_home is None:
            os.environ.pop("MEDIA_SAVER_HOME", None)
        else:
            os.environ["MEDIA_SAVER_HOME"] = self._old_home
        # the rotating app.log handler keeps a file open inside the scratch folder; close it before removing
        root = logging.getLogger("mediasaver")
        for h in list(root.handlers):
            root.removeHandler(h)
            h.close()
        for _ in range(10):
            shutil.rmtree(self.tmp, ignore_errors=True)
            if not os.path.exists(self.tmp):
                break
            time.sleep(0.1)

    def test_periodic_update_check_runs_while_alive_and_stops_on_shutdown(self):
        calls = []
        self.engine.update_scheduler._period_override = 0.05
        self.engine.selfupdate.check_background = lambda: calls.append(1)
        self.engine.selfupdate.status = lambda: {"stage": "up_to_date"}
        self.engine.update_scheduler.start()
        time.sleep(0.3)
        self.engine.shutdown()
        self.engine.update_scheduler._thread.join(1.0)
        self.assertFalse(self.engine.update_scheduler._thread.is_alive())
        self.assertGreaterEqual(len(calls), 2)
        n = len(calls)
        time.sleep(0.15)
        self.assertEqual(len(calls), n)

    def test_scheduler_has_a_fixed_five_minute_period_and_no_interval_setting(self):
        from app.vendor.selfupdate import CHECK_JITTER, CHECK_PERIOD
        self.assertEqual(CHECK_PERIOD, 300)
        sched = self.engine.update_scheduler
        self.assertFalse(hasattr(sched, "interval_key"))
        self.assertFalse(hasattr(sched, "set_interval"))
        self.assertNotIn("update_check_interval", self.engine.get_settings())
        self.assertTrue(self.engine.get_settings()["auto_download_updates"])
        self.assertIsNone(sched.next_check_at)
        before = time.time()
        sched.start()
        try:
            after = time.time()
            nxt = sched.next_check_at
            self.assertIsNotNone(nxt)
            self.assertGreaterEqual(nxt, before + CHECK_PERIOD - CHECK_JITTER - 1)
            self.assertLessEqual(nxt, after + CHECK_PERIOD + CHECK_JITTER + 1)
        finally:
            sched.stop()
        # a stored value of an older version is ignored, not an error
        self.assertNotIn("update_check_interval", self.engine.set_settings({"update_check_interval": "1d"}))

    def _stub_updater(self, stage="available"):
        """Replace the self-updater's status / download with a stub; returns (calls, state)."""
        calls, state = [], {"stage": stage}
        self.engine.selfupdate.status = lambda: {"stage": state["stage"], "latest": "9.10.0"}

        def download_background():
            calls.append(1)
            state["stage"] = "downloading"

        self.engine.selfupdate.download_background = download_background
        return calls, state

    def _wait(self, predicate, timeout=2.0):
        deadline = time.time() + timeout
        while time.time() < deadline and not predicate():
            time.sleep(0.02)
        return predicate()

    def test_update_status_is_the_self_updater_status(self):
        self._stub_updater("up_to_date")
        self.assertEqual(self.engine.update_status()["stage"], "up_to_date")

    def test_found_version_downloads_once_in_the_background(self):
        self.assertTrue(self.engine.get_settings()["auto_download_updates"])
        calls, state = self._stub_updater()
        self.engine._on_update_status({"stage": "available", "latest": "9.10.0"})
        self.assertTrue(self._wait(lambda: len(calls) == 1))
        # the same version found again (a later check, or after a failed download): no second download
        state["stage"] = "available"
        self.engine._on_update_status({"stage": "available", "latest": "9.10.0"})
        time.sleep(0.3)
        self.assertEqual(len(calls), 1)
        self.assertEqual([t for t, _ in self.events].count("update"), 2)

    def test_no_background_download_while_off_and_one_when_turned_on(self):
        self.engine.set_settings({"auto_download_updates": False})
        calls, _ = self._stub_updater()
        self.engine._on_update_status({"stage": "available", "latest": "9.10.0"})
        time.sleep(0.3)
        self.assertEqual(calls, [])
        self.engine.set_settings({"auto_download_updates": True})
        self.assertTrue(self._wait(lambda: len(calls) == 1))

    def test_paths_from_env(self):
        self.assertEqual(self.engine.paths.root, os.path.join(self.tmp, "home"))

    def test_state_shape(self):
        st = self.engine.get_state()
        self.assertEqual(set(st), {"app_version", "engine", "update", "auth", "settings", "jobs"})
        self.assertEqual(st["app_version"], "9.9.9")
        self.assertEqual(set(st["update"]), {"stage", "current", "latest", "notes", "size", "published_at",
                                             "progress", "message", "checked_at", "can_apply"})
        self.assertEqual(st["update"]["current"], "9.9.9")
        self.assertFalse(st["update"]["can_apply"])
        self.assertEqual(set(st["engine"]), {"stage", "message", "progress", "components", "last_check"})
        self.assertEqual(set(st["engine"]["components"]), {"yt-dlp", "gallery-dl", "ffmpeg", "deno"})
        self.assertFalse(st["auth"]["logged_in"])

    def test_set_settings_emits_and_validates(self):
        new = self.engine.set_settings({"concurrency": 3})
        self.assertEqual(new["concurrency"], 3)
        self.assertEqual([t for t, _ in self.events], ["settings"])
        with self.assertRaises(ValueError):
            self.engine.set_settings({"concurrency": 9})

    def test_emit_failure_is_contained(self):
        def boom(t, p):
            raise RuntimeError("ui gone")
        engine = Engine(boom, "1", use_system_ffmpeg=False, app_updates=False)
        engine.set_settings({"quality_youtube": "medium"})  # must not raise
        engine.logout()

    @unittest.skipUnless(HAS_LINKCLEAN, "app.core.linkclean not written yet")
    def test_enqueue_skips_invalid_and_duplicates(self):
        res = self.engine.enqueue("https://www.instagram.com/reel/DQCkNLtgqEe/?igsh=x https://example.com/a "
                                  "https://www.instagram.com/reel/DQCkNLtgqEe")
        self.assertEqual(len(res["added"]), 1)
        self.assertEqual(res["added"][0]["url"], "https://www.instagram.com/reel/DQCkNLtgqEe")
        self.assertEqual(res["added"][0]["status"], "queued")
        self.assertEqual(res["added"][0]["platform"], "instagram")
        self.assertEqual(res["added"][0]["kind"], "reel")
        self.assertEqual(res["added"][0]["format"], "mp4")
        self.assertEqual([s["reason"] for s in res["skipped"]], ["invalid"])
        again = self.engine.enqueue("instagram.com/reel/DQCkNLtgqEe")
        self.assertEqual(again["added"], [])
        self.assertEqual(again["skipped"][0]["reason"], "duplicate")
        job_id = res["added"][0]["id"]
        self.assertEqual(self.engine.set_job_format(job_id, "mp3")["format"], "mp3")
        with self.assertRaises(ValueError):
            self.engine.set_job_format(job_id, "wav")
        self.assertTrue(self.engine.cancel(job_id))
        self.assertIsNone(self.engine.set_job_format(job_id, "mp4"))  # no longer queued
        self.assertIsNone(self.engine.job_file(job_id))
        self.assertEqual(self.engine.clear_finished("youtube"), 0)
        self.assertEqual(self.engine.clear_finished(), 1)

    def _start_order(self, n):
        """Ids of the next n jobs the download workers would start (the engine is not started here)."""
        dl = self.engine.downloader
        dl.usable = lambda: True
        order = []
        for _ in range(n):
            job = dl._next_job()
            order.append(job.id)
            with dl._cond:
                dl._running.discard(job.id)  # as if it finished at once: the next one may start
        return order

    @unittest.skipUnless(HAS_LINKCLEAN, "app.core.linkclean not written yet")
    def test_pasted_batch_reads_and_downloads_top_to_bottom(self):
        links = ["https://www.youtube.com/watch?v=aaaaaaaaaa1", "https://www.youtube.com/watch?v=aaaaaaaaaa2",
                 "https://www.youtube.com/watch?v=aaaaaaaaaa3"]
        res = self.engine.enqueue("\n".join(links), "youtube")
        added = [j["id"] for j in res["added"]]
        self.assertEqual([j["url"] for j in res["added"]], links)  # `added` in paste order
        shown = [j["id"] for j in self.engine.get_state()["jobs"]]  # newest first = top to bottom in the UI
        self.assertEqual(shown, added)
        created = [j["created_at"] for j in res["added"]]
        self.assertEqual(created, sorted(created, reverse=True))  # the UI sorts by created_at, newest first
        self.assertEqual(len(set(created)), 3)
        job_events = [p["id"] for t, p in self.events if t == "job"]
        self.assertEqual(job_events, list(reversed(added)))  # emitted oldest first: inserting on arrival agrees
        self.assertEqual(self._start_order(3), added)

    @unittest.skipUnless(HAS_LINKCLEAN, "app.core.linkclean not written yet")
    def test_later_enqueue_starts_before_older_queued_jobs(self):
        first = [j["id"] for j in self.engine.enqueue(
            "https://www.youtube.com/watch?v=bbbbbbbbbb1 https://www.youtube.com/watch?v=bbbbbbbbbb2")["added"]]
        later = self.engine.enqueue("https://www.youtube.com/watch?v=bbbbbbbbbb3")["added"][0]["id"]
        self.assertEqual([j["id"] for j in self.engine.get_state()["jobs"]], [later] + first)
        self.assertEqual(self._start_order(3), [later] + first)

    def test_created_at_is_strictly_increasing_on_a_frozen_clock(self):
        dl = self.engine.downloader
        from unittest import mock
        with mock.patch("app.core.downloader.time.time", return_value=1760950000.0):
            a = dl.add("https://www.youtube.com/watch?v=ccccccccccc", "ccccccccccc", "youtube", "video", "mp4")
            batch = dl.add_batch([("https://www.youtube.com/watch?v=cccccccccc1", "cccccccccc1", "youtube", "video",
                                   "mp4"),
                                  ("https://www.youtube.com/watch?v=cccccccccc2", "cccccccccc2", "youtube", "video",
                                   "mp4")])
        self.assertGreater(batch[1]["created_at"], a["created_at"])
        self.assertGreater(batch[0]["created_at"], batch[1]["created_at"])
        shown = dl.jobs()
        self.assertEqual([j["id"] for j in shown], [batch[0]["id"], batch[1]["id"], a["id"]])
        self.assertEqual([j["created_at"] for j in shown], sorted((j["created_at"] for j in shown), reverse=True))

    def test_retried_job_moves_to_the_top_and_starts_next(self):
        dl = self.engine.downloader
        old = dl.add("https://www.youtube.com/watch?v=ddddddddddd", "ddddddddddd", "youtube", "video", "mp4")["id"]
        newer = dl.add("https://www.youtube.com/watch?v=dddddddddd1", "dddddddddd1", "youtube", "video", "mp4")["id"]
        self.assertTrue(dl.cancel(old))
        created = dl.get(old).created_at
        self.assertTrue(dl.retry(old))
        self.assertEqual([j["id"] for j in dl.jobs()], [old, newer])
        self.assertEqual(dl.get(old).created_at, created)  # unchanged: a live UI keeps the card in place
        self.assertEqual(self._start_order(2), [old, newer])

    @unittest.skipUnless(HAS_LINKCLEAN, "app.core.linkclean not written yet")
    def test_enqueue_platform_filter_and_format(self):
        text = "https://youtu.be/dQw4w9WgXcQ?t=5 https://www.instagram.com/reel/DQCkNLtgqEe"
        res = self.engine.enqueue(text, "youtube", "mp3")
        self.assertEqual(len(res["added"]), 1)
        job = res["added"][0]
        self.assertEqual(job["url"], "https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        self.assertEqual((job["platform"], job["kind"], job["format"]), ("youtube", "video", "mp3"))
        self.assertEqual([s["reason"] for s in res["skipped"]], ["invalid"])
        both = self.engine.clean_links(text)
        self.assertEqual(both["valid"], 2)
        only_ig = self.engine.clean_links(text, "instagram")
        self.assertEqual((only_ig["valid"], only_ig["invalid"]), (1, 1))
        self.assertEqual(only_ig["items"][0]["error"], "other_platform")
        with self.assertRaises(ValueError):
            self.engine.clean_links(text, "vimeo")
        self.engine.set_settings({"format": "mp3"})
        res2 = self.engine.enqueue("https://www.instagram.com/reel/DQCkNLtgqEe")
        self.assertEqual(res2["added"][0]["format"], "mp3")
        self.engine.cancel(res2["added"][0]["id"])
        self.engine.cancel(job["id"])


if __name__ == "__main__":
    unittest.main()
