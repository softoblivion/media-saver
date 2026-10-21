"""Updater logic with fake network and runner (no network)."""
import hashlib
import logging
import io
import json
import os
import shutil
import tempfile
import time
import unittest
import zipfile

from app.core.downloader import ProcessResult
from app.core.paths import AppPaths
from app.core.updater import (DENO_REFRESH_S, OfflineError, Updater, parse_deno_version, parse_ffmpeg_version,
                              parse_hash_sidecar, parse_sums, parse_ytdlp_update)

NEW_EXE = b"new yt-dlp build"
NEW_SHA = hashlib.sha256(NEW_EXE).hexdigest()
GDL_EXE = b"gallery-dl build"
GDL_SHA = hashlib.sha256(GDL_EXE).hexdigest()
DENO_EXE = b"MZ deno runtime"
DENO_EXE_SHA = hashlib.sha256(DENO_EXE).hexdigest()


def deno_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("deno.exe", DENO_EXE)
    return buf.getvalue()


DENO_ZIP = deno_zip()
DENO_ZIP_SHA = hashlib.sha256(DENO_ZIP).hexdigest()

logging.getLogger("mediasaver.core.updater").setLevel(logging.CRITICAL)  # the bad-checksum test logs an exception


def sidecar(sha: str, name: str) -> str:
    """The PowerShell Get-FileHash layout that denoland/deno publishes (CRLF, upper-case hash)."""
    return f"\r\nAlgorithm : SHA256\r\nHash      : {sha.upper()}\r\nPath      : C:\\a\\deno\\deno\\target\\release\\{name}\r\n\r\n"


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
        if url.endswith("deno-x86_64-pc-windows-msvc.zip.sha256sum"):
            return sidecar(DENO_ZIP_SHA, "deno-x86_64-pc-windows-msvc.zip")
        if url.endswith("deno-x86_64-pc-windows-msvc.sha256sum"):
            return sidecar(DENO_EXE_SHA, "deno.exe")
        raise AssertionError(url)

    def download(self, url, dest, on_progress=None):
        self.downloads.append(url)
        data = NEW_EXE if "yt-dlp" in url else DENO_ZIP if "deno" in url else GDL_EXE
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
            if os.path.basename(argv[0]) == "deno.exe":
                return ProcessResult(0, "deno 2.9.7 (stable, release, x86_64-pc-windows-msvc)\nv8 15.0\n", "")
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
        self.write_state({"ffmpeg": {"downloaded_at": time.time()}})

    def write_state(self, patch):
        state = {}
        if os.path.exists(self.paths.updater_state_file):
            with open(self.paths.updater_state_file) as fh:
                state = json.load(fh)
        state.update(patch)
        with open(self.paths.updater_state_file, "w") as fh:
            json.dump(state, fh)

    def fake_deno(self, age_s=0.0, zip_sha=DENO_ZIP_SHA):
        with open(self.paths.binary("deno.exe"), "wb") as fh:
            fh.write(DENO_EXE)
        self.write_state({"deno": {"downloaded_at": time.time() - age_s, "zip_sha": zip_sha}})

    def deno_downloads(self, net):
        return [u for u in net.downloads if "deno" in u]

    def test_fresh_install(self):
        self.fake_ffmpeg()
        net = FakeNet()
        up = self.make(net, FakeRunner())
        st = up.run()
        self.assertEqual(st["stage"], "ready", st)
        self.assertEqual(st["components"]["yt-dlp"]["version"], "2026.10.18.1")
        self.assertTrue(st["components"]["yt-dlp"]["updated"])
        self.assertEqual(st["components"]["ffmpeg"]["source"], "bundled")
        self.assertEqual(st["components"]["deno"], {"version": "2.9.7", "source": "bundled", "ok": True})
        self.assertEqual(up.binaries()["deno"], self.paths.binary("deno.exe"))
        with open(self.paths.binary("deno.exe"), "rb") as fh:
            self.assertEqual(fh.read(), DENO_EXE)
        self.assertFalse(os.path.exists(os.path.join(self.paths.cache_dir, "deno-x86_64-pc-windows-msvc.zip")))
        self.assertFalse(os.path.exists(self.paths.binary("deno.exe") + ".part"))
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
        self.assertFalse(st["components"]["deno"]["ok"])  # not installed, still usable without it
        self.assertIsNone(up.binaries()["deno"])

    def test_deno_up_to_date_is_not_downloaded(self):
        self.fake_ffmpeg()
        self.fake_deno()
        net = FakeNet()
        st = self.make(net, FakeRunner()).run()
        self.assertEqual(st["stage"], "ready", st)
        self.assertEqual(st["components"]["deno"], {"version": "2.9.7", "source": "bundled", "ok": True})
        self.assertEqual(self.deno_downloads(net), [])

    def test_deno_refresh_after_30_days_keeps_latest(self):
        self.fake_ffmpeg()
        self.fake_deno(age_s=DENO_REFRESH_S + 10)
        net = FakeNet()
        self.make(net, FakeRunner()).run()
        self.assertEqual(self.deno_downloads(net), [])  # sidecar SHA equals the stored one: only the timestamp moves
        with open(self.paths.updater_state_file) as fh:
            self.assertGreater(json.load(fh)["deno"]["downloaded_at"], time.time() - 60)

    def test_deno_refresh_after_30_days_installs_new_release(self):
        self.fake_ffmpeg()
        self.fake_deno(age_s=DENO_REFRESH_S + 10, zip_sha="0" * 64)
        net = FakeNet()
        st = self.make(net, FakeRunner()).run()
        self.assertEqual(len(self.deno_downloads(net)), 1)
        self.assertTrue(st["components"]["deno"]["ok"])
        with open(self.paths.updater_state_file) as fh:
            self.assertEqual(json.load(fh)["deno"]["zip_sha"], DENO_ZIP_SHA)

    def test_deno_missing_is_installed_even_without_auto_update(self):
        self.fake_ffmpeg()
        with open(self.paths.binary("yt-dlp.exe"), "wb") as fh:
            fh.write(NEW_EXE)
        self.settings["auto_update"] = False
        net = FakeNet()
        st = self.make(net, FakeRunner()).run()
        self.assertEqual(st["stage"], "ready", st)
        self.assertEqual(len(self.deno_downloads(net)), 1)
        self.assertTrue(st["components"]["deno"]["ok"])

    def test_deno_offline_keeps_installed_copy(self):
        self.fake_ffmpeg()
        self.fake_deno(age_s=DENO_REFRESH_S + 10)
        with open(self.paths.binary("yt-dlp.exe"), "wb") as fh:
            fh.write(NEW_EXE)
        up = self.make(FakeNet(online=False), FakeRunner())
        st = up.run()
        self.assertEqual(st["stage"], "offline")
        self.assertEqual(st["components"]["deno"], {"version": "2.9.7", "source": "bundled", "ok": True})
        self.assertEqual(up.binaries()["deno"], self.paths.binary("deno.exe"))

    def test_deno_bad_zip_sha_leaves_bin_clean(self):
        self.fake_ffmpeg()
        with open(self.paths.binary("yt-dlp.exe"), "wb") as fh:
            fh.write(NEW_EXE)

        class BadNet(FakeNet):
            def get_text(self, url):
                if url.endswith("deno-x86_64-pc-windows-msvc.zip.sha256sum"):
                    return sidecar("f" * 64, "deno-x86_64-pc-windows-msvc.zip")
                return super().get_text(url)

        st = self.make(BadNet(), FakeRunner()).run()
        self.assertEqual(st["stage"], "ready")  # one failing component never blocks the engine
        self.assertFalse(st["components"]["deno"]["ok"])
        self.assertFalse(os.path.exists(self.paths.binary("deno.exe")))
        self.assertFalse(os.path.exists(self.paths.binary("deno.exe") + ".part"))
        self.assertFalse(os.path.exists(os.path.join(self.paths.cache_dir, "deno-x86_64-pc-windows-msvc.zip.part")))

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
        self.assertEqual(parse_hash_sidecar(sidecar("ab" * 32, "deno.exe")), "ab" * 32)
        self.assertEqual(parse_hash_sidecar("cd" * 32 + "  deno.exe\n"), "cd" * 32)
        self.assertIsNone(parse_hash_sidecar("no hash here"))
        self.assertEqual(parse_deno_version("deno 2.9.7 (stable, release, x86_64-pc-windows-msvc)\nv8 15.0\n"), "2.9.7")
        self.assertIsNone(parse_deno_version("v8 15.0"))


if __name__ == "__main__":
    unittest.main()
