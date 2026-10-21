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
TT = "https://www.tiktok.com/@tiktok/video/7106594312292453675?is_from_webapp=1&sender_device=pc"
TT_CLEAN = "https://www.tiktok.com/@tiktok/video/7106594312292453675"
TT_SHORT = "https://vm.tiktok.com/ZMabcdEF1/"
TT_SHORT_CLEAN = "https://vm.tiktok.com/ZMabcdEF1"
TT_T = "https://www.tiktok.com/t/ZT8abcdef"
X = "https://x.com/oshtru/status/1577855540407197696?s=20"
X_CLEAN = "https://x.com/oshtru/status/1577855540407197696"
X_OLD = "https://twitter.com/oshtru/status/1577855540407197696"
X_I = "https://twitter.com/i/status/1577855540407197696"


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
            self.engine.clean_links(IG, "vimeo")

    def test_tiktok_and_x_links_clean_to_canonical_forms(self):
        result = self.engine.clean_links(f"{TT} {TT_SHORT} {TT_T} {X} {X_OLD} {X_I}")
        self.assertEqual([i["clean"] for i in result["items"]],
                         [TT_CLEAN, TT_SHORT_CLEAN, TT_T, X_CLEAN, X_CLEAN, "https://x.com/i/status/1577855540407197696"])
        self.assertEqual([i["platform"] for i in result["items"]], ["tiktok"] * 3 + ["twitter"] * 3)
        self.assertEqual([i["kind"] for i in result["items"]], ["video", "share", "share", "post", "post", "post"])
        self.assertEqual([i["shortcode"] for i in result["items"]],
                         ["7106594312292453675", "ZMabcdEF1", "ZT8abcdef"] + ["1577855540407197696"] * 3)
        # The simulated engine de-duplicates by platform and id, so twitter.com/i/status/<id> folds into the user form.
        self.assertEqual((result["valid"], result["invalid"], result["duplicates"]), (4, 0, 2))
        self.assertEqual(len(result["clean_text"].split("\n")), 4)

    def test_vt_short_link_keeps_its_host(self):
        item = self.engine.clean_links("https://vt.tiktok.com/ZSabc12345/")["items"][0]
        self.assertEqual((item["clean"], item["kind"]), ("https://vt.tiktok.com/ZSabc12345", "share"))

    def test_platform_filter_marks_the_other_three(self):
        text = f"{IG} {YT} {TT} {X}"
        for platform in ("instagram", "youtube", "tiktok", "twitter"):
            with self.subTest(platform=platform):
                result = self.engine.clean_links(text, platform)
                errors = {i["platform"]: i["error"] for i in result["items"]}
                self.assertEqual(errors, {p: (None if p == platform else "other_platform")
                                          for p in ("instagram", "youtube", "tiktok", "twitter")})
                self.assertEqual((result["valid"], result["invalid"]), (1, 3))
                for item in result["items"]:
                    self.assertTrue(item["clean"] and item["shortcode"] and item["kind"])

    def test_unsupported_paths_on_the_new_hosts(self):
        result = self.engine.clean_links(
            "https://www.tiktok.com/@tiktok https://x.com/home x.com/oshtru https://t.co/AbCdEfGhIj https://box.com/a")
        self.assertEqual([i["error"] for i in result["items"]],
                         ["unsupported_path", "unsupported_path", "unsupported_path", "unsupported_path",
                          "not_supported"])
        self.assertEqual((result["valid"], result["invalid"]), (0, 5))


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

    def test_enqueue_tiktok_and_x_links(self):
        result = self.engine.enqueue(f"{TT} {TT_SHORT} {X} {X_OLD}", None, "mp4")
        self.assertEqual(result["skipped"], [{"input": X_OLD, "reason": "duplicate"}])
        self.assertEqual([(j["platform"], j["kind"], j["shortcode"], j["url"]) for j in result["added"]], [
            ("tiktok", "video", "7106594312292453675", TT_CLEAN),
            ("tiktok", "share", "ZMabcdEF1", TT_SHORT_CLEAN),
            ("twitter", "post", "1577855540407197696", X_CLEAN),
        ])
        for job in result["added"]:
            self.assertEqual((job["format"], job["status"], job["previewed"]), ("mp4", "queued", False))

    def test_enqueue_with_platform_filter_skips_the_others(self):
        result = self.engine.enqueue(f"{IG} {TT} {X}", "twitter")
        self.assertEqual([j["platform"] for j in result["added"]], ["twitter"])
        self.assertEqual(result["skipped"], [{"input": IG, "reason": "invalid"}, {"input": TT, "reason": "invalid"}])

    def test_demo_preview_for_tiktok_and_x(self):
        self.engine.set_settings({"concurrency": 4})
        added = self.engine.enqueue(f"{TT} {X}")["added"]
        for job in added:
            self.assertTrue(self.wait_for(lambda j=job: any(
                t == "job" and p["id"] == j["id"] and p["previewed"] for t, p in list(self.events))))
        previews = {p["platform"]: p for t, p in list(self.events) if t == "job" and p["previewed"]}
        self.assertEqual((previews["tiktok"]["author"], previews["tiktok"]["title"]),
                         ("demo_tt_7106", "Demo TikTok 7106594312292453675"))
        self.assertEqual((previews["twitter"]["author"], previews["twitter"]["title"]),
                         ("demo_x_1577", "Demo post 1577855540407197696"))

    def test_tiktok_and_x_jobs_finish(self):
        with mock.patch("app.fake_engine.time.sleep", lambda s: None):
            self.engine.enqueue(f"{TT} {X}")
            self.assertTrue(self.wait_for(lambda: len(self.engine.get_state()["jobs"]) == 2 and all(
                j["status"] == "done" for j in self.engine.get_state()["jobs"]), 20))
        done = {j["platform"]: j for j in self.engine.get_state()["jobs"]}
        self.assertTrue(done["tiktok"]["filepath"].endswith(".mp4"))
        self.assertEqual(done["twitter"]["title"], "Demo post 1577855540407197696")

    def test_simulated_failure_for_the_new_platforms(self):
        cases = [
            ("tiktok", "video", "failAAAA1", "TikTok changed something and the video could not be read. Try again later."),
            ("twitter", "post", "failBBBB1", "X changed something and the video could not be read. Try again later."),
        ]
        for platform, kind, code, message in cases:
            with self.subTest(platform=platform):
                found = (platform, kind, code, f"https://example.invalid/{code}")
                with mock.patch("app.fake_engine._match_link", lambda token, f=found: f), mock.patch(
                        "app.fake_engine.time.sleep", lambda s: None):
                    self.engine.enqueue("https://example.invalid/" + code)
                    self.assertTrue(self.wait_for(lambda: any(
                        j["platform"] == platform and j["status"] == "error" for j in self.engine.get_state()["jobs"]), 20))
                job = next(j for j in self.engine.get_state()["jobs"] if j["platform"] == platform)
                self.assertEqual((job["error"]["code"], job["error"]["message"], job["error"]["can_login"]),
                                 ("extractor", message, False))

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
        for key in ("mode", "format", "auto_download_updates", "update_check_interval", "update_channel",
                    "open_folder_when_done"):
            self.assertIn(key, state["settings"])
        for mode in ("instagram", "youtube", "tiktok", "twitter"):
            self.assertEqual(self.engine.set_settings({"mode": mode})["mode"], mode)
        with self.assertRaises(ValueError):
            self.engine.set_settings({"mode": "vimeo"})
        with self.assertRaises(ValueError):
            self.engine.set_settings({"format": "avi"})


class QualitySettingsTests(FakeBase):
    def test_defaults_for_all_four_platforms(self):
        settings = self.engine.get_state()["settings"]
        self.assertEqual([settings[f"quality_{p}"] for p in ("instagram", "youtube", "tiktok", "twitter")],
                         ["max"] * 4)

    def test_tiktok_and_x_quality_accept_low_medium_max(self):
        for key in ("quality_tiktok", "quality_twitter"):
            for value in ("low", "medium", "max"):
                with self.subTest(key=key, value=value):
                    self.assertEqual(self.engine.set_settings({key: value})[key], value)
                    self.assertEqual(self.engine.get_settings()[key], value)

    def test_invalid_tiktok_and_x_quality_is_rejected(self):
        cases = [("quality_tiktok", "Invalid TikTok quality."), ("quality_twitter", "Invalid X quality.")]
        for key, message in cases:
            for value in ("360", "720", "high", "", None):
                with self.subTest(key=key, value=value):
                    with self.assertRaises(ValueError) as ctx:
                        self.engine.set_settings({key: value})
                    self.assertEqual(str(ctx.exception), message)
        self.assertEqual(self.engine.get_settings()["quality_tiktok"], "max")

    def test_settings_event_carries_the_new_keys(self):
        self.engine.set_settings({"quality_tiktok": "low"})
        events = [p for t, p in list(self.events) if t == "settings"]
        self.assertEqual(events[-1]["quality_tiktok"], "low")
        self.assertEqual(events[-1]["quality_twitter"], "max")


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
