"""Queueing finished jobs again: resume after a restart, retry every failed job, download a missing file again.
No workers run (the Downloader is not started), so a job queued again stays queued."""
import json
import os
import shutil
import tempfile
import time
import unittest

from app.core import settings as S
from app.core.downloader import FINISHED, INTERRUPTED_TEXT, Downloader
from app.core.engine import Engine
from app.core.paths import AppPaths


def link(code, platform="instagram"):
    if platform == "youtube":
        return (f"https://www.youtube.com/watch?v={code}", code, "youtube", "video", "mp4")
    return (f"https://www.instagram.com/reel/{code}", code, "instagram", "reel", "mp4")


class RequeueBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ms_requeue_")
        self._old_home = os.environ.get("MEDIA_SAVER_HOME")
        os.environ["MEDIA_SAVER_HOME"] = os.path.join(self.tmp, "home")
        self.paths = AppPaths()
        self.settings = S.apply_patch(S.defaults(), {"output_dir": os.path.join(self.tmp, "out")})
        self.events = []

    def tearDown(self):
        if self._old_home is None:
            os.environ.pop("MEDIA_SAVER_HOME", None)
        else:
            os.environ["MEDIA_SAVER_HOME"] = self._old_home
        for _ in range(10):  # a background history save may re-create home/ right after the removal
            shutil.rmtree(self.tmp, ignore_errors=True)
            time.sleep(0.1)
            if not os.path.exists(self.tmp):
                break

    def make(self):
        dl = Downloader(self.paths, lambda: self.settings, lambda t, p: self.events.append((t, p)),
                        binaries=lambda: {}, usable=lambda: True)
        dl._save_soon = dl.save_history  # the background save, run in line
        return dl

    def add(self, dl, code, platform="instagram", status=None, **fields):
        job = dl.get(dl.add(*link(code, platform))["id"])
        if status is not None:
            with dl._lock:
                job.status = status
                if status in FINISHED:
                    dl._queue.remove(job.id)
                for k, v in fields.items():
                    setattr(job, k, v)
        return job

    def restart(self, dl):
        dl.shutdown(0.1)
        dl2 = self.make()
        dl2.load_history()
        return dl2

    def by_code(self, dl):
        return {j["shortcode"]: j for j in dl.jobs()}


class ResumeInterruptedTest(RequeueBase):
    def test_close_marks_queued_and_running_jobs_and_resume_queues_them_again(self):
        dl = self.make()
        self.add(dl, "DONE1", status="done", filepath="C:\\x\\a.mp4")
        self.add(dl, "FAIL1", status="error")
        self.add(dl, "RUN01", status="downloading")
        self.add(dl, "QUE01")
        dl2 = self.restart(dl)
        jobs = self.by_code(dl2)
        self.assertEqual({c for c, j in jobs.items() if j["interrupted"]}, {"RUN01", "QUE01"})
        self.assertEqual(jobs["QUE01"]["stage_text"], INTERRUPTED_TEXT)
        self.assertEqual(dl2.resume_interrupted(), 2)
        jobs = self.by_code(dl2)
        self.assertEqual({c for c, j in jobs.items() if j["status"] == "queued"}, {"RUN01", "QUE01"})
        self.assertFalse(any(j["interrupted"] for j in jobs.values()))
        # both on top in their old order; the failed job stays failed
        self.assertEqual([j["shortcode"] for j in dl2.jobs()], ["QUE01", "RUN01", "FAIL1", "DONE1"])
        self.assertEqual(jobs["FAIL1"]["status"], "error")
        self.assertEqual(dl2.resume_interrupted(), 0)
        with open(self.paths.history_file, encoding="utf-8") as fh:
            saved = {d["shortcode"]: d for d in json.load(fh)["jobs"]}
        self.assertEqual((saved["QUE01"]["status"], saved["QUE01"]["interrupted"]), ("queued", False))

    def test_an_unanswered_mark_lasts_until_the_next_close(self):
        dl = self.make()
        self.add(dl, "QUE01")
        dl2 = self.restart(dl)
        self.assertTrue(self.by_code(dl2)["QUE01"]["interrupted"])
        self.add(dl2, "QUE02")
        dl3 = self.restart(dl2)  # the second start did not resume QUE01; this close interrupted QUE02 only
        jobs = self.by_code(dl3)
        self.assertEqual((jobs["QUE01"]["interrupted"], jobs["QUE02"]["interrupted"]), (False, True))

    def test_an_app_that_ended_without_its_shutdown_save_still_marks_active_jobs(self):
        dl = self.make()
        self.add(dl, "QUE01")
        dl.save_history()  # the last regular save; the process then dies
        dl2 = self.make()
        dl2.load_history()
        job = self.by_code(dl2)["QUE01"]
        self.assertEqual((job["status"], job["interrupted"]), ("canceled", True))

    def test_a_file_from_before_the_mark_uses_the_stage_text(self):
        def entry(i, stage, **extra):
            return {"id": f"a000000{i}", "url": f"https://www.instagram.com/reel/C{i}", "shortcode": f"C{i}",
                    "status": "canceled", "stage_text": stage, "created_at": float(i), **extra}
        with open(self.paths.history_file, "w", encoding="utf-8") as fh:
            json.dump({"version": 2, "jobs": [entry(1, INTERRUPTED_TEXT), entry(2, "Canceled"),
                                              entry(3, INTERRUPTED_TEXT, interrupted=False)]}, fh)
        dl = self.make()
        dl.load_history()
        jobs = self.by_code(dl)
        self.assertEqual([jobs[c]["interrupted"] for c in ("C1", "C2", "C3")], [True, False, False])

    def test_a_job_retried_by_hand_loses_the_mark(self):
        dl = self.make()
        self.add(dl, "QUE01")
        dl2 = self.restart(dl)
        job = self.by_code(dl2)["QUE01"]
        self.assertTrue(dl2.retry(job["id"]))
        self.assertFalse(dl2.get(job["id"]).interrupted)
        self.assertEqual(dl2.resume_interrupted(), 0)


class RetryFailedTest(RequeueBase):
    def test_retries_failed_jobs_of_one_platform_or_all(self):
        dl = self.make()
        self.add(dl, "IGERR", status="error")
        self.add(dl, "YTERR001234", "youtube", status="error")
        self.add(dl, "IGCAN", status="canceled")
        self.add(dl, "IGOK1", status="done", filepath="C:\\x\\a.mp4")
        self.assertEqual(dl.retry_failed("youtube"), 1)
        self.assertEqual(self.by_code(dl)["IGERR"]["status"], "error")
        self.assertEqual(dl.retry_failed(), 1)
        jobs = self.by_code(dl)
        self.assertEqual({c: j["status"] for c, j in jobs.items()},
                         {"IGERR": "queued", "YTERR001234": "queued", "IGCAN": "canceled", "IGOK1": "done"})
        self.assertEqual(dl.retry_failed(), 0)
        emitted = [p["shortcode"] for t, p in self.events if t == "job" and p["status"] == "queued"]
        self.assertEqual(emitted[-2:], ["YTERR001234", "IGERR"])

    def test_one_job_per_video(self):
        dl = self.make()
        self.add(dl, "SAME1", status="error")
        self.add(dl, "SAME1", status="error")
        self.assertEqual(dl.retry_failed(), 1)
        self.assertEqual(sorted(j["status"] for j in dl.jobs()), ["error", "queued"])


class DownloadAgainTest(RequeueBase):
    def test_a_done_job_whose_file_is_gone_is_queued_again(self):
        dl = self.make()
        present = os.path.join(self.tmp, "here.mp4")
        with open(present, "wb") as fh:
            fh.write(b"x")
        gone = self.add(dl, "GONE1", status="done", filepath=os.path.join(self.tmp, "gone.mp4"), filesize=10)
        here = self.add(dl, "HERE1", status="done", filepath=present)
        failed = self.add(dl, "FAIL1", status="error")
        self.assertEqual([dl.can_download_again(j.id) for j in (gone, here, failed)], [True, False, False])
        self.assertFalse(dl.download_again(here.id))
        self.assertFalse(dl.download_again(failed.id))
        self.assertFalse(dl.download_again("nope"))
        self.assertTrue(dl.download_again(gone.id))
        self.assertEqual((gone.status, gone.filepath, gone.filesize), ("queued", None, None))
        self.assertFalse(dl.can_download_again(gone.id))
        self.assertFalse(dl.download_again(gone.id))


class EngineFacadeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ms_requeue_engine_")
        self._old_home = os.environ.get("MEDIA_SAVER_HOME")
        os.environ["MEDIA_SAVER_HOME"] = os.path.join(self.tmp, "home")
        self.engine = Engine(lambda t, p: None, "9.9.9", use_system_ffmpeg=False, app_updates=False)

    def tearDown(self):
        import logging
        self.engine.downloader.shutdown(0.5)
        if self._old_home is None:
            os.environ.pop("MEDIA_SAVER_HOME", None)
        else:
            os.environ["MEDIA_SAVER_HOME"] = self._old_home
        root = logging.getLogger("mediasaver")
        for h in list(root.handlers):
            root.removeHandler(h)
            h.close()
        for _ in range(10):
            shutil.rmtree(self.tmp, ignore_errors=True)
            if not os.path.exists(self.tmp):
                break
            time.sleep(0.1)

    def test_facade_delegates_and_checks_the_platform(self):
        calls = []
        dl = self.engine.downloader
        dl.retry_failed = lambda platform=None: calls.append(("retry_failed", platform)) or 2
        dl.resume_interrupted = lambda: calls.append(("resume_interrupted",)) or 3
        dl.download_again = lambda job_id: calls.append(("download_again", job_id)) or True
        dl.can_download_again = lambda job_id: calls.append(("can_download_again", job_id)) or False
        self.assertEqual(self.engine.retry_failed("tiktok"), 2)
        self.assertEqual(self.engine.resume_interrupted(), 3)
        self.assertTrue(self.engine.download_again("a1"))
        self.assertFalse(self.engine.can_download_again("a1"))
        with self.assertRaises(ValueError):
            self.engine.retry_failed("vimeo")
        self.assertEqual(calls, [("retry_failed", "tiktok"), ("resume_interrupted",), ("download_again", "a1"),
                                 ("can_download_again", "a1")])


if __name__ == "__main__":
    unittest.main()
