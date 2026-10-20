"""Engine facade shape and enqueue rules (no network: the updater is never started)."""
import importlib.util
import os
import shutil
import tempfile
import unittest

from app.core.engine import Engine

HAS_LINKCLEAN = importlib.util.find_spec("app.core.linkclean") is not None


class EngineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="idl_engine_")
        os.environ["REELS_SAVER_HOME"] = os.path.join(self.tmp, "home")
        self.events = []
        self.engine = Engine(lambda t, p: self.events.append((t, p)), "9.9.9", use_system_ffmpeg=False)

    def tearDown(self):
        self.engine.downloader.shutdown(0.5)
        os.environ.pop("REELS_SAVER_HOME", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_paths_from_env(self):
        self.assertEqual(self.engine.paths.root, os.path.join(self.tmp, "home"))

    def test_state_shape(self):
        st = self.engine.get_state()
        self.assertEqual(set(st), {"app_version", "engine", "auth", "settings", "jobs"})
        self.assertEqual(st["app_version"], "9.9.9")
        self.assertEqual(set(st["engine"]), {"stage", "message", "progress", "components", "last_check"})
        self.assertEqual(set(st["engine"]["components"]), {"yt-dlp", "gallery-dl", "ffmpeg"})
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
        engine = Engine(boom, "1", use_system_ffmpeg=False)
        engine.set_settings({"quality": "720"})  # must not raise
        engine.logout()

    @unittest.skipUnless(HAS_LINKCLEAN, "app.core.linkclean not written yet")
    def test_enqueue_skips_invalid_and_duplicates(self):
        res = self.engine.enqueue("https://www.instagram.com/reel/DQCkNLtgqEe/?igsh=x https://example.com/a "
                                  "https://www.instagram.com/reel/DQCkNLtgqEe")
        self.assertEqual(len(res["added"]), 1)
        self.assertEqual(res["added"][0]["url"], "https://www.instagram.com/reel/DQCkNLtgqEe")
        self.assertEqual(res["added"][0]["status"], "queued")
        self.assertEqual([s["reason"] for s in res["skipped"]], ["invalid"])
        again = self.engine.enqueue("instagram.com/reel/DQCkNLtgqEe")
        self.assertEqual(again["added"], [])
        self.assertEqual(again["skipped"][0]["reason"], "duplicate")
        job_id = res["added"][0]["id"]
        self.assertTrue(self.engine.cancel(job_id))
        self.assertIsNone(self.engine.job_file(job_id))
        self.assertEqual(self.engine.clear_finished(), 1)


if __name__ == "__main__":
    unittest.main()
