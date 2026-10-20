"""Login window: cookie conversion and the polling / finish logic with a fake window."""

import threading
import unittest
from http.cookies import SimpleCookie
from unittest import mock

from app.login import LoginController, cookies_to_dicts, has_session


def morsel_cookie(name, value, domain=".instagram.com", path="/", expires="Mon, 01 Jan 0001 00:00:00 GMT",
                  secure=True, httponly=False):
    """Build the same SimpleCookie shape webview.util.create_cookie produces."""
    c = SimpleCookie()
    c[name] = value
    c[name]["path"] = path
    c[name]["domain"] = domain
    c[name]["expires"] = expires
    c[name]["secure"] = secure
    c[name]["httponly"] = httponly
    return c


class ConversionTests(unittest.TestCase):
    def test_http_only_flag_and_fields_survive(self):
        jar = [morsel_cookie("sessionid", "123%3Aabc", httponly=True, expires="Tue, 20 Oct 2026 10:00:00 GMT")]
        (cookie,) = cookies_to_dicts(jar)
        self.assertEqual(cookie["name"], "sessionid")
        self.assertEqual(cookie["value"], "123%3Aabc")
        self.assertEqual(cookie["domain"], ".instagram.com")
        self.assertTrue(cookie["http_only"])
        self.assertTrue(cookie["secure"])
        self.assertEqual(cookie["path"], "/")
        self.assertAlmostEqual(cookie["expires"], 1792490400.0, delta=1)

    def test_session_cookie_has_no_expiry(self):
        (cookie,) = cookies_to_dicts([morsel_cookie("csrftoken", "x")])
        self.assertIsNone(cookie["expires"])

    def test_foreign_domains_are_dropped(self):
        jar = [morsel_cookie("a", "1", domain=".facebook.com"), morsel_cookie("b", "2", domain="www.instagram.com"),
               morsel_cookie("c", "3", domain="notinstagram.com")]
        self.assertEqual([c["name"] for c in cookies_to_dicts(jar)], ["b"])

    def test_has_session(self):
        jar = cookies_to_dicts([morsel_cookie("sessionid", "v"), morsel_cookie("csrftoken", "t")])
        self.assertTrue(has_session(jar))
        self.assertFalse(has_session(cookies_to_dicts([morsel_cookie("sessionid", "")])))
        self.assertFalse(has_session(cookies_to_dicts([morsel_cookie("csrftoken", "t")])))


class FakeLoginWindow:
    def __init__(self, cookie_batches):
        self._batches = list(cookie_batches)
        self.destroyed = threading.Event()
        self.events = mock.MagicMock()
        self.polls = 0

    def get_cookies(self):
        self.polls += 1
        return self._batches.pop(0) if len(self._batches) > 1 else self._batches[0]

    def destroy(self):
        self.destroyed.set()

    def restore(self):
        pass

    def show(self):
        pass


class FakeEngine:
    def __init__(self):
        self.paths = mock.Mock(cookies_file="C:\\scratch\\auth\\cookies.txt")
        self.reloaded = threading.Event()

    def reload_auth(self):
        self.reloaded.set()
        return {"logged_in": True}


class ControllerTests(unittest.TestCase):
    def run_controller(self, batches, writer=None):
        window = FakeLoginWindow(batches)
        engine = FakeEngine()
        toasts = []
        written = []
        controller = LoginController(
            engine,
            lambda level, text: toasts.append((level, text)),
            create_window=lambda *a, **k: window,
            cookie_writer=writer or (lambda cookies, path: written.append((cookies, path))),
            poll_interval=0.01,
        )
        controller.open()
        controller._on_loaded()
        return controller, window, engine, toasts, written

    def test_waits_for_sessionid_then_saves_reloads_and_closes(self):
        anon = [morsel_cookie("csrftoken", "t")]
        logged_in = [morsel_cookie("csrftoken", "t"), morsel_cookie("sessionid", "9%3Az", httponly=True)]
        _, window, engine, toasts, written = self.run_controller([anon, anon, logged_in])
        self.assertTrue(window.destroyed.wait(3))
        self.assertTrue(engine.reloaded.is_set())
        self.assertEqual(len(written), 1)
        cookies, path = written[0]
        self.assertEqual(path, "C:\\scratch\\auth\\cookies.txt")
        self.assertEqual({c["name"] for c in cookies}, {"csrftoken", "sessionid"})
        self.assertTrue(next(c for c in cookies if c["name"] == "sessionid")["http_only"])
        self.assertEqual(toasts[0][0], "success")

    def test_user_closing_the_window_does_nothing(self):
        controller, window, engine, toasts, written = self.run_controller([[morsel_cookie("csrftoken", "t")]])
        controller._closed.set()  # what the `closed` event handler does
        threading.Event().wait(0.1)
        self.assertEqual(written, [])
        self.assertFalse(engine.reloaded.is_set())
        self.assertEqual(toasts, [])

    def test_writer_failure_reports_error_and_keeps_window(self):
        def boom(cookies, path):
            raise OSError("disk full")

        with self.assertLogs("reels.login", level="ERROR"):  # also keeps the expected traceback out of the test output
            _, window, engine, toasts, _ = self.run_controller([[morsel_cookie("sessionid", "v")]], writer=boom)
            threading.Event().wait(0.3)
        self.assertEqual(toasts[0][0], "error")
        self.assertFalse(window.destroyed.is_set())
        self.assertFalse(engine.reloaded.is_set())

    def test_open_twice_reuses_the_window(self):
        created = []
        window = FakeLoginWindow([[]])
        controller = LoginController(FakeEngine(), lambda *a: None, create_window=lambda *a, **k: created.append(1) or window)
        controller.open()
        controller.open()
        self.assertEqual(created, [1])


if __name__ == "__main__":
    unittest.main()
