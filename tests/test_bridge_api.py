"""Bridge: contract surface, delegation to the Engine facade, exception -> English message. Fake window and engine."""

import inspect
import logging
import threading
import unittest
from unittest import mock

from app.bridge import Bridge, BridgeError, EventPump, friendly_message

logging.getLogger("mediasaver.bridge").setLevel(logging.CRITICAL)  # the error tests log tracebacks on purpose

CONTRACT_METHODS = {
    "ready", "clean_links", "enqueue", "set_job_format", "cancel", "retry", "remove", "clear_finished", "open_file",
    "reveal_file", "open_output_dir", "open_logs", "choose_output_dir", "get_settings", "set_settings",
    "update_engine", "update_status", "check_updates", "download_update", "apply_update", "login", "logout", "import_cookies_file",
    "read_clipboard", "copy_text", "open_url", "window_minimize", "window_close", "set_scale",
}


class FakeWindow:
    def __init__(self) -> None:
        self.calls: list = []
        self.scripts: list[str] = []
        self.dialog_result = None
        self.destroyed = threading.Event()

    def run_js(self, script):
        self.scripts.append(script)

    def minimize(self):
        self.calls.append("minimize")

    def hide(self):
        self.calls.append("hide")

    def destroy(self):
        self.calls.append("destroy")
        self.destroyed.set()

    def create_file_dialog(self, dialog_type, directory="", allow_multiple=False, save_filename="", file_types=()):
        self.calls.append(("dialog", dialog_type, directory, tuple(file_types)))
        return self.dialog_result


class FakePaths:
    root = "C:\\scratch"
    logs_dir = None


class RecordingEngine:
    """Records calls; every method returns a recognisable value."""

    def __init__(self) -> None:
        self.calls: list = []
        self.paths = FakePaths()
        self.settings = {"output_dir": "C:\\out", "quality_instagram": "max", "quality_youtube": "max"}
        self.fail_with: BaseException | None = None
        self.job_path = None
        self.apply_result = True

    def _rec(self, name, *args):
        self.calls.append((name, *args))
        if self.fail_with is not None:
            raise self.fail_with

    def get_state(self):
        self._rec("get_state")
        return {"app_version": "1.0.0", "jobs": []}

    def clean_links(self, text, platform=None):
        self._rec("clean_links", text, platform)
        return {"items": [], "clean_text": "", "valid": 0, "invalid": 0, "duplicates": 0}

    def enqueue(self, text, platform=None, fmt=None):
        self._rec("enqueue", text, platform, fmt)
        return {"added": [], "skipped": []}

    def set_job_format(self, job_id, fmt):
        self._rec("set_job_format", job_id, fmt)
        return {"id": job_id, "format": fmt}

    def cancel(self, job_id):
        self._rec("cancel", job_id)
        return 1

    def retry(self, job_id):
        self._rec("retry", job_id)
        return True

    def remove(self, job_id):
        self._rec("remove", job_id)
        return True

    def clear_finished(self, platform=None):
        self._rec("clear_finished", platform)
        return 3

    def job_file(self, job_id):
        self._rec("job_file", job_id)
        return self.job_path

    def get_settings(self):
        self._rec("get_settings")
        return dict(self.settings)

    def set_settings(self, patch):
        self._rec("set_settings", patch)
        self.settings.update(patch)
        return dict(self.settings)

    def update_engine(self):
        self._rec("update_engine")

    def update_status(self):
        self._rec("update_status")
        return {"stage": "up_to_date", "current": "1.0.0"}

    def check_updates(self):
        self._rec("check_updates")
        return {"stage": "checking", "current": "1.0.0"}

    def download_update(self):
        self._rec("download_update")

    def apply_update(self):
        self._rec("apply_update")
        return self.apply_result

    def logout(self):
        self._rec("logout")
        return {"logged_in": False}

    def import_cookies_file(self, path):
        self._rec("import_cookies_file", path)
        return {"logged_in": True}

    def shutdown(self):
        self._rec("shutdown")


def make_bridge():
    engine = RecordingEngine()
    window = FakeWindow()
    pump = EventPump(threaded=False, start_delay=0.0)
    closed = threading.Event()
    bridge = Bridge(engine, pump, on_close=closed.set)
    bridge._attach_window(window)
    return bridge, engine, window, pump, closed


class SurfaceTests(unittest.TestCase):
    def test_public_attributes_are_exactly_the_contract(self):
        bridge, *_ = make_bridge()
        public = {n for n in dir(bridge) if not n.startswith("_")}
        self.assertEqual(public, CONTRACT_METHODS)

    def test_public_attributes_are_all_callable(self):
        bridge, *_ = make_bridge()
        for name in CONTRACT_METHODS:
            self.assertTrue(callable(getattr(bridge, name)), name)

    def test_pywebview_can_read_parameter_names(self):
        bridge, *_ = make_bridge()
        def arg_names(fn):
            return inspect.getfullargspec(fn).args

        self.assertEqual(arg_names(bridge.clean_links), ["self", "text", "platform"])
        self.assertEqual(arg_names(bridge.enqueue), ["self", "text", "platform", "format"])
        self.assertEqual(arg_names(bridge.clear_finished), ["self", "platform"])
        self.assertEqual(arg_names(bridge.set_job_format), ["self", "id", "format"])
        self.assertEqual(arg_names(bridge.cancel), ["self", "id"])
        self.assertEqual(arg_names(bridge.ready), ["self"])
        for name in ("update_status", "check_updates", "download_update", "apply_update"):
            self.assertEqual(arg_names(getattr(bridge, name)), ["self"], name)


class DelegationTests(unittest.TestCase):
    def test_simple_delegation_and_return_types(self):
        bridge, engine, *_ = make_bridge()
        self.assertEqual(bridge.clean_links("x")["valid"], 0)
        self.assertEqual(bridge.enqueue("y"), {"added": [], "skipped": []})
        self.assertIs(bridge.cancel("a1"), True)  # engine returned 1 -> bool
        self.assertIs(bridge.retry("a1"), True)
        self.assertIs(bridge.remove("a1"), True)
        self.assertEqual(bridge.clear_finished(), 3)
        self.assertEqual(bridge.get_settings()["quality_youtube"], "max")
        self.assertEqual(bridge.set_settings({"quality_youtube": "medium"})["quality_youtube"], "medium")
        self.assertIsNone(bridge.update_engine())
        self.assertEqual(bridge.logout(), {"logged_in": False})
        names = [c[0] for c in engine.calls]
        for expected in ("clean_links", "enqueue", "cancel", "retry", "remove", "clear_finished", "update_engine", "logout"):
            self.assertIn(expected, names)

    def test_platform_and_format_pass_through_as_none_when_omitted(self):
        bridge, engine, *_ = make_bridge()
        bridge.clean_links("x")
        bridge.enqueue("y")
        bridge.clear_finished()
        bridge.clean_links("x", "")  # an empty string means default as well
        bridge.enqueue("y", None, None)
        self.assertEqual(engine.calls, [
            ("clean_links", "x", None), ("enqueue", "y", None, None), ("clear_finished", None),
            ("clean_links", "x", None), ("enqueue", "y", None, None),
        ])

    def test_platform_and_format_arguments_are_forwarded(self):
        bridge, engine, *_ = make_bridge()
        bridge.clean_links("x", "youtube")
        bridge.enqueue("y", "youtube", "mp3")
        bridge.clear_finished("instagram")
        self.assertEqual(engine.calls, [
            ("clean_links", "x", "youtube"), ("enqueue", "y", "youtube", "mp3"), ("clear_finished", "instagram"),
        ])

    def test_platform_and_format_must_be_text(self):
        bridge, *_ = make_bridge()
        with self.assertRaises(BridgeError):
            bridge.enqueue("y", 5, None)
        with self.assertRaises(BridgeError):
            bridge.clear_finished(["instagram"])

    def test_set_job_format_delegates(self):
        bridge, engine, *_ = make_bridge()
        self.assertEqual(bridge.set_job_format("a1", "mp3"), {"id": "a1", "format": "mp3"})
        self.assertIn(("set_job_format", "a1", "mp3"), engine.calls)

    def test_set_job_format_rejects_a_missing_format(self):
        bridge, engine, *_ = make_bridge()
        for bad in (None, ""):
            with self.assertRaises(BridgeError) as ctx:
                bridge.set_job_format("a1", bad)
            self.assertEqual(str(ctx.exception), "Format must be mp4 or mp3.")
        with self.assertRaises(BridgeError):
            bridge.set_job_format("a1", 3)
        self.assertNotIn("set_job_format", [c[0] for c in engine.calls])

    def test_engine_value_errors_for_a_bad_format_reach_the_ui(self):
        bridge, engine, *_ = make_bridge()
        engine.fail_with = ValueError("Unknown format: 'avi'")
        with self.assertRaises(BridgeError) as ctx:
            bridge.set_job_format("a1", "avi")
        self.assertEqual(str(ctx.exception), "Unknown format: 'avi'")

    def test_ready_returns_state_and_opens_the_pump(self):
        bridge, engine, window, pump, _ = make_bridge()
        self.assertFalse(pump.ready)
        pump.emit("toast", {"level": "info", "text": "early"})
        state = bridge.ready()
        self.assertEqual(state["app_version"], "1.0.0")
        self.assertTrue(pump.ready)
        pump.flush()
        self.assertTrue(any("early" in s for s in window.scripts))
        self.assertTrue(any('"window"' in s for s in window.scripts))

    def test_set_settings_rejects_non_objects(self):
        bridge, *_ = make_bridge()
        with self.assertRaises(BridgeError) as ctx:
            bridge.set_settings("quality=720")
        self.assertEqual(str(ctx.exception), "Expected a settings object.")

    def test_text_arguments_must_be_text(self):
        bridge, *_ = make_bridge()
        with self.assertRaises(BridgeError):
            bridge.enqueue(123)
        self.assertEqual(bridge.enqueue(None), {"added": [], "skipped": []})

    def test_login_uses_the_opener(self):
        bridge, *_ = make_bridge()
        with self.assertRaises(BridgeError):
            bridge.login()
        opened = []
        bridge._set_login_opener(lambda: opened.append(1))
        self.assertIsNone(bridge.login())
        self.assertEqual(opened, [1])

    def test_logout_runs_hook(self):
        bridge, *_ = make_bridge()
        hook = mock.Mock()
        bridge._set_logout_hook(hook)
        bridge.logout()
        hook.assert_called_once_with()

    def test_clipboard_and_url_go_through_winutil(self):
        bridge, *_ = make_bridge()
        with mock.patch("app.bridge.winutil.read_clipboard", return_value="abc") as rc, \
                mock.patch("app.bridge.winutil.write_clipboard", return_value=True) as wc, \
                mock.patch("app.bridge.winutil.open_url", return_value=True) as ou:
            self.assertEqual(bridge.read_clipboard(), "abc")
            self.assertTrue(bridge.copy_text("hi"))
            self.assertTrue(bridge.open_url("https://example.com"))
        rc.assert_called_once()
        wc.assert_called_once_with("hi")
        ou.assert_called_once_with("https://example.com")

    def test_open_url_refuses_non_https_without_side_effects(self):
        from app import winutil

        for bad in ("http://example.com", "file:///C:/Windows/notepad.exe", "javascript:alert(1)", "calc.exe", ""):
            self.assertFalse(winutil.open_url(bad), bad)


class AppUpdateTests(unittest.TestCase):
    def test_check_and_download_delegate(self):
        bridge, engine, *_ = make_bridge()
        self.assertEqual(bridge.check_updates()["stage"], "checking")
        self.assertIsNone(bridge.download_update())
        self.assertEqual(bridge.update_status()["stage"], "up_to_date")
        self.assertEqual([c[0] for c in engine.calls], ["check_updates", "download_update", "update_status"])

    def test_apply_update_rejects_when_the_engine_refuses(self):
        bridge, engine, window, pump, closed = make_bridge()
        engine.apply_result = False
        with self.assertRaises(BridgeError) as ctx:
            bridge.apply_update()
        self.assertEqual(str(ctx.exception), "The update is not ready to install.")
        self.assertFalse(closed.wait(0.3))
        self.assertEqual(window.calls, [])
        self.assertNotIn("shutdown", [c[0] for c in engine.calls])

    def test_apply_update_closes_the_window_when_the_engine_accepts(self):
        bridge, engine, window, pump, closed = make_bridge()
        engine.apply_result = True
        self.assertIsNone(bridge.apply_update())
        self.assertTrue(closed.wait(3))
        self.assertEqual(window.calls, ["hide", "destroy"])  # the same path as window_close
        self.assertEqual([c for c in engine.calls if c[0] == "shutdown"], [("shutdown",)])

    def test_engine_errors_in_apply_update_become_bridge_errors(self):
        bridge, engine, *_ = make_bridge()
        engine.fail_with = OSError(5, "Access is denied")
        with self.assertRaises(BridgeError):
            bridge.apply_update()


class FileActionTests(unittest.TestCase):
    def test_open_and_reveal_missing_file_toast_and_false(self):
        bridge, engine, window, pump, _ = make_bridge()
        pump.set_ready()
        engine.job_path = None
        self.assertFalse(bridge.open_file("a"))
        self.assertFalse(bridge.reveal_file("a"))
        pump.flush()
        self.assertIn("warning", "".join(window.scripts))

    def test_open_and_reveal_existing_file(self):
        import os
        import tempfile

        bridge, engine, *_ = make_bridge()
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "v.mp4")
            with open(path, "wb") as fh:
                fh.write(b"x")
            engine.job_path = path
            with mock.patch("app.bridge.winutil.open_path", return_value=True) as op, \
                    mock.patch("app.bridge.winutil.reveal_in_explorer", return_value=True) as rv:
                self.assertTrue(bridge.open_file("a"))
                self.assertTrue(bridge.reveal_file("a"))
            op.assert_called_once_with(path)
            rv.assert_called_once_with(path)

    def test_open_output_dir_creates_and_opens(self):
        import os
        import tempfile

        bridge, engine, *_ = make_bridge()
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, "new", "Media Saver")
            engine.settings["output_dir"] = target
            with mock.patch("app.bridge.winutil.open_path", return_value=True) as op:
                self.assertTrue(bridge.open_output_dir())
            op.assert_called_once_with(target)
            self.assertTrue(os.path.isdir(target))

    def test_open_logs_falls_back_to_root_logs(self):
        import os

        bridge, engine, *_ = make_bridge()
        with mock.patch("app.bridge.os.makedirs"), mock.patch("app.bridge.winutil.open_path", return_value=True) as op:
            bridge.open_logs()
        op.assert_called_once_with(os.path.join("C:\\scratch", "logs"))

    def test_choose_output_dir_cancel(self):
        bridge, engine, window, *_ = make_bridge()
        window.dialog_result = None
        self.assertIsNone(bridge.choose_output_dir())
        self.assertNotIn("set_settings", [c[0] for c in engine.calls])

    def test_choose_output_dir_saves_through_engine(self):
        bridge, engine, window, *_ = make_bridge()
        window.dialog_result = ("D:\\Reels",)
        self.assertEqual(bridge.choose_output_dir(), "D:\\Reels")
        self.assertIn(("set_settings", {"output_dir": "D:\\Reels"}), engine.calls)
        self.assertEqual(window.calls[0][:3], ("dialog", 20, "C:\\out"))

    def test_import_cookies_file(self):
        bridge, engine, window, *_ = make_bridge()
        window.dialog_result = None
        self.assertIsNone(bridge.import_cookies_file())
        window.dialog_result = ["C:\\cookies.txt"]
        self.assertEqual(bridge.import_cookies_file(), {"logged_in": True})
        self.assertIn(("import_cookies_file", "C:\\cookies.txt"), engine.calls)
        self.assertEqual(window.calls[-1][1], 10)


class ErrorTests(unittest.TestCase):
    def test_value_error_messages_pass_through(self):
        bridge, engine, *_ = make_bridge()
        engine.fail_with = ValueError("Concurrent downloads must be between 1 and 4.")
        with self.assertRaises(BridgeError) as ctx:
            bridge.set_settings({"concurrency": 9})
        self.assertEqual(str(ctx.exception), "Concurrent downloads must be between 1 and 4.")

    def test_raw_errors_get_a_friendly_wrapper(self):
        expected = {
            RuntimeError: "The operation failed. See the log for details.",
            PermissionError: "No access to the file or folder.",
            FileNotFoundError: "File or folder not found.",
        }
        for exc in (RuntimeError("boom"), PermissionError(13, "Access is denied"), FileNotFoundError(2, "nope")):
            message = friendly_message(exc)
            self.assertEqual(message, expected[type(exc)], repr(exc))
            self.assertNotIn("boom", message)
        self.assertEqual(friendly_message(KeyError("x")), "Invalid value: 'x'")
        self.assertEqual(friendly_message(OSError(28, "No space left on device")), "File system error: No space left on device")

    def test_exception_in_engine_is_rejected_with_message(self):
        bridge, engine, *_ = make_bridge()
        engine.fail_with = RuntimeError("secret internals")
        with self.assertRaises(BridgeError) as ctx:
            bridge.enqueue("https://www.instagram.com/reel/AAA/")
        self.assertNotIn("secret", str(ctx.exception))
        self.assertEqual(str(ctx.exception), "The operation failed. See the log for details.")

    def test_os_error_detail_is_kept(self):
        self.assertIn("No space", friendly_message(OSError(28, "No space left on device")))


class WindowTests(unittest.TestCase):
    def test_minimize(self):
        bridge, _, window, *_ = make_bridge()
        self.assertIsNone(bridge.window_minimize())
        self.assertEqual(window.calls, ["minimize"])

    def test_no_maximize_method(self):
        # the window is fixed-size: nothing in the API can maximize or restore it
        bridge, *_ = make_bridge()
        self.assertFalse(hasattr(bridge, "window_toggle_maximize"))
        self.assertFalse(hasattr(bridge, "_maximized"))

    def test_window_event_only_when_state_changes(self):
        bridge, _, window, pump, _ = make_bridge()
        pump.set_ready()
        bridge._on_window_state(focused=True)  # same as the default
        pump.flush()
        self.assertEqual(window.scripts, [])
        bridge._on_window_state(focused=False)
        pump.flush()
        self.assertIn('"focused":false', window.scripts[0])

    def test_window_event_payload_is_focus_only(self):
        bridge, _, window, pump, _ = make_bridge()
        bridge.ready()
        bridge._on_window_state(focused=False)
        pump.flush()
        text = "".join(window.scripts)
        self.assertIn('"window",{"focused":true}', text)
        self.assertIn('"window",{"focused":false}', text)
        self.assertNotIn("maximized", text)

    def test_close_shuts_engine_down_once_then_destroys(self):
        bridge, engine, window, pump, closed = make_bridge()
        self.assertIsNone(bridge.window_close())
        self.assertTrue(closed.wait(3))
        self.assertEqual(window.calls, ["hide", "destroy"])
        bridge._shutdown()  # the window `closing` handler runs it again: must be a no-op
        self.assertEqual([c for c in engine.calls if c[0] == "shutdown"], [("shutdown",)])


if __name__ == "__main__":
    unittest.main()
