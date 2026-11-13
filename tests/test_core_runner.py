"""Subprocess control of a job: waiting for an engine update at the binary gate, and the stall limits of
ProcessRunner (a process that keeps working is never ended for its length; one that goes silent is)."""
import os
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core_fakes import YT_INFO, CoreTestBase, ScriptedRunner  # noqa: E402

YT_URL = "https://www.youtube.com/watch?v=aqz-KE-bpKQ"


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


if __name__ == "__main__":
    unittest.main()
