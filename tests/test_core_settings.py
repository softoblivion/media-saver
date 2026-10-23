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
                                  "auto_download_updates", "auth_mode", "browser_cookies", "clipboard_watch", "sound_on_done",
                                  "open_folder_when_done", "theme", "ui_scale"})
        self.assertEqual(d["theme"], "dusk")
        self.assertEqual(d["ui_scale"], 100)
        self.assertEqual(d["filename_template"], "{author}_{shortcode}")
        self.assertEqual(d["quality_instagram"], "max")
        self.assertEqual(d["quality_youtube"], "max")
        self.assertEqual(d["mode"], "instagram")
        self.assertEqual(d["format"], "mp4")
        self.assertTrue(d["auto_download_updates"])
        self.assertNotIn("update_check_interval", d)
        self.assertFalse(hasattr(S, "UPDATE_CHECK_INTERVALS"))
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
        new = S.apply_patch(S.defaults(), {"quality_instagram": "medium", "quality_youtube": "high", "concurrency": 4,
                                           "h264_compat": False,
                                           "browser_cookies": "firefox", "output_dir": self.tmp,
                                           "filename_template": "{date}_{author}_{shortcode}"})
        self.assertEqual(new["quality_instagram"], "medium")
        self.assertEqual(new["quality_youtube"], "high")
        self.assertEqual(new["concurrency"], 4)
        four = S.apply_patch(S.defaults(), {"mode": "twitter", "quality_tiktok": "low", "quality_twitter": "medium"})
        self.assertEqual((four["mode"], four["quality_tiktok"], four["quality_twitter"]), ("twitter", "low", "medium"))
        self.assertFalse(new["h264_compat"])
        self.assertEqual(new["output_dir"], os.path.normpath(self.tmp))

    def test_four_step_ladder_for_every_platform(self):
        for key in ("quality_instagram", "quality_youtube", "quality_tiktok", "quality_twitter"):
            for value in ("low", "medium", "high", "max"):
                with self.subTest(key=key, value=value):
                    self.assertEqual(S.apply_patch(S.defaults(), {key: value})[key], value)

    def test_retired_and_numeric_quality_in_a_patch_is_refused(self):
        # new input stays strict; only a stored file is mapped (test_legacy_quality_values_load_mapped)
        for key in ("quality_instagram", "quality_youtube", "quality_tiktok", "quality_twitter"):
            for value in ("360", "720", 720, 360, "1080", "best", "", None):
                with self.subTest(key=key, value=value):
                    with self.assertRaises(ValueError) as ctx:
                        S.apply_patch(S.defaults(), {key: value})
                    self.assertIn("Invalid value for", str(ctx.exception))

    def test_ui_scale_snaps_to_a_step(self):
        self.assertEqual(S.normalize_scale(150), 150)
        self.assertEqual(S.normalize_scale("125"), 125)
        self.assertEqual(S.normalize_scale(140), 150)       # nearest step, the larger one on a tie
        self.assertEqual(S.normalize_scale(112.5), 125)
        self.assertEqual(S.normalize_scale(300), 200)
        self.assertEqual(S.normalize_scale(0), 100)
        for junk in (None, "big", True, float("nan"), -50):
            with self.subTest(junk=junk):
                self.assertEqual(S.normalize_scale(junk), 100)
        new = S.apply_patch(S.defaults(), {"ui_scale": 175})
        self.assertEqual(new["ui_scale"], 175)
        self.assertEqual(S.apply_patch(S.defaults(), {"ui_scale": "huge"})["ui_scale"], 100)
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump({"ui_scale": 155}, fh)
        self.assertEqual(S.load(self.path)["ui_scale"], 150)

    def test_unknown_keys_ignored(self):
        new = S.apply_patch(S.defaults(), {"nope": 1, "quality": "1080", "quality_youtube": "low"})  # "quality" is the 1.x key
        self.assertNotIn("nope", new)
        self.assertNotIn("quality", new)
        self.assertEqual(new["quality_youtube"], "low")

    def test_invalid_values_raise_russian(self):
        bad = [
            {"concurrency": 0}, {"concurrency": 5}, {"concurrency": 2.5}, {"concurrency": True},
            {"quality_instagram": "4k"}, {"quality_instagram": "720"}, {"quality_youtube": "1080"},
            {"quality_youtube": "360"}, {"quality_youtube": "720"}, {"quality_youtube": 720},
            {"h264_compat": "yes"}, {"update_channel": "beta"}, {"auth_mode": "x"},
            {"mode": "vimeo"}, {"format": "wav"}, {"auto_download_updates": 1},
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

    def test_file_without_auto_download_key_loads_as_on(self):
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump({"concurrency": 3}, fh)
        data = S.load(self.path)
        self.assertIs(data["auto_download_updates"], True)
        self.assertEqual(data["concurrency"], 3)
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump({"auto_download_updates": False}, fh)
        self.assertIs(S.load(self.path)["auto_download_updates"], False)

    def test_file_of_an_older_version_with_the_interval_key_loads_silently(self):
        original = json.dumps({"concurrency": 3, "update_check_interval": "1w", "auto_download_updates": False})
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write(original)
        with self.assertNoLogs("mediasaver.core.settings", level="WARNING"):
            data = S.load(self.path)
        self.assertNotIn("update_check_interval", data)
        self.assertEqual(data["concurrency"], 3)
        self.assertIs(data["auto_download_updates"], False)
        with open(self.path, "r", encoding="utf-8") as fh:
            self.assertEqual(fh.read(), original)          # loading does not rewrite the file
        store = S.SettingsStore(self.path)
        self.assertNotIn("update_check_interval", store.get())
        store.update({"concurrency": 4})                   # the next save drops the stale key
        with open(self.path, "r", encoding="utf-8") as fh:
            self.assertNotIn("update_check_interval", json.load(fh))

    def test_save_load_roundtrip_atomic(self):
        store = S.SettingsStore(self.path)
        new, changed = store.update({"quality_youtube": "medium", "concurrency": 3})
        self.assertEqual(changed, {"quality_youtube": "max", "concurrency": 2})
        self.assertFalse(os.path.exists(self.path + ".tmp"))
        self.assertEqual(S.load(self.path)["quality_youtube"], "medium")
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
            json.dump({"concurrency": 99, "format": "wav", "legacy": True}, fh)
        data = S.load(self.path)
        self.assertEqual(data["concurrency"], 2)
        self.assertEqual(data["format"], "mp4")
        self.assertNotIn("legacy", data)

    def load_quality_file(self, stored):
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump(stored, fh)
        return S.load(self.path)

    def test_legacy_quality_values_load_mapped(self):
        cases = [("360", "low"), ("720", "medium"), (360, "low"), (720, "medium"),
                 ("low", "low"), ("medium", "medium"), ("high", "high"), ("max", "max")]
        for stored, expected in cases:
            with self.subTest(stored=stored):
                data = self.load_quality_file({"quality_youtube": stored, "quality_instagram": stored,
                                               "quality_tiktok": stored, "quality_twitter": stored})
                for key in ("quality_youtube", "quality_instagram", "quality_tiktok", "quality_twitter"):
                    self.assertEqual(data[key], expected, key)

    def test_unknown_stored_quality_loads_as_high(self):
        for stored in ("8k", "1080", "", None, True, 1080, 2.5, ["max"], {"q": 1}):
            with self.subTest(stored=stored):
                self.assertEqual(self.load_quality_file({"quality_instagram": stored})["quality_instagram"], "high")

    def test_missing_quality_key_keeps_the_default(self):
        data = self.load_quality_file({"concurrency": 3})
        for key in ("quality_youtube", "quality_instagram", "quality_tiktok", "quality_twitter"):
            self.assertEqual(data[key], "max")

    def test_legacy_quality_file_is_rewritten_on_the_next_save(self):
        original = json.dumps({"quality_youtube": "720", "quality_instagram": "low"})
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write(original)
        store = S.SettingsStore(self.path)
        self.assertEqual(store.get()["quality_youtube"], "medium")
        with open(self.path, "r", encoding="utf-8") as fh:
            self.assertEqual(fh.read(), original)          # loading does not rewrite the file
        store.update({"concurrency": 3})
        with open(self.path, "r", encoding="utf-8") as fh:
            saved = json.load(fh)
        self.assertEqual((saved["quality_youtube"], saved["quality_instagram"]), ("medium", "low"))


if __name__ == "__main__":
    unittest.main()
