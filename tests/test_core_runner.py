"""Subprocess control of a job: waiting for an engine update at the binary gate, and the stall limits of
ProcessRunner (a process that keeps working is never ended for its length; one that goes silent is)."""
import os
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core_fakes import YT_INFO, CoreTestBase, ScriptedRunner  # noqa: E402

from app.core.downloader import ProcessResult, ProcessRunner, tree_bytes  # noqa: E402

YT_URL = "https://www.youtube.com/watch?v=aqz-KE-bpKQ"
TOOL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fake_slow_tool.py")


def tool(mode, seconds, out):
    return [sys.executable, "-I", TOOL, mode, str(seconds), out]


def wait_for(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        time.sleep(0.02)
    return predicate()


class GateWaitTest(CoreTestBase, unittest.TestCase):
    def test_a_running_job_says_it_waits_for_the_engine_update(self):
        dl = self.make(ScriptedRunner(YT_INFO))
        job = dl.get(dl.add(YT_URL, "aqz-KE-bpKQ", "youtube", "video", "mp4")["id"])
        job.status, job.stage_text = "downloading", "Downloading video"
        self.assertTrue(dl.gate.acquire_exclusive(timeout=1))  # the updater replaces the binaries
        result = {}

        def run_tool():
            result["res"] = dl._run_tool(job, [self.bins["ffprobe"], "x.mp4"], [], label="probe", timeout=5)

        t = threading.Thread(target=run_tool, daemon=True)
        t.start()
        try:
            self.assertTrue(wait_for(lambda: job.stage_text == "Waiting for engine update"), job.stage_text)
            self.assertIn("Waiting for engine update", [p["stage_text"] for p in self.job_events(job.id)])
        finally:
            dl.gate.release_exclusive()
        t.join(5)
        self.assertEqual(result["res"].returncode, 0)
        self.assertEqual(job.stage_text, "Downloading video")  # the stage it had before the wait

    def test_no_stage_change_when_the_gate_is_free(self):
        dl = self.make(ScriptedRunner(YT_INFO))
        job = dl.get(dl.add(YT_URL, "aqz-KE-bpKQ", "youtube", "video", "mp4")["id"])
        job.status, job.stage_text = "downloading", "Downloading video"
        dl._run_tool(job, [self.bins["ffprobe"], "x.mp4"], [], label="probe", timeout=5)
        self.assertEqual(self.job_events(job.id)[1:], [])  # only the add event


class StallLimitTest(unittest.TestCase):
    """ProcessRunner with real child processes (tests/fake_slow_tool.py) and a stall limit of 1 s."""

    def setUp(self):
        import tempfile
        self.dir = tempfile.mkdtemp(prefix="ms_stall_")
        self.out = os.path.join(self.dir, "media.mp4")
        self.runner = ProcessRunner()
        self.runner.ACTIVITY_POLL = 0.2

    def tearDown(self):
        import shutil
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_a_run_that_keeps_printing_outlives_the_limit(self):
        started = time.monotonic()
        res = self.runner.run(tool("lines", 2.5, self.out), stall_timeout=1.0)
        self.assertGreater(time.monotonic() - started, 2.4)
        self.assertEqual((res.returncode, res.stalled, res.timed_out), (0, False, False), res.stderr)

    def test_a_run_that_only_writes_its_file_outlives_the_limit(self):
        res = self.runner.run(tool("writes", 2.5, self.out), stall_timeout=1.0,
                              activity=lambda: tree_bytes(self.dir))
        self.assertEqual((res.returncode, res.stalled), (0, False), res.stderr)

    def test_a_silent_run_is_ended_at_the_limit(self):
        idle = []
        started = time.monotonic()
        res = self.runner.run(tool("silent", 30, self.out), stall_timeout=1.0,
                              activity=lambda: tree_bytes(self.dir), on_idle=idle.append, idle_notice=0.3)
        self.assertLess(time.monotonic() - started, 10)
        self.assertTrue(res.stalled)
        self.assertFalse(res.timed_out)
        self.assertTrue(idle and all(s is not None and s >= 0.3 for s in idle), idle)

    def test_the_idle_notice_clears_when_output_comes_back(self):
        idle = []
        res = self.runner.run(tool("pause", 1.2, self.out), stall_timeout=5.0, on_idle=idle.append,
                              idle_notice=0.3)
        self.assertEqual((res.returncode, res.stalled), (0, False))
        self.assertGreater(len(idle), 1, idle)
        self.assertIsNotNone(idle[0])
        self.assertIsNone(idle[-1])  # activity came back: the card leaves the stalled state


class SlowDownloadRunner(ScriptedRunner):
    """ScriptedRunner whose download call (`--load-info-json`) is a real child process: `mode` of fake_slow_tool."""

    def __init__(self, info, mode, seconds):
        super().__init__(info)
        self.mode, self.seconds = mode, seconds
        self.real = ProcessRunner()
        self.real.ACTIVITY_POLL = 0.2

    def run(self, argv, **kw):
        if "--load-info-json" not in argv:
            return super().run(argv, **kw)
        with self.lock:
            self.calls.append(list(argv))
        out = argv[argv.index("-o") + 1].replace("%(ext)s", "mp4")
        return self.real.run(tool(self.mode, self.seconds, out), **kw)


class JobStallTest(CoreTestBase, unittest.TestCase):
    """The download stage of a job: no wall-clock limit, a stall limit (scaled to 1 s here) and its error code."""

    def run_download(self, mode, seconds):
        dl = self.make(SlowDownloadRunner(YT_INFO, mode, seconds))
        dl.DOWNLOAD_TIMEOUT = 1.0  # the former one-hour wall-clock limit, scaled like the stall limit
        dl.STALL_LIMITS = {"download": 1.0, "convert": 1.0}
        dl.STALL_NOTICE = 0.3
        job = dl.get(dl.add(YT_URL, "aqz-KE-bpKQ", "youtube", "video", "mp4")["id"])
        dl._run_job(job)
        return dl, job

    def test_a_download_that_keeps_working_past_the_old_limit_finishes(self):
        _, job = self.run_download("lines", 2.5)
        self.assertEqual(job.status, "done", job.error)
        self.assertTrue(os.path.isfile(job.filepath))

    def test_a_silent_download_ends_with_the_timeout_code(self):
        started = time.monotonic()
        _, job = self.run_download("silent", 30)
        self.assertLess(time.monotonic() - started, 15)
        self.assertEqual(job.status, "error")
        self.assertEqual(job.error["code"], "timeout")
        self.assertEqual(job.error["message"], "No data arrived for 1 second, so the download was stopped. Try again.")
        stalled = [p for p in self.job_events(job.id) if p["stalled_s"] is not None]
        self.assertTrue(stalled, "the card never showed the stall")
        self.assertEqual(stalled[-1]["stall_limit_s"], 1)
        self.assertIsNone(job.stalled_s)  # the failed card shows the error, not a stall

    def test_a_probe_without_an_answer_ends_with_the_timeout_code(self):
        runner = ScriptedRunner(YT_INFO)
        orig = runner.run

        def no_answer(argv, **kw):
            if "-J" in argv:
                return ProcessResult(None, "", "", timed_out=True)
            return orig(argv, **kw)

        runner.run = no_answer
        dl = self.make(runner)
        job = dl.get(dl.add(YT_URL, "aqz-KE-bpKQ", "youtube", "video", "mp4")["id"])
        dl._run_job(job)
        self.assertEqual(job.error["code"], "timeout")
        self.assertEqual(job.error["message"], "YouTube did not answer within 3 minutes. Try again.")


if __name__ == "__main__":
    unittest.main()
