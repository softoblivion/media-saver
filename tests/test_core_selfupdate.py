"""SelfUpdater: check, download + verify, apply, startup cleanup, the apply mode of the new exe. No network."""

import hashlib
import io
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import warnings
from unittest import mock

from app.core import selfupdate
from app.core.paths import AppPaths
from app.core.selfupdate import SelfUpdater
from app.core.updater import OfflineError

logging.getLogger("mediasaver.core.selfupdate").setLevel(logging.CRITICAL)  # expected failures log warnings

warnings.filterwarnings("ignore", category=ResourceWarning)  # fabricated HTTPError objects are never closed

REPO = "softoblivion/media-saver"
API = f"https://api.github.com/repos/{REPO}/releases/latest"
PAGE = f"https://github.com/{REPO}/releases/latest"
PAYLOAD = b"MZ" + b"new-exe-content" * 1000


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def dl(tag: str, name: str) -> str:
    return f"https://github.com/{REPO}/releases/download/{tag}/{name}"


def release_json(tag="v2.1.0", body="## Added\n- things", size=len(PAYLOAD), assets=("MediaSaver.exe", "SHA256SUMS")):
    return json.dumps({
        "tag_name": tag, "body": body, "published_at": "2026-10-20T10:00:00Z",
        "assets": [{"name": n, "size": size if n == "MediaSaver.exe" else 100, "browser_download_url": dl(tag, n)}
                   for n in assets],
    })


class FakeNet:
    """Canned answers per URL; an Exception value is raised. download() writes `payload` to dest."""

    def __init__(self, texts=None, payload=PAYLOAD, download_error=None):
        self.texts = dict(texts or {})
        self.payload = payload
        self.download_error = download_error
        self.calls: list[str] = []
        self.downloads: list[str] = []
        self.gate: threading.Event | None = None

    def get_text(self, url):
        self.calls.append(url)
        if self.gate is not None:
            self.gate.wait(5)
        value = self.texts.get(url)
        if value is None:
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, io.BytesIO(b""))
        if isinstance(value, BaseException):
            raise value
        return value

    def get_bytes(self, url, limit=0):
        return self.get_text(url).encode()

    def reachable(self, url):
        return True

    def download(self, url, dest, on_progress=None):
        self.downloads.append(url)
        if self.download_error is not None:
            raise self.download_error
        half = len(self.payload) // 2
        with open(dest, "wb") as fh:
            fh.write(self.payload[:half])
            if on_progress:
                on_progress(half, len(self.payload))
            fh.write(self.payload[half:])
        if on_progress:
            on_progress(len(self.payload), len(self.payload))
        return sha(self.payload)


class FakeResponse:
    def __init__(self, url, length=None):
        self._url = url
        self.headers = {"Content-Length": str(length)} if length is not None else {}

    def geturl(self):
        return self._url

    def close(self):
        pass


def fake_urlopen(routes):
    """routes: {(method, url): FakeResponse | Exception}; unknown routes behave as a connection failure."""
    calls = []

    def opener(request, timeout=None):
        key = (request.get_method(), request.full_url)
        calls.append(key)
        value = routes.get(key)
        if value is None:
            raise urllib.error.URLError("unreachable")
        if isinstance(value, BaseException):
            raise value
        return value

    opener.calls = calls
    return opener


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ms_selfupdate_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.paths = AppPaths(os.path.join(self.tmp, "home"))
        self.exe = os.path.join(self.tmp, "app", "MediaSaver.exe")
        os.makedirs(os.path.dirname(self.exe))
        with open(self.exe, "wb") as fh:
            fh.write(b"OLD-EXE")
        self.statuses: list[dict] = []
        self.toasts: list[tuple] = []
        self.spawned: list[tuple] = []

    def make(self, net=None, urlopen=None, exe_path="default", version="2.0.0"):
        return SelfUpdater(
            self.paths, version, self.statuses.append, net=net or FakeNet(),
            exe_path=self.exe if exe_path == "default" else exe_path,
            on_toast=lambda level, text: self.toasts.append((level, text)),
            spawn=lambda exe, argv, env, cwd: self.spawned.append((exe, argv, env, cwd)),
            urlopen=urlopen or fake_urlopen({}),
        )

    def api_net(self, **kwargs):
        texts = {API: release_json(**kwargs.pop("release", {})),
                 dl("v2.1.0", "SHA256SUMS"): f"{sha(PAYLOAD)}  MediaSaver.exe\n"}
        texts.update(kwargs.pop("texts", {}))
        return FakeNet(texts, **kwargs)

    def ready_updater(self, **kwargs):
        up = self.make(self.api_net(**kwargs))
        up.check()
        up.download()
        self.assertEqual(up.status()["stage"], "ready")
        return up


class StatusTests(Base):
    def test_initial_status(self):
        s = self.make().status()
        self.assertEqual(s, {
            "stage": "idle", "current": "2.0.0", "latest": None, "notes": None, "size": None, "published_at": None,
            "progress": None, "message": "Updates are not checked yet", "checked_at": None, "can_apply": False})

    def test_status_is_a_copy_and_callback_errors_are_swallowed(self):
        def boom(_status):
            raise RuntimeError("ui gone")

        up = SelfUpdater(self.paths, "2.0.0", boom, net=self.api_net(), exe_path=self.exe)
        with self.assertLogs("mediasaver.core.selfupdate", level="ERROR"):
            up.check()
        up.status()["stage"] = "hacked"
        self.assertEqual(up.status()["stage"], "available")


class VersionTests(unittest.TestCase):
    def test_parse_and_compare(self):
        self.assertEqual(selfupdate.parse_version("v2.1.0"), (2, 1, 0))
        self.assertEqual(selfupdate.parse_version("2.1"), (2, 1))
        self.assertEqual(selfupdate.parse_version("2.0.0-beta.1"), (2, 0, 0))
        self.assertIsNone(selfupdate.parse_version("nightly"))
        self.assertIsNone(selfupdate.parse_version(""))
        self.assertTrue(selfupdate.is_newer((2, 1, 0), (2, 0, 9)))
        self.assertTrue(selfupdate.is_newer((10, 0, 0), (9, 9, 9)))
        self.assertFalse(selfupdate.is_newer((2, 0), (2, 0, 0)))
        self.assertFalse(selfupdate.is_newer((1, 9, 9), (2, 0, 0)))


class CheckTests(Base):
    def test_available_from_api(self):
        up = self.make(self.api_net())
        status = up.check()
        self.assertEqual(status["stage"], "available")
        self.assertEqual(status["latest"], "2.1.0")
        self.assertEqual(status["message"], "Version 2.1.0 is available")
        self.assertEqual(status["notes"], "## Added\n- things")
        self.assertEqual(status["size"], len(PAYLOAD))
        self.assertGreater(status["published_at"], 1.7e9)
        self.assertIsNotNone(status["checked_at"])
        self.assertFalse(status["can_apply"])
        self.assertEqual([s["stage"] for s in self.statuses], ["checking", "available"])

    def test_empty_notes_become_none(self):
        status = self.make(self.api_net(release={"body": ""})).check()
        self.assertIsNone(status["notes"])

    def test_up_to_date(self):
        status = self.make(self.api_net(release={"tag": "v2.0.0"})).check()
        self.assertEqual(status["stage"], "up_to_date")
        self.assertEqual(status["message"], "Media Saver is up to date")
        self.assertIsNotNone(status["checked_at"])
        older = self.make(self.api_net(release={"tag": "v1.0.0"})).check()
        self.assertEqual(older["stage"], "up_to_date")

    def test_offline(self):
        net = FakeNet({API: OfflineError("api: connection failed")})
        status = self.make(net).check()
        self.assertEqual((status["stage"], status["message"]), ("error", "No internet connection"))
        self.assertIsNone(status["checked_at"])

    def test_api_limit_without_fallback_answer(self):
        net = FakeNet({API: OfflineError(f"{API}: HTTP 403")})
        opener = fake_urlopen({("GET", PAGE): urllib.error.HTTPError(PAGE, 429, "Too Many", {}, io.BytesIO(b""))})
        status = self.make(net, opener).check()
        self.assertEqual(status["message"], "GitHub limited the request, try again later")

    def test_api_403_falls_back_to_the_redirect_url(self):
        net = FakeNet({API: OfflineError(f"{API}: HTTP 403")})
        asset = dl("v2.1.0", "MediaSaver.exe")
        opener = fake_urlopen({
            ("GET", PAGE): FakeResponse(f"https://github.com/{REPO}/releases/tag/v2.1.0"),
            ("HEAD", asset): FakeResponse(asset, length=len(PAYLOAD)),
        })
        status = self.make(net, opener).check()
        self.assertEqual(status["stage"], "available")
        self.assertEqual(status["latest"], "2.1.0")
        self.assertEqual(status["size"], len(PAYLOAD))
        self.assertIsNone(status["notes"])
        self.assertIsNone(status["published_at"])
        self.assertIn(("HEAD", asset), opener.calls)

    def test_fallback_without_content_length_gives_unknown_size(self):
        net = FakeNet({API: ValueError("not json")})
        asset = dl("v2.1.0", "MediaSaver.exe")
        opener = fake_urlopen({
            ("GET", PAGE): FakeResponse(f"https://github.com/{REPO}/releases/tag/v2.1.0"),
            ("HEAD", asset): FakeResponse(asset),
        })
        status = self.make(net, opener).check()
        self.assertEqual(status["stage"], "available")
        self.assertIsNone(status["size"])

    def test_fallback_missing_asset(self):
        net = FakeNet({API: OfflineError("x: HTTP 403")})
        asset = dl("v2.1.0", "MediaSaver.exe")
        opener = fake_urlopen({
            ("GET", PAGE): FakeResponse(f"https://github.com/{REPO}/releases/tag/v2.1.0"),
            ("HEAD", asset): urllib.error.HTTPError(asset, 404, "Not Found", {}, io.BytesIO(b"")),
        })
        status = self.make(net, opener).check()
        self.assertEqual(status["message"], "The release has no MediaSaver.exe")

    def test_fallback_offline(self):
        net = FakeNet({API: OfflineError("x: connection failed")})
        status = self.make(net, fake_urlopen({})).check()
        self.assertEqual(status["message"], "No internet connection")

    def test_fallback_without_a_release(self):
        net = FakeNet({API: OfflineError("x: HTTP 403")})
        opener = fake_urlopen({("GET", PAGE): FakeResponse(f"https://github.com/{REPO}/releases")})
        self.assertEqual(self.make(net, opener).check()["message"], "No release has been published yet")

    def test_bad_tag(self):
        status = self.make(self.api_net(release={"tag": "nightly"})).check()
        self.assertEqual((status["stage"], status["message"]), ("error", "Could not read the release version"))
        self.assertIsNone(status["latest"])

    def test_missing_asset(self):
        status = self.make(self.api_net(release={"assets": ("SHA256SUMS",)})).check()
        self.assertEqual((status["stage"], status["message"]), ("error", "The release has no MediaSaver.exe"))

    def test_error_clears_a_previous_result(self):
        net = self.api_net()
        up = self.make(net)
        up.check()
        net.texts[API] = OfflineError("x: connection failed")
        status = up.check()
        self.assertEqual(status["stage"], "error")
        self.assertIsNone(status["latest"])
        self.assertEqual(up.download()["stage"], "error")  # nothing to download any more
        self.assertEqual(net.downloads, [])

    def test_existing_verified_download_goes_straight_to_ready(self):
        up = self.make(self.api_net())
        with open(os.path.join(self.paths.updates_dir, "MediaSaver-2.1.0.exe"), "wb") as fh:
            fh.write(PAYLOAD)
        status = up.check()
        self.assertEqual(status["stage"], "ready")
        self.assertTrue(status["can_apply"])
        self.assertEqual(status["message"], "Version 2.1.0 is ready to install")

    def test_existing_corrupt_download_is_not_trusted(self):
        up = self.make(self.api_net())
        with open(os.path.join(self.paths.updates_dir, "MediaSaver-2.1.0.exe"), "wb") as fh:
            fh.write(b"x" * len(PAYLOAD))  # right size, wrong content
        self.assertEqual(up.check()["stage"], "available")

    def test_request_headers_for_the_api(self):
        seen = []

        class Resp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def fake_open(request, timeout=None):
            seen.append(dict(request.header_items()))
            return Resp()

        with mock.patch("app.core.selfupdate.urllib.request.urlopen", fake_open):
            selfupdate.ReleaseNet()._open(API)
            selfupdate.ReleaseNet()._open("https://github.com/x")
        self.assertEqual(seen[0]["Accept"], "application/vnd.github+json")
        self.assertEqual(seen[0]["X-github-api-version"], "2022-11-28")
        self.assertIn("User-agent", seen[0])
        self.assertNotIn("Accept", seen[1])

    def test_second_check_while_running_is_ignored(self):
        net = self.api_net()
        net.gate = threading.Event()
        up = self.make(net)
        up.check_background()
        up.check_background()
        self.assertEqual(up.status()["stage"], "checking")
        net.gate.set()
        deadline = time.time() + 5
        while up.status()["stage"] == "checking" and time.time() < deadline:
            time.sleep(0.02)
        self.assertEqual(up.status()["stage"], "available")
        self.assertEqual(net.calls.count(API), 1)


class DownloadTests(Base):
    def test_download_ok(self):
        net = self.api_net()
        up = self.make(net)
        up.check()
        status = up.download()
        self.assertEqual(status["stage"], "ready")
        self.assertEqual(status["message"], "Version 2.1.0 is ready to install")
        self.assertTrue(status["can_apply"])
        self.assertIsNone(status["progress"])
        final = os.path.join(self.paths.updates_dir, "MediaSaver-2.1.0.exe")
        with open(final, "rb") as fh:
            self.assertEqual(fh.read(), PAYLOAD)
        self.assertFalse(os.path.exists(final + ".part"))
        self.assertEqual(net.downloads, [dl("v2.1.0", "MediaSaver.exe")])
        downloading = [s for s in self.statuses if s["stage"] == "downloading"]
        self.assertTrue(downloading)
        self.assertTrue(any("%" in s["message"] and s["message"].startswith("Downloading Media Saver 2.1.0…")
                            for s in downloading))
        self.assertTrue(all(0 <= s["progress"] <= 1 for s in downloading))

    def test_download_from_source_adds_a_hint_and_cannot_apply(self):
        up = self.make(self.api_net(), exe_path=None)
        up.exe_path = None
        up.check()
        status = up.download()
        self.assertEqual(status["stage"], "ready")
        self.assertFalse(status["can_apply"])
        self.assertTrue(status["message"].endswith("(applies to the packaged MediaSaver.exe only)"))

    def test_download_needs_an_available_release(self):
        net = self.api_net()
        up = self.make(net)
        self.assertEqual(up.download()["stage"], "idle")
        self.assertEqual(net.downloads, [])

    def test_size_mismatch(self):
        up = self.make(self.api_net(release={"size": len(PAYLOAD) + 5}))
        up.check()
        status = up.download()
        self.assertEqual((status["stage"], status["message"]), ("error", "The downloaded file is damaged (checksum mismatch)"))
        self.assertEqual(os.listdir(self.paths.updates_dir), [])

    def test_sha_mismatch(self):
        sums = {dl("v2.1.0", "SHA256SUMS"): f"{sha(b'other')}  MediaSaver.exe\n"}
        up = self.make(self.api_net(texts=sums))
        up.check()
        status = up.download()
        self.assertEqual(status["message"], "The downloaded file is damaged (checksum mismatch)")
        self.assertEqual(os.listdir(self.paths.updates_dir), [])
        self.assertFalse(status["can_apply"])

    def test_missing_sums_file(self):
        net = self.api_net()
        del net.texts[dl("v2.1.0", "SHA256SUMS")]
        up = self.make(net)
        up.check()
        status = up.download()
        self.assertEqual((status["stage"], status["message"]), ("error", "The release has no checksum file"))
        self.assertEqual(net.downloads, [])

    def test_sums_without_our_entry(self):
        up = self.make(self.api_net(texts={dl("v2.1.0", "SHA256SUMS"): f"{sha(b'x')}  other.exe\n"}))
        up.check()
        self.assertEqual(up.download()["message"], "The release has no checksum file")

    def test_network_failure_keeps_a_retry_possible(self):
        net = self.api_net(download_error=OfflineError("x: connection failed"))
        up = self.make(net)
        up.check()
        status = up.download()
        self.assertEqual((status["stage"], status["message"]), ("error", "No internet connection"))
        self.assertEqual(status["latest"], "2.1.0")
        net.download_error = None
        self.assertEqual(up.download()["stage"], "ready")

    def test_disk_error(self):
        up = self.make(self.api_net(download_error=OSError(28, "No space left on device")))
        up.check()
        self.assertEqual(up.download()["message"], "Could not save the update file")

    def test_download_background_runs_in_a_thread(self):
        up = self.make(self.api_net())
        up.check()
        up.download_background()
        deadline = time.time() + 5
        while up.status()["stage"] != "ready" and time.time() < deadline:
            time.sleep(0.02)
        self.assertEqual(up.status()["stage"], "ready")


class ApplyTests(Base):
    def test_refused_when_not_ready(self):
        up = self.make(self.api_net())
        up.check()  # available, not downloaded
        self.assertFalse(up.apply())
        self.assertEqual(up.status()["stage"], "error")
        self.assertEqual(self.spawned, [])

    def test_refused_without_exe_path(self):
        up = self.make(self.api_net(), exe_path=None)
        up.exe_path = None
        up.check()
        up.download()
        self.assertFalse(up.apply())
        self.assertEqual(up.status()["stage"], "error")
        self.assertEqual(self.spawned, [])

    def test_refused_when_the_file_changed(self):
        up = self.ready_updater()
        with open(os.path.join(self.paths.updates_dir, "MediaSaver-2.1.0.exe"), "ab") as fh:
            fh.write(b"tampered")
        self.assertFalse(up.apply())
        self.assertEqual(up.status()["message"], "The downloaded file is damaged (checksum mismatch)")
        self.assertEqual(self.spawned, [])
        self.assertFalse(os.path.exists(os.path.join(self.paths.updates_dir, "MediaSaver-2.1.0.exe")))

    def test_refused_when_the_file_is_gone(self):
        up = self.ready_updater()
        os.remove(os.path.join(self.paths.updates_dir, "MediaSaver-2.1.0.exe"))
        self.assertFalse(up.apply())
        self.assertEqual(up.status()["stage"], "error")

    def test_apply_writes_marker_and_starts_the_new_exe_in_apply_mode(self):
        up = self.ready_updater()
        self.assertTrue(up.apply())
        updates = self.paths.updates_dir
        with open(os.path.join(updates, "applied.json"), encoding="utf-8") as fh:
            marker = json.load(fh)
        self.assertEqual(marker["version"], "2.1.0")
        self.assertAlmostEqual(marker["at"], time.time(), delta=60)
        ((exe, argv, env, cwd),) = self.spawned
        self.assertEqual(exe, os.path.join(updates, "MediaSaver-2.1.0.exe"))
        self.assertEqual(argv, ["--apply-update", str(os.getpid()), self.exe])
        self.assertEqual(cwd, updates)
        self.assertEqual(env["PYINSTALLER_RESET_ENVIRONMENT"], "1")
        self.assertFalse(any(k.upper().startswith("_PYI") for k in env))
        self.assertFalse(os.path.exists(os.path.join(updates, "apply-update.cmd")))

    def test_apply_without_launch_passes_the_flag(self):
        up = self.ready_updater()
        self.assertTrue(up.apply(launch=False))
        ((_, argv, _, _),) = self.spawned
        self.assertEqual(argv[-1], "--no-launch")

    def test_spawn_failure_reports_error_and_removes_the_marker(self):
        up = self.ready_updater()
        up._spawn = mock.Mock(side_effect=OSError("denied"))
        with self.assertLogs("mediasaver.core.selfupdate", level="ERROR"):
            self.assertFalse(up.apply())
        self.assertEqual(up.status()["message"], "Could not start the update helper")
        self.assertFalse(os.path.exists(os.path.join(self.paths.updates_dir, "applied.json")))


class StartupTests(Base):
    def test_toast_when_the_marker_matches_and_old_exe_is_deleted(self):
        updates = self.paths.updates_dir
        with open(self.exe + ".old", "wb") as fh:
            fh.write(b"old")
        with open(os.path.join(updates, "applied.json"), "w", encoding="utf-8") as fh:
            json.dump({"version": "2.1.0", "at": 1}, fh)
        self.make(version="2.1.0").startup()
        self.assertEqual(self.toasts, [("success", "Media Saver updated to v2.1.0")])
        self.assertFalse(os.path.exists(self.exe + ".old"))
        self.assertFalse(os.path.exists(os.path.join(updates, "applied.json")))

    def test_stale_marker_is_removed_without_toast(self):
        updates = self.paths.updates_dir
        with open(os.path.join(updates, "applied.json"), "w", encoding="utf-8") as fh:
            json.dump({"version": "2.1.0", "at": 1}, fh)
        with self.assertLogs("mediasaver.core.selfupdate", level="WARNING"):
            self.make(version="2.0.0").startup()
        self.assertEqual(self.toasts, [])
        self.assertFalse(os.path.exists(os.path.join(updates, "applied.json")))

    def test_leftover_downloads_up_to_the_running_version_are_deleted(self):
        updates = self.paths.updates_dir
        names = ["MediaSaver-1.9.0.exe", "MediaSaver-2.0.0.exe", "MediaSaver-2.0.0.exe.part",
                 "MediaSaver-2.2.0.exe", "MediaSaver-2.2.0.exe.part", "notes.txt", "apply-update.cmd"]
        for name in names:
            with open(os.path.join(updates, name), "wb") as fh:
                fh.write(b"x")
        self.make(version="2.1.0").startup()
        self.assertEqual(sorted(os.listdir(updates)), ["MediaSaver-2.2.0.exe", "MediaSaver-2.2.0.exe.part", "notes.txt"])

    def test_broken_marker_and_missing_folders_never_raise(self):
        with open(os.path.join(self.paths.updates_dir, "applied.json"), "w", encoding="utf-8") as fh:
            fh.write("{not json")
        self.make().startup()
        self.assertEqual(self.toasts, [])
        up = self.make(exe_path=None)
        up.exe_path = None
        up.startup()

    def test_toast_callback_failure_is_swallowed(self):
        with open(os.path.join(self.paths.updates_dir, "applied.json"), "w", encoding="utf-8") as fh:
            json.dump({"version": "2.0.0"}, fh)
        up = self.make()
        up.on_toast = mock.Mock(side_effect=RuntimeError("x"))
        with self.assertLogs("mediasaver.core.selfupdate", level="ERROR"):
            up.startup()


class ApplyModeTests(Base):
    """`run_apply`: the downloaded exe waits for the old process, swaps the file and restarts the app."""

    def files(self, new_content=b"NEW"):
        target = os.path.join(self.tmp, "Target Dir (x86)", "MediaSaver.exe")
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "wb") as fh:
            fh.write(b"OLD")
        new_file = os.path.join(self.paths.updates_dir, "MediaSaver-9.9.9.exe")
        if new_content is not None:
            with open(new_file, "wb") as fh:
                fh.write(new_content)
        return target, new_file, os.path.join(self.paths.updates_dir, "apply.log")

    def test_waits_for_the_process_swaps_keeps_old_and_launches(self):
        target, new_file, log_path = self.files()
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(2)"])
        self.addCleanup(proc.wait)
        launched: list[tuple] = []
        started = time.time()
        rc = selfupdate.run_apply([str(proc.pid), target], new_file=new_file, log_path=log_path,
                                  launch=lambda t, env: launched.append((t, env)))
        elapsed = time.time() - started
        self.assertEqual(rc, 0)
        self.assertIsNotNone(proc.poll(), "returned while the watched process was still running")
        self.assertGreater(elapsed, 1.0)
        with open(target, "rb") as fh:
            self.assertEqual(fh.read(), b"NEW")
        with open(target + ".old", "rb") as fh:
            self.assertEqual(fh.read(), b"OLD")
        self.assertTrue(os.path.exists(new_file))  # the running copy stays; startup() of the new version removes it
        ((t, env),) = launched
        self.assertEqual(t, target)
        self.assertEqual(env["PYINSTALLER_RESET_ENVIRONMENT"], "1")
        with open(log_path, encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("exited", text)
        self.assertIn("swap done", text)
        self.assertIn("started the new exe", text)

    def test_no_launch_flag_and_an_already_gone_pid(self):
        target, new_file, log_path = self.files()
        proc = subprocess.Popen([sys.executable, "-c", "pass"])
        proc.wait()
        launched: list[tuple] = []
        rc = selfupdate.run_apply([str(proc.pid), target, "--no-launch"], new_file=new_file, log_path=log_path,
                                  launch=lambda t, env: launched.append((t, env)))
        self.assertEqual(rc, 0)
        self.assertEqual(launched, [])
        with open(target, "rb") as fh:
            self.assertEqual(fh.read(), b"NEW")

    def test_restores_the_old_exe_when_the_new_file_is_missing(self):
        target, new_file, log_path = self.files(new_content=None)
        rc = selfupdate.run_apply(["1", target, "--no-launch"], new_file=new_file, log_path=log_path,
                                  wait=lambda pid, t: True, launch=lambda t, env: None)
        self.assertEqual(rc, 1)
        with open(target, "rb") as fh:
            self.assertEqual(fh.read(), b"OLD")
        self.assertFalse(os.path.exists(target + ".old"))
        with open(log_path, encoding="utf-8") as fh:
            self.assertIn("swap failed, old exe kept", fh.read())

    def test_bad_arguments(self):
        _, new_file, log_path = self.files()
        self.assertEqual(selfupdate.run_apply([], new_file=new_file, log_path=log_path), 1)
        self.assertEqual(selfupdate.run_apply(["x", "y"], new_file=new_file, log_path=log_path), 1)

    def test_launch_failure_is_reported(self):
        target, new_file, log_path = self.files()

        def boom(t, env):
            raise OSError("no")
        rc = selfupdate.run_apply(["1", target], new_file=new_file, log_path=log_path,
                                  wait=lambda pid, t: True, launch=boom)
        self.assertEqual(rc, 1)
        with open(target, "rb") as fh:
            self.assertEqual(fh.read(), b"NEW")  # the swap itself succeeded

    def test_wait_for_pid(self):
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(1.5)"])
        self.addCleanup(proc.wait)
        self.assertFalse(selfupdate.wait_for_pid(proc.pid, 0.3))
        self.assertTrue(selfupdate.wait_for_pid(proc.pid, 10))
        self.assertTrue(selfupdate.wait_for_pid(proc.pid, 1))  # already gone

    def test_clean_env_drops_pyinstaller_variables(self):
        env = selfupdate.clean_env({"PATH": "p", "_PYI_APPLICATION_HOME_DIR": "x", "_MEIPASS2": "y"})
        self.assertEqual(env["PATH"], "p")
        self.assertNotIn("_PYI_APPLICATION_HOME_DIR", env)
        self.assertNotIn("_MEIPASS2", env)
        self.assertEqual(env["PYINSTALLER_RESET_ENVIRONMENT"], "1")


class MainApplyModeTests(unittest.TestCase):
    def test_main_routes_the_apply_flag_before_the_window(self):
        from app import main as app_main
        with mock.patch("app.core.selfupdate.run_apply", return_value=7) as run:
            self.assertEqual(app_main.main(["--apply-update", "123", "C:\\x\\MediaSaver.exe"]), 7)
        run.assert_called_once_with(["123", "C:\\x\\MediaSaver.exe"])


if __name__ == "__main__":
    unittest.main()
