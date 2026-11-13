"""Subprocess control of a job: waiting for an engine update at the binary gate, and the stall limits of
ProcessRunner (a process that keeps working is never ended for its length; one that goes silent is)."""
import os
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core_fakes import YT_INFO, CoreTestBase, ScriptedRunner  # noqa: E402

from app.core.downloader import ProcessResult, ProcessRunner, tree_bytes  # noqa: E402

YT_URL = "https://www.youtube.com/watch?v=aqz-KE-bpKQ"
TOOL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fake_slow_tool.py")


def tool(mode, seconds, out, times=None):
    return [sys.executable, "-I", TOOL, mode, str(seconds), out] + ([times] if times else [])


def longest_silence(times_path, spawned, returned):
    """The longest span in which the child showed no sign of life: from the spawn to its first mark, between its
    marks, and from its last mark to the run's return (fake_slow_tool's <times_path>, time.monotonic())."""
    try:
        with open(times_path, encoding="ascii") as fh:
            marks = [float(x) for x in fh.read().split()]
    except OSError:
        marks = []
    points = [spawned] + marks + [returned]
    return max(b - a for a, b in zip(points, points[1:]))


def run_working(test, attempt, limit, tries=3):
    """Runs `attempt(times_path)` -> (result, stalled) until a run is not stalled. A stalled run fails the test
    when the child was never silent for `limit` (the runner ended a working child); one whose child was silent
    that long met a host that stalled the child (its interpreter start or its output under a heavy load), which
    is what the limit is for, so it is tried again, and the test is skipped when every try met such a host."""
    silences = []
    for _ in range(tries):
        with tempfile.TemporaryDirectory(prefix="ms_times_") as folder:
            times = os.path.join(folder, "times.txt")
            spawned = time.monotonic()
            res, stalled = attempt(times)
            returned = time.monotonic()
            if not stalled:
                return res
            silence = longest_silence(times, spawned, returned)
        test.assertGreaterEqual(silence, limit, f"a child silent for at most {silence:.2f} s was ended by the "
                                                f"{limit} s stall limit")
        silences.append(round(silence, 2))
    test.skipTest(f"the host stalled the child past the {limit} s limit in every try (silences {silences} s)")


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


# The stall clock starts when the child is spawned, so the child's interpreter start counts as silence. That start
# takes about 0.1 s on an idle machine, more than 1 s on a loaded one and, on a host at full load, longer than any
# limit a test can afford: a test that needs a working run to survive gives it a limit of OUTLIVE_LIMIT and a run
# longer than that, never a 1 s limit, and goes through run_working, which tells a host stall from a runner defect.
OUTLIVE_LIMIT = 3.0
OUTLIVE_RUN = 4.0


class StallLimitTest(unittest.TestCase):
    """ProcessRunner with real child processes (tests/fake_slow_tool.py) and stall limits scaled to seconds."""

    def setUp(self):
        import tempfile
        self.dir = tempfile.mkdtemp(prefix="ms_stall_")
        self.out = os.path.join(self.dir, "media.mp4")
        self.runner = ProcessRunner()
        self.runner.ACTIVITY_POLL = 0.2

    def tearDown(self):
        import shutil
        shutil.rmtree(self.dir, ignore_errors=True)

    def run_tool(self, argv, **kw):
        res = self.runner.run(argv, **kw)
        return res, res.stalled

    def test_a_run_that_keeps_printing_outlives_the_limit(self):
        started = time.monotonic()
        res = run_working(self, lambda times: self.run_tool(tool("lines", OUTLIVE_RUN, self.out, times),
                                                            stall_timeout=OUTLIVE_LIMIT), OUTLIVE_LIMIT)
        self.assertGreater(time.monotonic() - started, OUTLIVE_RUN - 0.1)
        self.assertEqual((res.returncode, res.stalled, res.timed_out), (0, False, False), res.stderr)

    def test_a_run_that_only_writes_its_file_outlives_the_limit(self):
        res = run_working(self, lambda times: self.run_tool(tool("writes", OUTLIVE_RUN, self.out, times),
                                                            stall_timeout=OUTLIVE_LIMIT,
                                                            activity=lambda: tree_bytes(self.dir)), OUTLIVE_LIMIT)
        self.assertEqual((res.returncode, res.stalled), (0, False), res.stderr)

    def test_a_silent_run_is_ended_at_the_limit(self):
        idle = []
        started = time.monotonic()
        res = self.runner.run(tool("silent", 30, self.out), stall_timeout=1.0,
                              activity=lambda: tree_bytes(self.dir), on_idle=idle.append, idle_notice=0.3)
        # Ended by the limit, long before the child's own 30 s; the bound leaves room for a slow kill and start
        # on a loaded host.
        self.assertLess(time.monotonic() - started, 20)
        self.assertTrue(res.stalled)
        self.assertFalse(res.timed_out)
        # The child's interpreter start may take longer than the notice on a loaded machine, so a notice and its
        # clearing can come before the first line; the run ends while it is silent.
        self.assertTrue(idle and all(s is None or s >= 0.3 for s in idle), idle)
        self.assertIsNotNone(idle[-1], idle)

    def test_the_idle_notice_clears_when_output_comes_back(self):
        idle = []

        def attempt(times):
            idle.clear()
            return self.run_tool(tool("pause", 1.2, self.out, times), stall_timeout=5.0, on_idle=idle.append,
                                 idle_notice=0.3)
        res = run_working(self, attempt, 5.0)
        self.assertEqual((res.returncode, res.stalled), (0, False))
        # The 1.2 s pause gives a notice of 1 s or more; the lines after it clear it. A notice may follow again in
        # the child's exit after its last line, so the clearing is looked for after the pause, not at the end.
        paused = next((i for i, s in enumerate(idle) if s is not None and s >= 1.0), None)
        self.assertIsNotNone(paused, idle)
        self.assertIn(None, idle[paused + 1:], idle)  # activity came back: the card leaves the stalled state


class SlowDownloadRunner(ScriptedRunner):
    """ScriptedRunner whose download call (`--load-info-json`) is a real child process: `mode` of fake_slow_tool."""

    def __init__(self, info, mode, seconds, times=None):
        super().__init__(info)
        self.mode, self.seconds, self.times = mode, seconds, times
        self.real = ProcessRunner()
        self.real.ACTIVITY_POLL = 0.2

    def run(self, argv, **kw):
        if "--load-info-json" not in argv:
            return super().run(argv, **kw)
        with self.lock:
            self.calls.append(list(argv))
        out = argv[argv.index("-o") + 1].replace("%(ext)s", "mp4")
        return self.real.run(tool(self.mode, self.seconds, out, self.times), **kw)


class JobStallTest(CoreTestBase, unittest.TestCase):
    """The download stage of a job: no wall-clock limit, a stall limit (scaled to seconds here) and its error code."""

    def run_download(self, mode, seconds, limit=1.0, times=None):
        dl = self.make(SlowDownloadRunner(YT_INFO, mode, seconds, times))
        dl.STALL_LIMITS = {"download": limit, "convert": limit}
        dl.STALL_NOTICE = 0.3
        job = dl.get(dl.add(YT_URL, "aqz-KE-bpKQ", "youtube", "video", "mp4")["id"])
        dl._run_job(job)
        return dl, job

    def test_a_download_that_keeps_working_longer_than_the_stall_limit_finishes(self):
        def attempt(times):
            _, job = self.run_download("lines", OUTLIVE_RUN, OUTLIVE_LIMIT, times)
            return job, bool(job.error) and job.error["code"] == "timeout"
        job = run_working(self, attempt, OUTLIVE_LIMIT)
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

    def test_the_stall_message_follows_the_part_of_the_run_that_stalled(self):
        dl = self.make(ScriptedRunner(YT_INFO))
        job = dl.get(dl.add(YT_URL, "aqz-KE-bpKQ", "youtube", "video", "mp4")["id"])
        job.status = "downloading"
        self.assertEqual(dl._timeout_message(job, "download", 300),
                         "No data arrived for 5 minutes, so the download was stopped. Try again.")
        job.status = "processing"  # yt-dlp merging tracks or extracting MP3 audio inside the download run
        self.assertEqual(dl._timeout_message(job, "download", 300),
                         "The conversion made no progress for 5 minutes and was stopped. Try again.")
        self.assertEqual(dl._timeout_message(job, "convert", 600),
                         "The conversion made no progress for 10 minutes and was stopped. Try again.")

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
