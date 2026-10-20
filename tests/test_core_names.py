import unittest

from app.core.downloader import meta_from_info, render_filename, sanitize_filename


class SanitizeTest(unittest.TestCase):
    def test_invalid_chars(self):
        self.assertEqual(sanitize_filename('a<b>c:d"e/f\\g|h?i*j'), "a_b_c_d_e_f_g_h_i_j")
        self.assertEqual(sanitize_filename("tab\there\nnew"), "tab here new")

    def test_reserved_names(self):
        for name in ("CON", "con", "Nul", "COM1", "lpt9", "AUX.txt"):
            with self.subTest(name=name):
                self.assertTrue(sanitize_filename(name).startswith("_"))
        self.assertEqual(sanitize_filename("CONSOLE"), "CONSOLE")

    def test_trailing_dots_spaces_and_empty(self):
        self.assertEqual(sanitize_filename("name. . "), "name")
        self.assertEqual(sanitize_filename("   "), "video")
        self.assertEqual(sanitize_filename("...", fallback="x"), "x")

    def test_length(self):
        out = sanitize_filename("ü" * 400)
        self.assertEqual(len(out), 150)
        self.assertLessEqual(len(sanitize_filename("a" * 149 + " .b", 150)), 150)

    def test_unicode_kept(self):
        self.assertEqual(sanitize_filename("Gʻayratilla 😺 Jäger"), "Gʻayratilla 😺 Jäger")


class RenderTest(unittest.TestCase):
    def test_default_template(self):
        self.assertEqual(render_filename("{author}_{shortcode}", "DQCkNLtgqEe", "teddysphotos", None, ""),
                         "teddysphotos_DQCkNLtgqEe")

    def test_all_tokens_and_cleanup(self):
        out = render_filename("{date}_{author}_{title}_{shortcode}", "AbC", "user", 'Hi: "there"?', "2026-10-20")
        self.assertEqual(out, "2026-10-20_user_Hi_ _there_AbC")
        self.assertNotRegex(out, r'[<>:"/\\|?*]')
        # empty title / date collapse the separators
        self.assertEqual(render_filename("{date}_{title}_{shortcode}", "AbC", "u", None, ""), "AbC")

    def test_long_title_is_capped(self):
        out = render_filename("{title}_{shortcode}", "AbC", "u", "x" * 500, "")
        self.assertLessEqual(len(out), 150)
        self.assertTrue(out.endswith("_AbC"))


class MetaTest(unittest.TestCase):
    def test_meta_from_real_info(self):
        info = {"channel": "teddysphotos", "uploader": "Ed Sheeran", "uploader_id": "185546187",
                "title": "Video by teddysphotos", "description": "\n“Gingerton”\nsecond line",
                "upload_date": "20251020", "duration": 26.2}
        m = meta_from_info(info)
        self.assertEqual(m, {"author": "teddysphotos", "title": "“Gingerton”", "date": "2025-10-20", "duration_s": 26.2})

    def test_meta_fallbacks(self):
        m = meta_from_info({"uploader_id": "123", "uploader": "Name", "title": "Video by x", "timestamp": 0})
        self.assertEqual(m["author"], "Name")
        self.assertEqual(m["title"], "Video by x")
        self.assertEqual(m["date"], "1970-01-01")
        self.assertIsNone(m["duration_s"])


if __name__ == "__main__":
    unittest.main()
