"""Updater logic with fake network and runner (no network)."""
import hashlib
import os
import shutil
import tempfile
import unittest

from app.core.downloader import ProcessResult
from app.core.paths import AppPaths
from app.core.updater import (OfflineError, Updater, parse_ffmpeg_version, parse_sums, parse_ytdlp_update)

NEW_EXE = b"new yt-dlp build"
NEW_SHA = hashlib.sha256(NEW_EXE).hexdigest()
GDL_EXE = b"gallery-dl build"
GDL_SHA = hashlib.sha256(GDL_EXE).hexdigest()


class FakeNet:
    def __init__(self, online=True):
        self.online = online
        self.downloads = []

    def reachable(self, url):
        return self.online

    def get_text(self, url):
        if not self.online:
            raise OfflineError(url)
        if url.endswith("SHA2-256SUMS"):
            return f"{NEW_SHA}  yt-dlp.exe\n{'0' * 64}  yt-dlp_x86.exe\n"
        if url.endswith("SHA256SUMS"):
            return f"{GDL_SHA}  gallery-dl.exe\n"
        raise AssertionError(url)

    def download(self, url, dest, on_progress=None):
        self.downloads.append(url)
        data = NEW_EXE if "yt-dlp" in url else GDL_EXE
        with open(dest, "wb") as fh:
            fh.write(data)
        if on_progress:
            on_progress(len(data), len(data))
        return hashlib.sha256(data).hexdigest()


class FakeRunner:
    def __init__(self, update_ok=True):
        self.calls = []
        self.update_ok = update_ok

    def run(self, argv, **kw):
        self.calls.append(argv)
        if "--version" in argv:
            with open(argv[0], "rb") as fh:
                data = fh.read()
            return ProcessResult(0, "2026.10.18.1" if data == NEW_EXE else "1.0", "")
        if "--update-to" in argv:
            if self.update_ok:
                with open(argv[0], "wb") as fh:
                    fh.write(NEW_EXE)
                return ProcessResult(0, "Updated yt-dlp to nightly@2026.10.18.1 from yt-dlp/yt-dlp-nightly-builds", "")
            return ProcessResult(100, "", "ERROR: Unable to write to yt-dlp.exe; Try running as administrator")
        if "-version" in argv:
            return ProcessResult(0, "ffmpeg version 9.0.2-full_build-www.gyan.dev Copyright", "")
        return ProcessResult(0, "", "")


class UpdaterTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="idl_upd_")
        self.paths = AppPaths(os.path.join(self.tmp, "home"))
        self.statuses = []
        self.settings = {"update_channel": "nightly", "auto_update": True}

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def make(self, net, runner):
        return Updater(self.paths, lambda: self.settings, self.statuses.append, net=net, runner=runner,
                       use_system_ffmpeg=False)

    def fake_ffmpeg(self):
        for n in ("ffmpeg.exe", "ffprobe.exe"):
            with open(self.paths.binary(n), "wb") as fh:
                fh.write(b"x")
        import json, time
        with open(self.paths.updater_state_file, "w") as fh:
            json.dump({"ffmpeg": {"downloaded_at": time.time()}}, fh)

    def test_fresh_install(self):
        self.fake_ffmpeg()
        net = FakeNet()
        up = self.make(net, FakeRunner())
        st = up.run()
        self.assertEqual(st["stage"], "ready", st)
        self.assertEqual(st["components"]["yt-dlp"]["version"], "2026.10.18.1")
        self.assertTrue(st["components"]["yt-dlp"]["updated"])
        self.assertEqual(st["components"]["ffmpeg"]["source"], "bundled")
        self.assertTrue(up.usable())
        self.assertFalse(os.path.exists(self.paths.binary("yt-dlp.exe") + ".part"))
        self.assertIn("downloading", [s["stage"] for s in self.statuses])

    def test_self_update_then_uptodate(self):
        self.fake_ffmpeg()
        with open(self.paths.binary("yt-dlp.exe"), "wb") as fh:
            fh.write(b"old build")
        runner = FakeRunner()
        net = FakeNet()
        st = self.make(net, runner).run()
        self.assertTrue(any("--update-to" in c for c in runner.calls))
        self.assertEqual(st["components"]["yt-dlp"]["version"], "2026.10.18.1")
        self.assertNotIn("yt-dlp", " ".join(net.downloads).replace("gallery-dl", ""))
        runner2 = FakeRunner()
        self.make(FakeNet(), runner2).run()
        self.assertFalse(any("--update-to" in c for c in runner2.calls))  # SHA matches: nothing to do

    def test_self_update_failure_falls_back_to_download(self):
        self.fake_ffmpeg()
        with open(self.paths.binary("yt-dlp.exe"), "wb") as fh:
            fh.write(b"old build")
        net = FakeNet()
        st = self.make(net, FakeRunner(update_ok=False)).run()
        self.assertEqual(st["stage"], "ready")
        self.assertTrue(any(u.endswith("yt-dlp.exe") for u in net.downloads))
        with open(self.paths.binary("yt-dlp.exe"), "rb") as fh:
            self.assertEqual(fh.read(), NEW_EXE)

    def test_offline_with_and_without_binaries(self):
        st = self.make(FakeNet(online=False), FakeRunner()).run()
        self.assertEqual(st["stage"], "error")
        self.fake_ffmpeg()
        with open(self.paths.binary("yt-dlp.exe"), "wb") as fh:
            fh.write(b"old build")
        up = self.make(FakeNet(online=False), FakeRunner())
        st = up.run()
        self.assertEqual(st["stage"], "offline")
        self.assertTrue(up.usable())

    def test_self_heal_once_per_session(self):
        self.fake_ffmpeg()
        up = self.make(FakeNet(), FakeRunner())
        self.assertTrue(up.self_heal())
        self.assertFalse(up.self_heal())

    def test_parsers(self):
        self.assertEqual(parse_sums("abc\n" + "A" * 64 + " *yt-dlp.exe\n"), {"yt-dlp.exe": "a" * 64})
        self.assertEqual(parse_ffmpeg_version("ffmpeg version 9.0.2-full_build-www.gyan.dev Copyright"), "9.0.2")
        self.assertEqual(parse_ffmpeg_version("ffmpeg version N-127709-g5993347a1b-20261019 Copyright"),
                         "N-127709-g5993347a1b-20261019")
        self.assertEqual(parse_ytdlp_update("yt-dlp is up to date (nightly@2026.10.10.233141 from x)"), "uptodate")
        self.assertEqual(parse_ytdlp_update("Updated yt-dlp to nightly@2026.10.10.233141 from x"), "updated")
        self.assertEqual(parse_ytdlp_update("ERROR: Unable to obtain version info"), "failed")


if __name__ == "__main__":
    unittest.main()
