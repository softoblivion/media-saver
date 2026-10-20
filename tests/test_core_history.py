import json
import os
import shutil
import tempfile
import unittest

from app.core import settings as S
from app.core.downloader import HISTORY_LIMIT, Downloader
from app.core.paths import AppPaths


class HistoryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="idl_hist_")
        os.environ["REELS_SAVER_HOME"] = os.path.join(self.tmp, "home")
        self.paths = AppPaths()
        self.settings = S.apply_patch(S.defaults(), {"output_dir": os.path.join(self.tmp, "out")})

    def tearDown(self):
        os.environ.pop("REELS_SAVER_HOME", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def make(self):
        return Downloader(self.paths, lambda: self.settings, lambda t, p: None, binaries=lambda: {},
                          usable=lambda: False)

    def test_restore_marks_active_as_interrupted(self):
        dl = self.make()
        a = dl.get(dl.add("https://www.instagram.com/reel/AAA", "AAA")["id"])
        b = dl.get(dl.add("https://www.instagram.com/reel/BBB", "BBB")["id"])
        c = dl.get(dl.add("https://www.instagram.com/reel/CCC", "CCC")["id"])
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
        dl.add("https://www.instagram.com/reel/AAA", "AAA")
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

    def test_remove_and_clear(self):
        events = []
        dl = Downloader(self.paths, lambda: self.settings, lambda t, p: events.append((t, p)), binaries=lambda: {},
                        usable=lambda: False)
        a = dl.get(dl.add("https://www.instagram.com/reel/AAA", "AAA")["id"])
        b = dl.get(dl.add("https://www.instagram.com/reel/BBB", "BBB")["id"])
        self.assertEqual(a.stage_text, "Waiting for engine update")
        self.assertEqual(dl.active_shortcodes(), {"AAA", "BBB"})
        self.assertTrue(dl.cancel(a.id))
        self.assertEqual(a.status, "canceled")
        self.assertEqual(dl.clear_finished(), 1)
        self.assertTrue(dl.remove(b.id))
        self.assertFalse(dl.remove(b.id))
        self.assertEqual(dl.jobs(), [])
        self.assertEqual([p["id"] for t, p in events if t == "job_removed"], [a.id, b.id])


if __name__ == "__main__":
    unittest.main()
