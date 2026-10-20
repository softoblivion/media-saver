import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.linkclean import LinkResult, clean_link, clean_text, extract_candidates  # noqa: E402

SC = "DAbC123xYz_"
R = f"https://www.instagram.com/reel/{SC}"
P = f"https://www.instagram.com/p/{SC}"

# (raw, clean, kind, error)
CLEAN_LINK_CASES = [
    (f"https://www.instagram.com/reel/{SC}/?igsh=MWx0cG9", R, "reel", None),
    (f"https://www.instagram.com/reel/{SC}/", R, "reel", None),
    (f"https://www.instagram.com/reel/{SC}", R, "reel", None),
    (f"http://instagram.com/reel/{SC}/", R, "reel", None),
    (f"instagram.com/reel/{SC}", R, "reel", None),
    (f"www.instagram.com/reel/{SC}/?utm_source=ig_web_copy_link", R, "reel", None),
    (f"https://m.instagram.com/reel/{SC}/", R, "reel", None),
    (f"https://INSTAGRAM.COM/reel/{SC}", R, "reel", None),
    (f"https://instagr.am/reel/{SC}", R, "reel", None),
    (f"https://www.instagram.com/reels/{SC}/", R, "reel", None),
    (f"https://www.instagram.com/tv/{SC}/", R, "reel", None),
    (f"https://www.instagram.com/p/{SC}/?img_index=2", P, "p", None),
    (f"https://www.instagram.com/some.user_1/reel/{SC}/?igsh=x", R, "reel", None),
    (f"https://www.instagram.com/some.user_1/p/{SC}/", P, "p", None),
    (f"https://www.instagram.com/reel/{SC}/#frag", R, "reel", None),
    (f"https://www.instagram.com/reel/{SC}?igsh=a#frag", R, "reel", None),
    (f"https://www.instagram.com/reel/{SC}/embed", R, "reel", None),
    (f"https://www.instagram.com/reel/{SC}/embed/captioned/", R, "reel", None),
    (f"https://www.instagram.com/p/{SC}/liked_by/", P, "p", None),
    (f"https://www.instagram.com//reel//{SC}//", R, "reel", None),
    ("https://www.instagram.com/reel/abcDE", "https://www.instagram.com/reel/abcDE", "reel", None),
    ("https://www.instagram.com/reel/AbCdE-_9xY", "https://www.instagram.com/reel/AbCdE-_9xY", "reel", None),
    (
        "https://l.instagram.com/?u=https%3A%2F%2Fwww.instagram.com%2Freel%2F" + SC + "%2F%3Figsh%3Dabc&e=AT0x",
        R, "reel", None,
    ),
    ("https://l.instagram.com/?u=https://www.instagram.com/p/" + SC + "/&e=1", P, "p", None),
    # errors
    ("https://example.com/reel/" + SC, None, None, "not_instagram"),
    ("https://notinstagram.com/reel/" + SC, None, None, "not_instagram"),
    ("https://youtube.com/watch?v=abc", None, None, "not_instagram"),
    ("https://instagram.com.evil.com/reel/" + SC, None, None, "not_instagram"),
    ("https://www.instagram.com/someuser/", None, None, "unsupported_path"),
    ("https://www.instagram.com/", None, None, "unsupported_path"),
    ("https://www.instagram.com/stories/someuser/3456789/", None, None, "unsupported_path"),
    ("https://www.instagram.com/explore/tags/cats/", None, None, "unsupported_path"),
    ("https://www.instagram.com/reels/audio/123456789/", None, None, "unsupported_path"),
    ("https://www.instagram.com/reels/", None, None, "unsupported_path"),
    ("https://www.instagram.com/share/reel/BAhd2k1Z/", None, None, "unsupported_path"),
    ("https://www.instagram.com/share/p/BAhd2k1Z", None, None, "unsupported_path"),
    ("https://l.instagram.com/?e=AT0x", None, None, "unsupported_path"),
    ("https://help.instagram.com/reel/" + SC, None, None, "unsupported_path"),
    ("https://www.instagram.com/reel/", None, None, "no_shortcode"),
    ("https://www.instagram.com/p/", None, None, "no_shortcode"),
    ("https://www.instagram.com/reel/abc", None, None, "no_shortcode"),
    ("https://www.instagram.com/reel/?igsh=abc", None, None, "no_shortcode"),
    ("https://www.instagram.com/p/ab$cdef/", None, None, "no_shortcode"),
    ("https://www.instagram.com/reel/" + "a" * 65, None, None, "no_shortcode"),
]


class CleanLinkTests(unittest.TestCase):
    def test_table(self):
        for raw, clean, kind, error in CLEAN_LINK_CASES:
            with self.subTest(raw=raw):
                res = clean_link(raw)
                self.assertEqual(res.original, raw)
                self.assertEqual(res.clean, clean)
                self.assertEqual(res.kind, kind)
                self.assertEqual(res.error, error)
                if clean:
                    self.assertEqual(res.shortcode, clean.rsplit("/", 1)[1])
                else:
                    self.assertIsNone(res.shortcode)

    def test_shortcode_case_preserved(self):
        self.assertEqual(clean_link("https://WWW.Instagram.com/reel/aBcDeF/").clean, "https://www.instagram.com/reel/aBcDeF")

    def test_max_length_shortcode_ok(self):
        sc = "a" * 64
        self.assertEqual(clean_link(f"instagram.com/reel/{sc}").shortcode, sc)

    def test_to_dict_keys(self):
        d = clean_link(R).to_dict()
        self.assertEqual(list(d), ["original", "clean", "shortcode", "kind", "error"])
        self.assertIsInstance(LinkResult("x", None, None, None, "not_instagram").to_dict(), dict)


# (text, expected candidate list)
EXTRACT_CASES = [
    ("", []),
    ("just some text without links", []),
    (f"Schau mal {R}/?igsh=abc für Käse", [f"{R}/?igsh=abc"]),
    (f"🔥🔥 {R} 🔥", [R]),
    (f'"{R}"', [R]),
    (f"'{R}'", [R]),
    (f"({R}).", [R]),
    (f"[{R}]", [R]),
    (f"<{R}>", [R]),
    (f"«{R}»", [R]),
    (f"[ansehen]({R}/?igsh=1)", [f"{R}/?igsh=1"]),
    (f"{R}.", [R]),
    (f"{R}!", [R]),
    (f"{R}?", [R]),
    (f"{R},{P}", [R, P]),
    (f"{R};", [R]),
    (f"{R}:", [R]),
    (f"{R}\r\n{P}\r\n", [R, P]),
    (f"{R}\t{P}", [R, P]),
    (f"{R}/?igsh=abc{P}", [f"{R}/?igsh=abc", P]),
    (f"{R}{P}", [R, P]),
    (f"{R}/https://example.com/x", [f"{R}/", "https://example.com/x"]),
    (f"instagram.com/reel/{SC}/ und www.instagram.com/p/{SC}", [f"instagram.com/reel/{SC}/", f"www.instagram.com/p/{SC}"]),
    ("siehe www.example.com/page.", ["www.example.com/page"]),
    ("https://example.com/a", ["https://example.com/a"]),
    ("http:// ", []),
    ("notinstagram.com/reel/abcdef", []),
    ("mein@instagram.com/reel/abcdef", []),
    (
        "https://l.instagram.com/?u=https://www.instagram.com/reel/" + SC + "/&e=1",
        ["https://l.instagram.com/?u=https://www.instagram.com/reel/" + SC + "/&e=1"],
    ),
    (f"**{R}**", [R]),
]


class ExtractTests(unittest.TestCase):
    def test_table(self):
        for text, expected in EXTRACT_CASES:
            with self.subTest(text=text):
                self.assertEqual(extract_candidates(text), expected)


class CleanTextTests(unittest.TestCase):
    def test_result_shape(self):
        res = clean_text(f"{R}/?igsh=1")
        self.assertEqual(set(res), {"items", "clean_text", "valid", "invalid", "duplicates"})
        self.assertEqual(set(res["items"][0]), {"original", "clean", "shortcode", "kind", "error"})

    def test_empty(self):
        self.assertEqual(clean_text(""), {"items": [], "clean_text": "", "valid": 0, "invalid": 0, "duplicates": 0})

    def test_dedup_by_clean_url_keeps_first(self):
        res = clean_text(f"{R}/?igsh=a\n{R}/?igsh=b\n{R}\n{P}")
        self.assertEqual([i["clean"] for i in res["items"]], [R, P])
        self.assertEqual(res["items"][0]["original"], f"{R}/?igsh=a")
        self.assertEqual((res["valid"], res["invalid"], res["duplicates"]), (2, 0, 2))
        self.assertEqual(res["clean_text"], f"{R}\n{P}")

    def test_shortcode_case_not_merged(self):
        res = clean_text("instagram.com/reel/AbCdEf instagram.com/reel/abcdef")
        self.assertEqual((res["valid"], res["duplicates"]), (2, 0))

    def test_invalid_dedup_by_original(self):
        res = clean_text("https://example.com/a https://example.com/a https://example.com/b")
        self.assertEqual((res["valid"], res["invalid"], res["duplicates"]), (0, 2, 1))
        self.assertEqual(res["clean_text"], "")
        self.assertEqual(res["items"][0]["error"], "not_instagram")

    def test_mixed_message(self):
        text = (
            "Hallo! Hier die Reels:\r\n"
            f"1) {R}/?igsh=aaa\r\n"
            f"2) [hier]({P}?img_index=1), und noch https://example.com/x.\r\n"
            "3) https://www.instagram.com/someuser/\r\n"
            "4) https://www.instagram.com/reel/\r\n"
            f"5) {R}/?utm_source=x 🙏"
        )
        res = clean_text(text)
        self.assertEqual([i["error"] for i in res["items"]], [None, None, "not_instagram", "unsupported_path", "no_shortcode"])
        self.assertEqual((res["valid"], res["invalid"], res["duplicates"]), (2, 3, 1))
        self.assertEqual(res["clean_text"], f"{R}\n{P}")

    def test_glued_links(self):
        res = clean_text(f"{R}/?igsh=abc{P}/?igsh=def")
        self.assertEqual(res["clean_text"], f"{R}\n{P}")

    def test_wrapped_link_in_text(self):
        wrapped = "https://l.instagram.com/?u=https%3A%2F%2Fwww.instagram.com%2Freel%2F" + SC + "%2F&e=1"
        res = clean_text(f"リンク: {wrapped}")
        self.assertEqual(res["clean_text"], R)
        self.assertEqual(res["items"][0]["original"], wrapped)

    def test_performance_1000_links(self):
        lines = [
            f"Schau 🔥 https://www.instagram.com/reel/Code{i:06d}/?igsh=MWx0cG9{i}&utm_source=ig_web_copy_link ΟΚ."
            for i in range(1000)
        ]
        text = "\n".join(lines)
        best = min(self._timed(text) for _ in range(3))
        self.assertLess(best, 0.05)
        self.assertEqual(clean_text(text)["valid"], 1000)

    @staticmethod
    def _timed(text):
        t = time.perf_counter()
        clean_text(text)
        return time.perf_counter() - t


if __name__ == "__main__":
    unittest.main()
