import json
import os
import shutil
import tempfile
import unittest

from app.core import settings as S


class SettingsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="idl_settings_")
        self.path = os.path.join(self.tmp, "settings.json")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_defaults_match_bridge_schema(self):
        d = S.defaults()
        self.assertEqual(set(d), {"output_dir", "filename_template", "quality", "h264_compat", "skip_existing",
                                  "concurrency", "update_channel", "auto_update", "auth_mode", "browser_cookies",
                                  "clipboard_watch", "open_folder_when_done"})
        self.assertEqual(d["filename_template"], "{author}_{shortcode}")
        self.assertEqual(d["quality"], "max")
        self.assertEqual(d["concurrency"], 2)
        self.assertTrue(d["output_dir"].endswith(os.path.join("Downloads", "Reels Saver")))

    def test_valid_patch(self):
        new = S.apply_patch(S.defaults(), {"quality": "720", "concurrency": 4, "h264_compat": False,
                                           "browser_cookies": "firefox", "output_dir": self.tmp,
                                           "filename_template": "{date}_{author}_{shortcode}"})
        self.assertEqual(new["quality"], "720")
        self.assertEqual(new["concurrency"], 4)
        self.assertFalse(new["h264_compat"])
        self.assertEqual(new["output_dir"], os.path.normpath(self.tmp))

    def test_numeric_quality_is_accepted(self):
        self.assertEqual(S.apply_patch(S.defaults(), {"quality": 1080})["quality"], "1080")

    def test_unknown_keys_ignored(self):
        new = S.apply_patch(S.defaults(), {"nope": 1, "quality": "1080"})
        self.assertNotIn("nope", new)
        self.assertEqual(new["quality"], "1080")

    def test_invalid_values_raise_russian(self):
        bad = [
            {"concurrency": 0}, {"concurrency": 5}, {"concurrency": 2.5}, {"concurrency": True},
            {"quality": "4k"}, {"h264_compat": "yes"}, {"update_channel": "beta"}, {"auth_mode": "x"},
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

    def test_save_load_roundtrip_atomic(self):
        store = S.SettingsStore(self.path)
        new, changed = store.update({"quality": "720", "concurrency": 3})
        self.assertEqual(changed, {"quality": "max", "concurrency": 2})
        self.assertFalse(os.path.exists(self.path + ".tmp"))
        self.assertEqual(S.load(self.path)["quality"], "720")
        self.assertEqual(S.SettingsStore(self.path).get()["concurrency"], 3)

    def test_no_change_no_write(self):
        store = S.SettingsStore(self.path)
        _, changed = store.update({"quality": "max"})
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
            json.dump({"quality": "8k", "concurrency": 3, "legacy": True}, fh)
        data = S.load(self.path)
        self.assertEqual(data["quality"], "max")
        self.assertEqual(data["concurrency"], 3)
        self.assertNotIn("legacy", data)


if __name__ == "__main__":
    unittest.main()
