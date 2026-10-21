import json
import os
import shutil
import tempfile
import unittest

from app.core import settings as S


class SettingsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ms_settings_")
        self.path = os.path.join(self.tmp, "settings.json")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_defaults_match_bridge_schema(self):
        d = S.defaults()
        self.assertEqual(set(d), {"output_dir", "filename_template", "mode", "format", "quality_instagram",
                                  "quality_youtube", "quality_tiktok", "quality_twitter", "h264_compat",
                                  "skip_existing", "concurrency", "update_channel", "auto_update",
                                  "auto_download_updates", "update_check_interval", "auth_mode", "browser_cookies", "clipboard_watch", "sound_on_done",
                                  "open_folder_when_done"})
        self.assertEqual(d["filename_template"], "{author}_{shortcode}")
        self.assertEqual(d["quality_instagram"], "max")
        self.assertEqual(d["quality_youtube"], "max")
        self.assertEqual(d["mode"], "instagram")
        self.assertEqual(d["format"], "mp4")
        self.assertTrue(d["auto_download_updates"])
        self.assertEqual(d["update_check_interval"], "30m")
        self.assertEqual(S.UPDATE_CHECK_INTERVALS, ("30m", "1h", "6h", "1d", "1w"))
        self.assertEqual(d["concurrency"], 2)
        self.assertIn(os.path.basename(d["output_dir"]), ("Downloads", "Media-Saver"))  # override / Videos default

    def test_output_dir_is_portable(self):
        from app.core import paths as P
        old = os.environ.get(P.HOME_ENV)
        try:
            os.environ[P.HOME_ENV] = self.tmp
            self.assertEqual(P.default_root(), os.path.abspath(self.tmp))
            self.assertEqual(P.default_output_dir(), os.path.join(os.path.abspath(self.tmp), "Downloads"))
            os.environ.pop(P.HOME_ENV)
            root = P.default_root()
            self.assertEqual(os.path.basename(root), "media-saver_data")
            self.assertEqual(os.path.dirname(root), P.app_dir())
            out = P.default_output_dir()
            self.assertEqual(os.path.basename(out), "Media-Saver")
            self.assertEqual(os.path.dirname(out), P.videos_dir())
            self.assertTrue(os.path.isabs(out))
            for forbidden in (os.environ.get("LOCALAPPDATA", "<unset>"), os.environ.get("APPDATA", "<unset>")):
                self.assertFalse(root.lower().startswith(forbidden.lower()))
        finally:
            if old is not None:
                os.environ[P.HOME_ENV] = old
            else:
                os.environ.pop(P.HOME_ENV, None)

    def test_valid_patch(self):
        new = S.apply_patch(S.defaults(), {"quality_instagram": "medium", "quality_youtube": "720", "concurrency": 4,
                                           "h264_compat": False,
                                           "browser_cookies": "firefox", "output_dir": self.tmp,
                                           "filename_template": "{date}_{author}_{shortcode}"})
        self.assertEqual(new["quality_instagram"], "medium")
        self.assertEqual(new["quality_youtube"], "720")
        self.assertEqual(new["concurrency"], 4)
        four = S.apply_patch(S.defaults(), {"mode": "twitter", "quality_tiktok": "low", "quality_twitter": "medium"})
        self.assertEqual((four["mode"], four["quality_tiktok"], four["quality_twitter"]), ("twitter", "low", "medium"))
        self.assertFalse(new["h264_compat"])
        self.assertEqual(new["output_dir"], os.path.normpath(self.tmp))

    def test_numeric_quality_is_accepted(self):
        self.assertEqual(S.apply_patch(S.defaults(), {"quality_youtube": 720})["quality_youtube"], "720")

    def test_unknown_keys_ignored(self):
        new = S.apply_patch(S.defaults(), {"nope": 1, "quality": "1080", "quality_youtube": "360"})  # "quality" is the 1.x key
        self.assertNotIn("nope", new)
        self.assertNotIn("quality", new)
        self.assertEqual(new["quality_youtube"], "360")

    def test_invalid_values_raise_russian(self):
        bad = [
            {"concurrency": 0}, {"concurrency": 5}, {"concurrency": 2.5}, {"concurrency": True},
            {"quality_instagram": "4k"}, {"quality_instagram": "720"}, {"quality_youtube": "1080"},
            {"h264_compat": "yes"}, {"update_channel": "beta"}, {"auth_mode": "x"},
            {"mode": "vimeo"}, {"format": "wav"}, {"auto_download_updates": 1},
            {"update_check_interval": "5m"}, {"update_check_interval": 1800}, {"update_check_interval": None},
            {"quality_tiktok": "720"}, {"quality_twitter": "1080"},
            {"browser_cookies": "safari"}, {"output_dir": ""}, {"output_dir": "relative\\dir"},
            {"filename_template": ""}, {"filename_template": "{author}"}, {"filename_template": "{foo}_{shortcode}"},
            {"filename_template": "a/b_{shortcode}"}, {"filename_template": "{shortcode}" + "x" * 120},
            {"filename_template": "{shortcode"},
        ]
        for patch in bad:
            with self.subTest(patch=patch):
                with self.assertRaises(ValueError) as ctx:
                    S.apply_patch(S.defaults(), patch)
                self.assertRegex(str(ctx.exception), "[A-Za-z]")

    def test_patch_must_be_dict(self):
        with self.assertRaises(ValueError):
            S.apply_patch(S.defaults(), ["quality"])

    def test_older_file_without_the_interval_key_gets_the_default(self):
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump({"concurrency": 3, "auto_download_updates": False}, fh)
        data = S.load(self.path)
        self.assertEqual(data["update_check_interval"], "30m")
        self.assertEqual(data["concurrency"], 3)
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump({"update_check_interval": "1w"}, fh)
        self.assertEqual(S.load(self.path)["update_check_interval"], "1w")

    def test_save_load_roundtrip_atomic(self):
        store = S.SettingsStore(self.path)
        new, changed = store.update({"quality_youtube": "720", "concurrency": 3})
        self.assertEqual(changed, {"quality_youtube": "max", "concurrency": 2})
        self.assertFalse(os.path.exists(self.path + ".tmp"))
        self.assertEqual(S.load(self.path)["quality_youtube"], "720")
        self.assertEqual(S.SettingsStore(self.path).get()["concurrency"], 3)

    def test_no_change_no_write(self):
        store = S.SettingsStore(self.path)
        _, changed = store.update({"quality_youtube": "max"})
        self.assertEqual(changed, {})
        self.assertFalse(os.path.exists(self.path))

    def test_broken_file_gives_defaults(self):
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write("{not json")
        self.assertEqual(S.load(self.path), S.defaults())
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write("[1, 2]")
        self.assertEqual(S.load(self.path), S.defaults())

    def test_bad_key_falls_back_per_key(self):
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump({"quality_instagram": "8k", "concurrency": 3, "legacy": True}, fh)
        data = S.load(self.path)
        self.assertEqual(data["quality_instagram"], "max")
        self.assertEqual(data["concurrency"], 3)
        self.assertNotIn("legacy", data)


if __name__ == "__main__":
    unittest.main()
