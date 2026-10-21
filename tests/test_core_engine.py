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

    def test_update_check_interval_setting_drives_the_scheduler(self):
        self.assertEqual(self.engine.get_settings()["update_check_interval"], "30m")
        self.assertEqual(self.engine.update_scheduler.interval_key, "30m")
        self.engine.set_settings({"update_check_interval": "1d"})
        self.assertEqual(self.engine.update_scheduler.interval_key, "1d")
        with self.assertRaises(ValueError):
            self.engine.set_settings({"update_check_interval": "5m"})
        self.assertEqual(self.engine.update_scheduler.interval_key, "1d")

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
        engine.set_settings({"quality_youtube": "720"})  # must not raise
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
