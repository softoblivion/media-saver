"""The simulated engine (`--fake`) serves the whole contract of docs/BRIDGE_API.md without network access."""

import os
import shutil
import tempfile
import threading
import time
import unittest
from unittest import mock

from app.fake_engine import FakeEngine

IG = "https://www.instagram.com/reel/DAbC123xYz_/?igsh=MWx0"
IG_CLEAN = "https://www.instagram.com/reel/DAbC123xYz_"
YT = "https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PL1&t=5"
YT_CLEAN = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
SHORTS = "https://youtube.com/shorts/abcdefghijk?feature=share"
YOUTU_BE = "https://youtu.be/dQw4w9WgXcQ?si=zzz"


class FakeBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ms_fake_engine_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.events: list[tuple[str, dict]] = []
        self.lock = threading.Lock()
        self.engine = FakeEngine(self._emit, "2.0.0", root=self.tmp)
        self.addCleanup(self.engine.shutdown)

    def _emit(self, etype, payload):
        with self.lock:
            self.events.append((etype, payload))

    def wait_for(self, predicate, timeout=10.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if predicate():
                return True
            time.sleep(0.05)
        return False

    def update_events(self):
        with self.lock:
            return [p for t, p in self.events if t == "update"]


class LinkTests(FakeBase):
    def test_both_platforms_clean_to_canonical_forms(self):
        result = self.engine.clean_links(f"{IG}\n{YT} {SHORTS} {YOUTU_BE}")
        clean = [i["clean"] for i in result["items"]]
        self.assertEqual(clean, [IG_CLEAN, YT_CLEAN, "https://www.youtube.com/shorts/abcdefghijk", YT_CLEAN])
        self.assertEqual([i["platform"] for i in result["items"]], ["instagram", "youtube", "youtube", "youtube"])
        self.assertEqual([i["kind"] for i in result["items"]], ["reel", "video", "shorts", "video"])
        self.assertEqual((result["valid"], result["invalid"], result["duplicates"]), (3, 0, 1))
        self.assertEqual(result["clean_text"].count("\n"), 2)

    def test_other_platform_items_are_filled_but_invalid(self):
        result = self.engine.clean_links(f"{IG} {YT}", "youtube")
        ig, yt = result["items"]
        self.assertEqual(ig["error"], "other_platform")
        self.assertEqual((ig["clean"], ig["platform"], ig["kind"]), (IG_CLEAN, "instagram", "reel"))
        self.assertIsNone(yt["error"])
        self.assertEqual((result["valid"], result["invalid"]), (1, 1))

    def test_errors_for_unknown_hosts_and_paths(self):
        result = self.engine.clean_links("https://example.com/x https://www.instagram.com/someuser/ plain")
        self.assertEqual([i["error"] for i in result["items"]], ["not_supported", "unsupported_path"])

    def test_bad_platform_is_rejected(self):
        with self.assertRaises(ValueError):
            self.engine.clean_links(IG, "tiktok")


class JobTests(FakeBase):
    def test_enqueue_creates_jobs_with_platform_kind_and_format(self):
        result = self.engine.enqueue(f"{IG} {YT}", None, "mp3")
        self.assertEqual(result["skipped"], [])
        by_platform = {j["platform"]: j for j in result["added"]}
        self.assertEqual(by_platform["instagram"]["kind"], "reel")
        self.assertEqual(by_platform["youtube"]["kind"], "video")
        for job in result["added"]:
            self.assertEqual(job["format"], "mp3")
            self.assertFalse(job["previewed"])
            self.assertEqual(job["status"], "queued")

    def test_default_format_comes_from_settings_and_platform_filters_links(self):
        self.engine.set_settings({"format": "mp3"})
        result = self.engine.enqueue(f"{IG} {YT}", "youtube")
        self.assertEqual([j["platform"] for j in result["added"]], ["youtube"])
        self.assertEqual(result["added"][0]["format"], "mp3")
        self.assertEqual(result["skipped"], [{"input": IG, "reason": "invalid"}])

    def test_duplicates_are_per_platform(self):
        self.engine.enqueue(IG)
        again = self.engine.enqueue(IG)
        self.assertEqual(again["skipped"], [{"input": IG, "reason": "duplicate"}])

    def test_preview_arrives_while_the_job_is_queued(self):
        self.engine.set_settings({"concurrency": 1})
        first = self.engine.enqueue(YT)["added"][0]  # occupies the single slot
        second = self.engine.enqueue(IG)["added"][0]
        self.assertTrue(self.wait_for(lambda: any(
            t == "job" and p["id"] == second["id"] and p["previewed"] for t, p in list(self.events))))
        job = next(p for t, p in list(self.events) if t == "job" and p["id"] == second["id"] and p["previewed"])
        self.assertEqual(job["status"], "queued")
        self.assertTrue(job["title"] and job["author"] and job["thumbnail"])
        self.assertGreater(job["duration_s"], 0)
        self.assertIsNotNone(first)

    def test_set_job_format_only_while_queued(self):
        self.engine.set_settings({"concurrency": 1})
        self.engine.enqueue(YT)
        queued = self.engine.enqueue(IG)["added"][0]
        changed = self.engine.set_job_format(queued["id"], "mp3")
        self.assertEqual(changed["format"], "mp3")
        self.assertTrue(any(t == "job" and p["id"] == queued["id"] and p["format"] == "mp3" for t, p in list(self.events)))
        self.assertIsNone(self.engine.set_job_format("nope", "mp3"))
        with self.assertRaises(ValueError):
            self.engine.set_job_format(queued["id"], "avi")

    def test_mp3_job_finishes_without_video_fields(self):
        with mock.patch("app.fake_engine.time.sleep", lambda s: None):
            job = self.engine.enqueue(IG, None, "mp3")["added"][0]
            self.assertTrue(self.wait_for(lambda: self.engine.get_state()["jobs"][0]["status"] == "done", 20))
        done = self.engine.get_state()["jobs"][0]
        self.assertEqual(done["id"], job["id"])
        self.assertEqual(done["acodec"], "mp3")
        for key in ("vcodec", "width", "height", "fps"):
            self.assertIsNone(done[key], key)
        self.assertTrue(done["filepath"].endswith(".mp3"))
        self.assertTrue(os.path.exists(done["filepath"]))

    def test_mp4_job_has_video_fields(self):
        with mock.patch("app.fake_engine.time.sleep", lambda s: None):
            self.engine.enqueue(YT)
            self.assertTrue(self.wait_for(lambda: self.engine.get_state()["jobs"][0]["status"] == "done", 20))
        done = self.engine.get_state()["jobs"][0]
        self.assertEqual((done["vcodec"], done["acodec"], done["height"]), ("h264", "aac", 1920))
        self.assertTrue(done["filepath"].endswith(".mp4"))

    def test_fail_shortcode_gives_a_platform_specific_error(self):
        with mock.patch("app.fake_engine.time.sleep", lambda s: None):
            self.engine.enqueue("https://www.instagram.com/reel/failAAAA1/")
            self.assertTrue(self.wait_for(lambda: self.engine.get_state()["jobs"][0]["status"] == "error", 20))
        error = self.engine.get_state()["jobs"][0]["error"]
        self.assertEqual(error["code"], "login_required")
        self.assertTrue(error["can_login"])

    def test_clear_finished_filters_by_platform(self):
        with mock.patch("app.fake_engine.time.sleep", lambda s: None):
            self.engine.enqueue(f"{IG} {YT}")
            self.assertTrue(self.wait_for(lambda: all(j["status"] == "done" for j in self.engine.get_state()["jobs"])
                                          and len(self.engine.get_state()["jobs"]) == 2, 20))
        self.assertEqual(self.engine.clear_finished("youtube"), 1)
        self.assertEqual([j["platform"] for j in self.engine.get_state()["jobs"]], ["instagram"])
        self.assertEqual(self.engine.clear_finished(), 1)
        self.assertEqual(self.engine.get_state()["jobs"], [])

    def test_state_has_all_sections_and_settings_keys(self):
        state = self.engine.get_state()
        self.assertEqual(set(state), {"app_version", "engine", "update", "auth", "settings", "jobs"})
        self.assertEqual(state["update"]["stage"], "idle")
        for key in ("mode", "format", "auto_install_updates", "update_channel", "open_folder_when_done"):
            self.assertIn(key, state["settings"])
        with self.assertRaises(ValueError):
            self.engine.set_settings({"mode": "tiktok"})
        with self.assertRaises(ValueError):
            self.engine.set_settings({"format": "avi"})


class AppUpdateTests(FakeBase):
    def test_check_then_download_then_apply(self):
        status = self.engine.check_updates()
        self.assertEqual(status["stage"], "checking")
        self.assertTrue(self.wait_for(lambda: self.engine.get_state()["update"]["stage"] == "available"))
        update = self.engine.get_state()["update"]
        self.assertEqual((update["current"], update["latest"]), ("2.0.0", "2.1.0"))
        self.assertTrue(update["notes"].startswith("## "))
        self.assertGreater(update["size"], 10_000_000)
        self.assertGreater(update["published_at"], 1.7e9)
        self.assertFalse(self.engine.apply_update())  # not downloaded yet
        self.engine.download_update()
        self.assertTrue(self.wait_for(lambda: self.engine.get_state()["update"]["stage"] == "ready", 10))
        ready = self.engine.get_state()["update"]
        self.assertTrue(ready["can_apply"])
        progresses = [u["progress"] for u in self.update_events() if u["stage"] == "downloading"]
        self.assertGreater(len(progresses), 5)
        self.assertEqual(progresses, sorted(progresses))
        self.assertTrue(self.engine.apply_update())

    def test_second_check_while_running_is_ignored(self):
        self.engine.check_updates()
        self.engine.check_updates()
        self.assertTrue(self.wait_for(lambda: self.engine.get_state()["update"]["stage"] == "available"))
        self.assertEqual([u["stage"] for u in self.update_events()].count("checking"), 1)

    def test_up_to_date_mode(self):
        with mock.patch.dict(os.environ, {"MEDIA_SAVER_FAKE_UPDATE": "uptodate"}):
            self.engine.check_updates()
            self.assertTrue(self.wait_for(lambda: self.engine.get_state()["update"]["stage"] == "up_to_date"))
        self.assertEqual(self.engine.get_state()["update"]["message"], "Media Saver is up to date")
        self.assertFalse(self.engine.apply_update())

    def test_error_mode_and_retry(self):
        with mock.patch.dict(os.environ, {"MEDIA_SAVER_FAKE_UPDATE": "error"}):
            self.engine.check_updates()
            self.assertTrue(self.wait_for(lambda: self.engine.get_state()["update"]["stage"] == "error"))
        self.assertEqual(self.engine.get_state()["update"]["message"], "No internet connection")
        self.engine.download_update()  # nothing to download after a failed check
        self.assertEqual(self.engine.get_state()["update"]["stage"], "error")
        self.engine.check_updates()
        self.assertTrue(self.wait_for(lambda: self.engine.get_state()["update"]["stage"] == "available"))


if __name__ == "__main__":
    unittest.main()
