"""EventPump: buffering until ready(), per-job coalescing, escaping. No real window involved."""

import json
import threading
import unittest

from app.bridge import EventPump, build_event_script, js_literal


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def make_pump(**kwargs):
    clock = FakeClock()
    scripts: list[str] = []
    pump = EventPump(deliver=scripts.append, clock=clock, threaded=False, start_delay=0.0, **kwargs)
    return pump, clock, scripts


def delivered_events(scripts):
    """Parse the delivered scripts back into (type, payload) pairs."""
    import re

    out = []
    for script in scripts:
        for match in re.finditer(r"try\{f\((\".*?\"),(.*?)\)\}catch\(e\)", script):
            out.append((json.loads(match.group(1)), json.loads(match.group(2))))
    return out


def job(job_id: str, progress: float, status: str = "downloading") -> dict:
    return {"id": job_id, "status": status, "progress": progress}


class BufferingTests(unittest.TestCase):
    def test_nothing_is_delivered_before_ready(self):
        pump, clock, scripts = make_pump()
        pump.emit("toast", {"level": "info", "text": "a"})
        pump.emit("engine", {"stage": "ready"})
        pump.flush()
        clock.advance(5)
        pump.flush()
        self.assertEqual(scripts, [])

    def test_buffered_events_are_delivered_in_order_after_ready(self):
        pump, clock, scripts = make_pump()
        pump.emit("engine", {"stage": "checking"})
        pump.emit("toast", {"level": "info", "text": "hi"})
        pump.emit("engine", {"stage": "ready"})
        pump.set_ready()
        pump.flush()
        self.assertEqual(
            delivered_events(scripts),
            [("engine", {"stage": "checking"}), ("toast", {"level": "info", "text": "hi"}), ("engine", {"stage": "ready"})],
        )

    def test_ready_has_a_grace_period(self):
        clock = FakeClock()
        scripts: list[str] = []
        pump = EventPump(deliver=scripts.append, clock=clock, threaded=False, start_delay=0.15)
        pump.emit("toast", {"level": "info", "text": "x"})
        pump.set_ready()
        wait = pump.flush()
        self.assertEqual(scripts, [])
        self.assertAlmostEqual(wait, 0.15)
        clock.advance(0.16)
        pump.flush()
        self.assertEqual(len(scripts), 1)

    def test_events_after_ready_go_out_immediately(self):
        pump, clock, scripts = make_pump()
        pump.set_ready()
        pump.emit("auth", {"logged_in": True})
        pump.flush()
        self.assertEqual(delivered_events(scripts), [("auth", {"logged_in": True})])

    def test_no_deliver_target_keeps_events(self):
        clock = FakeClock()
        pump = EventPump(deliver=None, clock=clock, threaded=False, start_delay=0.0)
        pump.emit("toast", {"level": "info", "text": "x"})
        pump.set_ready()
        pump.flush()
        scripts: list[str] = []
        pump.set_deliver(scripts.append)
        pump.flush()
        self.assertEqual(len(scripts), 1)

    def test_stop_discards_new_events(self):
        pump, clock, scripts = make_pump()
        pump.set_ready()
        pump.stop()
        pump.emit("toast", {"level": "info", "text": "late"})
        pump.flush()
        self.assertEqual(scripts, [])


class CoalescingTests(unittest.TestCase):
    def test_burst_for_one_job_delivers_only_the_newest_payload(self):
        pump, clock, scripts = make_pump()
        for i in range(100):
            pump.emit("job", job("a", i / 100))
        pump.set_ready()
        pump.flush()
        events = delivered_events(scripts)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0][1]["progress"], 0.99)

    def test_second_delivery_is_held_until_the_interval_passed(self):
        pump, clock, scripts = make_pump()
        pump.set_ready()
        pump.emit("job", job("a", 0.1))
        pump.flush()
        clock.advance(0.05)
        pump.emit("job", job("a", 0.2))
        wait = pump.flush()
        self.assertEqual(len(delivered_events(scripts)), 1)
        self.assertAlmostEqual(wait, 0.075, places=3)
        clock.advance(0.08)
        pump.flush()
        events = delivered_events(scripts)
        self.assertEqual([e[1]["progress"] for e in events], [0.1, 0.2])

    def test_rate_is_at_most_eight_per_second_per_job(self):
        pump, clock, scripts = make_pump()
        pump.set_ready()
        for step in range(1000):  # one second, an update every millisecond, a pump tick every 5 ms
            pump.emit("job", job("a", step / 1000))
            if step % 5 == 0:
                pump.flush()
            clock.advance(0.001)
        events = delivered_events(scripts)
        self.assertLessEqual(len(events), 8)
        self.assertGreaterEqual(len(events), 6)

    def test_jobs_are_throttled_independently(self):
        pump, clock, scripts = make_pump()
        pump.set_ready()
        pump.emit("job", job("a", 0.1))
        pump.emit("job", job("b", 0.1))
        pump.flush()
        clock.advance(0.01)
        pump.emit("job", job("a", 0.2))
        pump.emit("job", job("c", 0.1))
        pump.flush()
        ids = [e[1]["id"] for e in delivered_events(scripts)]
        self.assertEqual(ids, ["a", "b", "c"])

    def test_final_state_is_never_lost(self):
        pump, clock, scripts = make_pump()
        pump.set_ready()
        pump.emit("job", job("a", 0.5))
        pump.flush()
        pump.emit("job", job("a", 1.0, status="done"))
        clock.advance(0.01)
        pump.flush()
        clock.advance(0.2)
        pump.flush()
        self.assertEqual(delivered_events(scripts)[-1][1]["status"], "done")

    def test_job_removed_cancels_a_pending_job_event(self):
        pump, clock, scripts = make_pump()
        pump.set_ready()
        pump.emit("job", job("a", 0.5))
        pump.flush()
        pump.emit("job", job("a", 0.6))
        pump.emit("job_removed", {"id": "a"})
        clock.advance(1)
        pump.flush()
        types = [e[0] for e in delivered_events(scripts)]
        self.assertEqual(types, ["job", "job_removed"])

    def test_other_events_are_not_held_back_by_throttled_jobs(self):
        pump, clock, scripts = make_pump()
        pump.set_ready()
        pump.emit("job", job("a", 0.1))
        pump.flush()
        pump.emit("job", job("a", 0.2))
        pump.emit("toast", {"level": "info", "text": "now"})
        pump.flush()
        self.assertEqual([e[0] for e in delivered_events(scripts)], ["job", "toast"])


class UpdateEventTests(unittest.TestCase):
    def test_update_events_are_not_coalesced_and_keep_their_order(self):
        pump, clock, scripts = make_pump()
        pump.set_ready()
        for stage, progress in (("checking", None), ("available", None), ("downloading", 0.2), ("downloading", 0.4),
                                ("ready", None)):
            pump.emit("update", {"stage": stage, "progress": progress})
        pump.flush()
        events = delivered_events(scripts)
        self.assertEqual([e[0] for e in events], ["update"] * 5)
        self.assertEqual([e[1]["stage"] for e in events], ["checking", "available", "downloading", "downloading", "ready"])

    def test_update_events_before_ready_are_buffered(self):
        pump, clock, scripts = make_pump()
        pump.emit("update", {"stage": "available", "latest": "2.1.0"})
        pump.flush()
        self.assertEqual(scripts, [])
        pump.set_ready()
        pump.flush()
        self.assertEqual(delivered_events(scripts), [("update", {"stage": "available", "latest": "2.1.0"})])


class EscapingTests(unittest.TestCase):
    def test_line_separators_are_escaped(self):
        text = js_literal({"t": "a b c"})
        self.assertNotIn(" ", text)
        self.assertNotIn(" ", text)
        self.assertEqual(json.loads(text), {"t": "a b c"})

    def test_non_ascii_is_kept_readable(self):
        text = js_literal({"t": "Café ✓"})
        self.assertIn("Café ✓", text)

    def test_script_survives_hostile_text(self):
        hostile = 'x"); alert(1); //\n</script> \\'
        script = build_event_script([("toast", {"level": "info", "text": hostile})])
        self.assertNotIn(" ", script)
        self.assertNotIn("\n", script.replace("\\n", ""))  # raw newlines would end a JS string literal
        events = delivered_events([script])
        self.assertEqual(events[0][1]["text"], hostile)

    def test_script_guards_missing_handler(self):
        script = build_event_script([("toast", {})])
        self.assertIn("typeof f!=='function'", script)

    def test_delivery_errors_do_not_propagate(self):
        clock = FakeClock()

        def broken(_script):
            raise RuntimeError("window gone")

        pump = EventPump(deliver=broken, clock=clock, threaded=False, start_delay=0.0)
        pump.set_ready()
        pump.emit("toast", {"level": "info", "text": "x"})
        pump.flush()  # must not raise


class ThreadedPumpTests(unittest.TestCase):
    def test_background_thread_delivers_after_ready(self):
        got = threading.Event()
        scripts: list[str] = []

        def deliver(script: str) -> None:
            scripts.append(script)
            got.set()

        pump = EventPump(deliver=deliver, job_interval=0.02, start_delay=0.01)
        try:
            pump.emit("toast", {"level": "info", "text": "early"})
            self.assertFalse(got.wait(0.1))
            pump.set_ready()
            self.assertTrue(got.wait(2))
            got.clear()
            pump.emit("job", job("a", 0.1))
            pump.emit("job", job("a", 0.2))
            pump.emit("job", job("a", 0.3))
            self.assertTrue(got.wait(2))
            deadline = threading.Event()
            deadline.wait(0.2)
            events = delivered_events(scripts)
            self.assertEqual(events[0][0], "toast")
            self.assertEqual(events[-1][1]["progress"], 0.3)
        finally:
            pump.stop()


if __name__ == "__main__":
    unittest.main()
