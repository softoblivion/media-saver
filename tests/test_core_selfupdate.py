"""Media Saver's configuration of the vendored self-updater and the `--apply-update` route. No network.

The updater itself (check, download, verify, apply, startup cleanup, the apply mode) is tested where the package is
maintained; these tests pin the values Media Saver gives it and the calls the rest of the app relies on.
"""

import hashlib
import json
import logging
import os
import shutil
import tempfile
import unittest
import urllib.error
from unittest import mock

from app.core import selfupdate
from app.core import updater as engine_updater
from app.core.paths import AppPaths
from app.core.selfupdate import SelfUpdater
from app.vendor import selfupdate as lib

logging.getLogger("mediasaver.core.selfupdate").setLevel(logging.CRITICAL)  # expected failures log warnings

API = "https://api.github.com/repos/softoblivion/media-saver/releases/latest"
PAYLOAD = b"MZ" + b"new-exe-content" * 100


def dl(tag: str, name: str) -> str:
    return f"https://github.com/softoblivion/media-saver/releases/download/{tag}/{name}"


class FakeNet:
    def __init__(self, texts):
        self.texts = texts
        self.downloads: list[str] = []

    def get_text(self, url):
        if url not in self.texts:
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
        return self.texts[url]

    def download(self, url, dest, on_progress=None):
        self.downloads.append(url)
        with open(dest, "wb") as fh:
            fh.write(PAYLOAD)
        if on_progress:
            on_progress(len(PAYLOAD), len(PAYLOAD))
        return hashlib.sha256(PAYLOAD).hexdigest()


def release(tag):
    return json.dumps({"tag_name": tag, "body": "notes", "published_at": "2026-10-20T10:00:00Z", "assets": [
        {"name": "MediaSaver.exe", "size": len(PAYLOAD), "browser_download_url": dl(tag, "MediaSaver.exe")},
        {"name": "SHA256SUMS", "size": 90, "browser_download_url": dl(tag, "SHA256SUMS")}]})


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ms_selfupdate_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.paths = AppPaths(os.path.join(self.tmp, "home"))
        self.exe = os.path.join(self.tmp, "app", "MediaSaver.exe")
        self.statuses: list[dict] = []
        self.toasts: list[tuple] = []
        self.spawned: list[tuple] = []

    def make(self, net, version="2.0.0", exe_path="default"):
        return SelfUpdater(self.paths, version, self.statuses.append, net=net,
                           exe_path=self.exe if exe_path == "default" else exe_path,
                           on_toast=lambda level, text: self.toasts.append((level, text)),
                           spawn=lambda exe, argv, env, cwd: self.spawned.append((exe, argv, env, cwd)))

    def test_configuration_values(self):
        cfg = self.make(FakeNet({})).config
        self.assertEqual((cfg.repo, cfg.asset_name, cfg.app_name), ("softoblivion/media-saver", "MediaSaver.exe",
                                                                    "Media Saver"))
        self.assertEqual(cfg.version, "2.0.0")
        self.assertEqual(cfg.updates_dir, os.path.join(self.paths.root, "updates"))
        self.assertEqual(cfg.exe_path, self.exe)
        self.assertEqual(cfg.user_agent, engine_updater.UA)
        self.assertEqual(cfg.legacy_files, ("apply-update.cmd",))
        self.assertEqual(cfg.logger_name, "mediasaver.core.selfupdate")
        self.assertEqual(cfg.release_url_env, "MEDIA_SAVER_RELEASE_URL")

    def test_default_net_and_exe_path(self):
        up = SelfUpdater(self.paths, "2.0.0", self.statuses.append)
        self.assertIsInstance(up.net, lib.ReleaseNet)
        self.assertEqual(up.net.user_agent, engine_updater.UA)
        self.assertEqual(up.exe_path, self.paths.exe_path)  # None when running from source

    def test_messages_and_file_names_are_media_savers(self):
        net = FakeNet({API: release("v2.1.0"),
                       dl("v2.1.0", "SHA256SUMS"): f"{hashlib.sha256(PAYLOAD).hexdigest()}  MediaSaver.exe\n"})
        up = self.make(net)
        self.assertEqual(up.check()["message"], "Version 2.1.0 is available")
        self.assertEqual(up.download()["stage"], "ready")
        self.assertTrue(any(s["message"].startswith("Downloading Media Saver 2.1.0…") for s in self.statuses))
        self.assertTrue(os.path.isfile(os.path.join(self.paths.updates_dir, "MediaSaver-2.1.0.exe")))
        self.assertTrue(up.apply())
        ((exe, argv, _env, _cwd),) = self.spawned
        self.assertEqual(exe, os.path.join(self.paths.updates_dir, "MediaSaver-2.1.0.exe"))
        self.assertEqual(argv, ["--apply-update", str(os.getpid()), self.exe])
        uptodate = self.make(FakeNet({API: release("v2.0.0")})).check()
        self.assertEqual(uptodate["message"], "Media Saver is up to date")

    def test_startup_toast_and_legacy_helper_cleanup(self):
        updates = self.paths.updates_dir
        for name, data in (("applied.json", b'{"version": "2.1.0", "at": 1}'), ("apply-update.cmd", b"x")):
            with open(os.path.join(updates, name), "wb") as fh:
                fh.write(data)
        self.make(FakeNet({}), version="2.1.0").startup()
        self.assertEqual(self.toasts, [("success", "Media Saver updated to v2.1.0")])
        self.assertEqual(os.listdir(updates), [])

    def test_failures_log_to_the_app_logger(self):
        with self.assertLogs("mediasaver.core.selfupdate", level="WARNING"):
            self.make(FakeNet({API: release("v2.1.0")}), exe_path=None).apply()

    def test_shared_helpers_are_the_vendored_ones(self):
        from app.core import paths
        self.assertIs(engine_updater.OfflineError, lib.OfflineError)
        self.assertIs(engine_updater.Net, lib.Net)
        self.assertIs(engine_updater.parse_sums, lib.parse_sums)
        self.assertIs(paths.atomic_write_json, lib.atomic_write_json)
        self.assertIs(selfupdate.run_apply, lib.run_apply)
        self.assertEqual(selfupdate.APPLY_FLAG, "--apply-update")

    def test_vendored_copy_names_its_version(self):
        folder = os.path.dirname(lib.__file__)
        with open(os.path.join(folder, "VENDORED.txt"), encoding="utf-8") as fh:
            text = fh.read()
        self.assertEqual(text, f"name: selfupdate\nversion: {lib.__version__}\n")


class MainApplyModeTests(unittest.TestCase):
    def test_main_routes_the_apply_flag_before_the_window(self):
        from app import main as app_main
        with mock.patch("app.core.selfupdate.run_apply", return_value=7) as run:
            self.assertEqual(app_main.main(["--apply-update", "123", "C:\\x\\MediaSaver.exe"]), 7)
        run.assert_called_once_with(["123", "C:\\x\\MediaSaver.exe"])


if __name__ == "__main__":
    unittest.main()
