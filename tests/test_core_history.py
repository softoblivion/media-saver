import json
import os
import shutil
import tempfile
import time
import unittest

from app.core import settings as S
from app.core.downloader import HISTORY_LIMIT, Downloader
from app.core.paths import AppPaths


class HistoryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="idl_hist_")
        os.environ["MEDIA_SAVER_HOME"] = os.path.join(self.tmp, "home")
        self.paths = AppPaths()
        self.settings = S.apply_patch(S.defaults(), {"output_dir": os.path.join(self.tmp, "out")})

    def tearDown(self):
        os.environ.pop("MEDIA_SAVER_HOME", None)
        for _ in range(10):  # a background history save may re-create home/ right after the removal
            shutil.rmtree(self.tmp, ignore_errors=True)
            time.sleep(0.1)
            if not os.path.exists(self.tmp):
                break

    def make(self):
        return Downloader(self.paths, lambda: self.settings, lambda t, p: None, binaries=lambda: {},
                          usable=lambda: False)

    def test_restore_marks_active_as_interrupted(self):
        dl = self.make()
        a = dl.get(dl.add("https://www.instagram.com/reel/AAA", "AAA", "instagram", "reel", "mp4")["id"])
        b = dl.get(dl.add("https://www.instagram.com/reel/BBB", "BBB", "instagram", "reel", "mp4")["id"])
        c = dl.get(dl.add("https://www.instagram.com/reel/CCC", "CCC", "instagram", "reel", "mp4")["id"])
        a.status, a.filepath, a.progress = "done", "C:\\x\\a.mp4", 1.0
        b.status = "downloading"
        thumb = self.paths.thumb_file("CCC")
        with open(thumb, "wb") as fh:
            fh.write(b"\xff\xd8x")
        c.thumb_path = thumb
        c.thumbnail = "data:image/jpeg;base64,AAAA"
        dl.save_history()
        with open(self.paths.history_file, encoding="utf-8") as fh:
            raw = json.load(fh)
        self.assertNotIn("thumbnail", raw["jobs"][0])  # data URLs are not persisted

        dl2 = self.make()
        dl2.load_history()
        jobs = {j["shortcode"]: j for j in dl2.jobs()}
        self.assertEqual(jobs["AAA"]["status"], "done")
        self.assertEqual(jobs["AAA"]["filepath"], "C:\\x\\a.mp4")
        for code in ("BBB", "CCC"):
            self.assertEqual(jobs[code]["status"], "canceled")
            self.assertEqual(jobs[code]["stage_text"], "Interrupted when the app closed")
        self.assertTrue(jobs["CCC"]["thumbnail"].startswith("data:image/jpeg;base64,"))
        self.assertEqual([j["shortcode"] for j in dl2.jobs()], ["CCC", "BBB", "AAA"])  # newest first
        self.assertTrue(dl2.retry(jobs["BBB"]["id"]))

    def test_shutdown_save_marks_interrupted(self):
        dl = self.make()
        dl.add("https://www.instagram.com/reel/AAA", "AAA", "instagram", "reel", "mp4")
        dl.save_history(interrupted=True)
        with open(self.paths.history_file, encoding="utf-8") as fh:
            item = json.load(fh)["jobs"][0]
        self.assertEqual((item["status"], item["stage_text"]), ("canceled", "Interrupted when the app closed"))

    def test_limit_and_bad_file(self):
        items = [{"id": f"{i:08x}", "url": f"https://www.instagram.com/reel/S{i}", "shortcode": f"S{i}",
                  "status": "done", "created_at": float(i)} for i in range(HISTORY_LIMIT + 50)]
        with open(self.paths.history_file, "w", encoding="utf-8") as fh:
            json.dump({"version": 1, "jobs": items + [{"broken": True}]}, fh)
        dl = self.make()
        dl.load_history()
        self.assertLessEqual(len(dl.jobs()), HISTORY_LIMIT)
        self.assertEqual(dl.jobs()[0]["shortcode"], f"S{HISTORY_LIMIT + 49}")
        with open(self.paths.history_file, "w", encoding="utf-8") as fh:
            fh.write("{oops")
        dl3 = self.make()
        dl3.load_history()
        self.assertEqual(dl3.jobs(), [])

    def test_v1_history_migrates_to_v2(self):
        v1 = {"version": 1, "jobs": [
            {"id": "aaaa0001", "url": "https://www.instagram.com/reel/AAAAA", "shortcode": "AAAAA", "status": "done",
             "stage_text": "Done", "progress": 1.0, "title": "t", "author": "u", "filepath": "C:\\x\\u_AAAAA.mp4",
             "vcodec": "h264", "acodec": "aac", "created_at": 1.0, "finished_at": 2.0},
            {"id": "aaaa0002", "url": "https://www.instagram.com/p/BBBBB", "shortcode": "BBBBB", "status": "error",
             "error": {"code": "login_required", "message": "m", "can_login": True, "log_path": None},
             "created_at": 3.0},
        ]}
        with open(self.paths.history_file, "w", encoding="utf-8") as fh:
            json.dump(v1, fh)
        dl = self.make()
        dl.load_history()
        jobs = {j["shortcode"]: j for j in dl.jobs()}
        self.assertEqual((jobs["AAAAA"]["platform"], jobs["AAAAA"]["kind"], jobs["AAAAA"]["format"],
                          jobs["AAAAA"]["previewed"]), ("instagram", "reel", "mp4", True))
        self.assertEqual((jobs["BBBBB"]["platform"], jobs["BBBBB"]["kind"]), ("instagram", "p"))
        self.assertEqual(jobs["AAAAA"]["filepath"], "C:\\x\\u_AAAAA.mp4")
        self.assertEqual(dl.active_keys(), set())
        dl.save_history()
        with open(self.paths.history_file, encoding="utf-8") as fh:
            raw = json.load(fh)
        self.assertEqual(raw["version"], 2)
        self.assertTrue(all({"platform", "kind", "format", "previewed"} <= set(d) for d in raw["jobs"]))

    def test_bad_values_in_v2_entries_fall_back(self):
        items = [{"id": "c0000001", "url": "https://www.youtube.com/shorts/aqz-KE-bpKQ", "shortcode": "aqz-KE-bpKQ",
                  "platform": "youtube", "kind": "weird", "format": "flac", "status": "done", "created_at": 1.0},
                 {"id": "c0000002", "url": "https://x", "shortcode": "X", "platform": "tiktok", "status": "done",
                  "created_at": 2.0}]
        with open(self.paths.history_file, "w", encoding="utf-8") as fh:
            json.dump({"version": 2, "jobs": items}, fh)
        dl = self.make()
        dl.load_history()
        jobs = {j["id"]: j for j in dl.jobs()}
        self.assertEqual((jobs["c0000001"]["kind"], jobs["c0000001"]["format"]), ("shorts", "mp4"))
        self.assertEqual((jobs["c0000002"]["platform"], jobs["c0000002"]["kind"]), ("instagram", "reel"))

    def test_remove_and_clear(self):
        events = []
        dl = Downloader(self.paths, lambda: self.settings, lambda t, p: events.append((t, p)), binaries=lambda: {},
                        usable=lambda: False)
        a = dl.get(dl.add("https://www.instagram.com/reel/AAA", "AAA", "instagram", "reel", "mp4")["id"])
        b = dl.get(dl.add("https://www.instagram.com/reel/BBB", "BBB", "instagram", "reel", "mp4")["id"])
        self.assertEqual(a.stage_text, "Waiting for engine update")
        self.assertEqual(dl.active_keys(), {"instagram:AAA", "instagram:BBB"})
        self.assertTrue(dl.cancel(a.id))
        self.assertEqual(a.status, "canceled")
        self.assertEqual(dl.clear_finished(), 1)
        self.assertTrue(dl.remove(b.id))
        self.assertFalse(dl.remove(b.id))
        self.assertEqual(dl.jobs(), [])
        self.assertEqual([p["id"] for t, p in events if t == "job_removed"], [a.id, b.id])


if __name__ == "__main__":
    unittest.main()
