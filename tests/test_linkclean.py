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
    ("https://example.com/reel/" + SC, None, None, "not_supported"),
    ("https://notinstagram.com/reel/" + SC, None, None, "not_supported"),
    ("https://vimeo.com/123456", None, None, "not_supported"),
    ("https://instagram.com.evil.com/reel/" + SC, None, None, "not_supported"),
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
                self.assertEqual(res.platform, None if error == "not_supported" else "instagram")
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
        self.assertEqual(list(d), ["original", "clean", "shortcode", "platform", "kind", "error"])
        self.assertIsInstance(LinkResult("x", None, None, None, None, "not_supported").to_dict(), dict)


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
        self.assertEqual(set(res["items"][0]), {"original", "clean", "shortcode", "platform", "kind", "error"})

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
        self.assertEqual(res["items"][0]["error"], "not_supported")

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
        self.assertEqual([i["error"] for i in res["items"]], [None, None, "not_supported", "unsupported_path", "no_shortcode"])
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


YID = "dQw4w9WgXcQ"
YID2 = "aqz-KE-bpKQ"
YV = f"https://www.youtube.com/watch?v={YID}"
YS = f"https://www.youtube.com/shorts/{YID}"

# (raw, clean, kind, error); every row is a YouTube link (platform "youtube")
YOUTUBE_CASES = [
    (f"https://www.youtube.com/watch?v={YID}", YV, "video", None),
    (f"http://youtube.com/watch?v={YID}", YV, "video", None),
    (f"youtube.com/watch?v={YID}", YV, "video", None),
    (f"www.youtube.com/watch?v={YID}&t=42s", YV, "video", None),
    (f"https://m.youtube.com/watch?v={YID}&feature=share", YV, "video", None),
    (f"https://music.youtube.com/watch?v={YID}&si=AbCdEf", YV, "video", None),
    (f"https://WWW.YOUTUBE.COM/watch?v={YID}", YV, "video", None),
    (f"https://www.youtube.com/watch?feature=share&v={YID}", YV, "video", None),
    (f"https://www.youtube.com/watch?app=desktop&v={YID}&pp=ygUFaGVsbG8%3D", YV, "video", None),
    (f"https://www.youtube.com/watch?v={YID}&list=PLx0sYbCqOb8TBPRdmBHs5Iftvv9TPboYG&index=3", YV, "video", None),
    (f"https://www.youtube.com/watch?v={YID}#t=1m30s", YV, "video", None),
    (f"https://www.youtube.com/watch/?v={YID}", YV, "video", None),
    (f"https://www.youtube.com/watch/{YID}", YV, "video", None),
    (f"https://youtu.be/{YID}", YV, "video", None),
    (f"https://youtu.be/{YID}?si=xYz123&t=5", YV, "video", None),
    (f"youtu.be/{YID}/", YV, "video", None),
    (f"https://www.youtube.com/embed/{YID}?autoplay=1", YV, "video", None),
    (f"https://www.youtube-nocookie.com/embed/{YID}", YV, "video", None),
    (f"https://youtube-nocookie.com/embed/{YID}?rel=0", YV, "video", None),
    (f"https://www.youtube.com/v/{YID}&hl=en_US&fs=1", YV, "video", None),
    (f"https://www.youtube.com/live/{YID}?si=abc", YV, "video", None),
    (f"https://www.youtube.com/e/{YID}", YV, "video", None),
    (f"https://www.youtube.com/shorts/{YID}", YS, "shorts", None),
    (f"https://youtube.com/shorts/{YID}?feature=share", YS, "shorts", None),
    (f"https://m.youtube.com/shorts/{YID}/", YS, "shorts", None),
    ("https://www.youtube.com/attribution_link?a=x&u=%2Fwatch%3Fv%3D" + YID + "%26feature%3Dshare", YV, "video", None),
    (f"https://www.youtube.com/watch?v={YID2}", f"https://www.youtube.com/watch?v={YID2}", "video", None),
    # unsupported paths
    ("https://www.youtube.com/", None, None, "unsupported_path"),
    ("https://youtube.com", None, None, "unsupported_path"),
    ("https://www.youtube.com/playlist?list=PLx0sYbCqOb8TBPRdmBHs5Iftvv9TPboYG", None, None, "unsupported_path"),
    ("https://www.youtube.com/channel/UCuAXFkgsw1L7xaCfnd5JJOw", None, None, "unsupported_path"),
    ("https://www.youtube.com/@BlenderOfficial", None, None, "unsupported_path"),
    ("https://www.youtube.com/@BlenderOfficial/shorts", None, None, "unsupported_path"),
    ("https://www.youtube.com/c/BlenderFoundation", None, None, "unsupported_path"),
    ("https://www.youtube.com/user/BlenderFoundation", None, None, "unsupported_path"),
    ("https://www.youtube.com/results?search_query=blender", None, None, "unsupported_path"),
    ("https://www.youtube.com/feed/subscriptions", None, None, "unsupported_path"),
    ("https://www.youtube.com/embed/videoseries?list=PLx0sYbCqOb8TBPRdmBHs5Iftvv9TPboYG", None, None, "unsupported_path"),
    (f"https://studio.youtube.com/video/{YID}/edit", None, None, "unsupported_path"),
    # missing or malformed id
    ("https://www.youtube.com/watch", None, None, "no_shortcode"),
    ("https://www.youtube.com/watch?v=", None, None, "no_shortcode"),
    ("https://www.youtube.com/watch?list=PLx0sYbCqOb8TBPRdmBHs5Iftvv9TPboYG", None, None, "no_shortcode"),
    ("https://www.youtube.com/watch?v=dQw4w9WgXc", None, None, "no_shortcode"),
    ("https://www.youtube.com/watch?v=dQw4w9WgXcQQ", None, None, "no_shortcode"),
    ("https://www.youtube.com/watch?v=dQw4w9Wg$cQ", None, None, "no_shortcode"),
    ("https://youtu.be/short", None, None, "no_shortcode"),
    ("https://www.youtube.com/shorts/", None, None, "no_shortcode"),
    ("https://www.youtube.com/embed/", None, None, "no_shortcode"),
]


class YouTubeLinkTests(unittest.TestCase):
    def test_table(self):
        self.assertGreaterEqual(len(YOUTUBE_CASES), 30)
        for raw, clean, kind, error in YOUTUBE_CASES:
            with self.subTest(raw=raw):
                res = clean_link(raw)
                self.assertEqual(res.original, raw)
                self.assertEqual(res.clean, clean)
                self.assertEqual(res.kind, kind)
                self.assertEqual(res.error, error)
                self.assertEqual(res.platform, "youtube")
                if clean:
                    self.assertEqual(len(res.shortcode), 11)
                    self.assertTrue(clean.endswith(res.shortcode))
                else:
                    self.assertIsNone(res.shortcode)

    def test_id_case_preserved(self):
        self.assertEqual(clean_link("https://YOUTU.BE/aBcDeFgHiJk").shortcode, "aBcDeFgHiJk")

    def test_extract_scheme_less_and_glued(self):
        text = (f"look youtube.com/watch?v={YID}&t=3, also youtu.be/{YID2}! and m.youtube.com/shorts/{YID}."
                f" [md](https://youtu.be/{YID2}?si=q) \"https://www.youtube.com/watch?v={YID}\""
                f"https://youtu.be/{YID}https://www.instagram.com/reel/{SC}")
        self.assertEqual(extract_candidates(text), [
            f"youtube.com/watch?v={YID}&t=3",
            f"youtu.be/{YID2}",
            f"m.youtube.com/shorts/{YID}",
            f"https://youtu.be/{YID2}?si=q",
            f"https://www.youtube.com/watch?v={YID}",
            f"https://youtu.be/{YID}",
            f"https://www.instagram.com/reel/{SC}",
        ])

    def test_lookalike_hosts(self):
        self.assertEqual(clean_link(f"https://notyoutube.com/watch?v={YID}").error, "not_supported")
        self.assertEqual(clean_link(f"https://youtube.com.evil.com/watch?v={YID}").error, "not_supported")
        self.assertEqual(extract_candidates(f"myyoutube.com/watch?v={YID}"), [])


class MixedPlatformTests(unittest.TestCase):
    TEXT = (
        f"1) {R}/?igsh=aaa\n"
        f"2) https://youtu.be/{YID}?t=10\n"
        f"3) https://www.youtube.com/watch?v={YID}&list=PL1\n"   # same video as 2) -> duplicate
        f"4) https://www.youtube.com/shorts/{YID}\n"             # other kind -> its own URL
        f"5) instagram.com/reel/{SC}\n"                          # duplicate of 1)
        "6) https://www.youtube.com/@someone\n"
        "7) https://vimeo.com/1\n"
        f"8) youtube.com/watch?v={YID2}"
    )

    def test_both_platforms(self):
        res = clean_text(self.TEXT)
        self.assertEqual([(i["platform"], i["kind"], i["error"]) for i in res["items"]], [
            ("instagram", "reel", None), ("youtube", "video", None), ("youtube", "shorts", None),
            ("youtube", None, "unsupported_path"), (None, None, "not_supported"), ("youtube", "video", None)])
        self.assertEqual((res["valid"], res["invalid"], res["duplicates"]), (4, 2, 2))
        self.assertEqual(res["clean_text"].split("\n"), [R, YV, YS, f"https://www.youtube.com/watch?v={YID2}"])

    def test_youtube_mode(self):
        res = clean_text(self.TEXT, "youtube")
        first = res["items"][0]
        self.assertEqual((first["error"], first["clean"], first["shortcode"], first["platform"], first["kind"]),
                         ("other_platform", R, SC, "instagram", "reel"))
        self.assertEqual((res["valid"], res["invalid"], res["duplicates"]), (3, 3, 2))
        self.assertEqual(res["clean_text"].split("\n"), [YV, YS, f"https://www.youtube.com/watch?v={YID2}"])

    def test_instagram_mode(self):
        res = clean_text(self.TEXT, "instagram")
        self.assertEqual([i["error"] for i in res["items"]],
                         [None, "other_platform", "other_platform", "unsupported_path", "not_supported",
                          "other_platform"])
        self.assertEqual((res["valid"], res["invalid"], res["duplicates"]), (1, 5, 2))
        self.assertEqual(res["clean_text"], R)

    def test_unknown_platform(self):
        with self.assertRaises(ValueError):
            clean_text(R, "tiktok")


if __name__ == "__main__":
    unittest.main()
