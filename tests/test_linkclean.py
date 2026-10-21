import subprocess
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
            clean_text(R, "vimeo")


TTID = "7106594312292453675"
TTID2 = "7201234567890123456"
TTV = f"https://www.tiktok.com/@tiktok/video/{TTID}"
TTN = f"https://www.tiktok.com/@/video/{TTID}"
TTS = "https://vm.tiktok.com/ZMabcdEF1"

# (raw, clean, kind, error); every row is a TikTok link (platform "tiktok")
TIKTOK_CASES = [
    # full URLs
    (f"https://www.tiktok.com/@tiktok/video/{TTID}?is_from_webapp=1&sender_device=pc&web_id=7123456789012345678",
     TTV, "video", None),
    (f"https://www.tiktok.com/@tiktok/video/{TTID}/", TTV, "video", None),
    (f"https://www.tiktok.com/@tiktok/video/{TTID}", TTV, "video", None),
    (f"tiktok.com/@tiktok/video/{TTID}", TTV, "video", None),
    (f"www.tiktok.com/@tiktok/video/{TTID}?_r=1&_t=8abcDEF", TTV, "video", None),
    (f"http://m.tiktok.com/@tiktok/video/{TTID}", TTV, "video", None),
    (f"https://TIKTOK.COM/@tiktok/video/{TTID}?lang=en", TTV, "video", None),
    (f"https://www.tiktok.com/@tiktok/video/{TTID}#frag", TTV, "video", None),
    (f"https://www.tiktok.com/@tiktok/video/{TTID}/?a=1#frag", TTV, "video", None),
    (f"https://www.tiktok.com//@tiktok//video//{TTID}//", TTV, "video", None),
    (f"https://www.tiktok.com/%40tiktok/video/{TTID}", TTV, "video", None),
    (f"https://www.tiktok.com/@User.Name-1_x/video/{TTID}", f"https://www.tiktok.com/@User.Name-1_x/video/{TTID}",
     "video", None),
    (f"https://www.tiktok.com/@tiktok/Video/{TTID}", TTV, "video", None),
    (f"https://www.tiktok.com/@tiktok/VIDEO/{TTID}", TTV, "video", None),
    ("https://www.tiktok.com/@tiktok/video/" + "1" * 15, "https://www.tiktok.com/@tiktok/video/" + "1" * 15,
     "video", None),
    ("https://www.tiktok.com/@tiktok/video/" + "9" * 25, "https://www.tiktok.com/@tiktok/video/" + "9" * 25,
     "video", None),
    # forms without a user
    (f"https://www.tiktok.com/@/video/{TTID}", TTN, "video", None),
    (f"https://www.tiktok.com/video/{TTID}", TTN, "video", None),
    (f"https://www.tiktok.com/embed/{TTID}", TTN, "video", None),
    (f"https://www.tiktok.com/embed/v2/{TTID}", TTN, "video", None),
    (f"https://www.tiktok.com/EMBED/V2/{TTID}?lang=en", TTN, "video", None),
    (f"https://m.tiktok.com/v/{TTID}.html", TTN, "video", None),
    (f"https://m.tiktok.com/v/{TTID}.html?u_code=abc&share_app_id=1233", TTN, "video", None),
    (f"m.tiktok.com/v/{TTID}", TTN, "video", None),
    # share links
    ("https://vm.tiktok.com/ZMabcdEF1/", TTS, "share", None),
    ("https://vm.tiktok.com/ZMabcdEF1", TTS, "share", None),
    ("vm.tiktok.com/ZMabcdEF1/?foo=bar", TTS, "share", None),
    ("https://VM.TIKTOK.COM/ZMabcdEF1", TTS, "share", None),
    ("https://vt.tiktok.com/ZSabc12345/", "https://vt.tiktok.com/ZSabc12345", "share", None),
    ("http://vt.tiktok.com/ZSabc12345#x", "https://vt.tiktok.com/ZSabc12345", "share", None),
    ("https://www.tiktok.com/t/ZT8abcdef/", "https://www.tiktok.com/t/ZT8abcdef", "share", None),
    ("tiktok.com/t/ZT8abcdef?lang=en", "https://www.tiktok.com/t/ZT8abcdef", "share", None),
    ("https://m.tiktok.com/t/ZT8abcdef", "https://www.tiktok.com/t/ZT8abcdef", "share", None),
    ("https://tiktok.com/T/ZT8abcdef", "https://www.tiktok.com/t/ZT8abcdef", "share", None),
    ("https://vm.tiktok.com/abcdef", "https://vm.tiktok.com/abcdef", "share", None),
    ("https://vm.tiktok.com/" + "A" * 20, "https://vm.tiktok.com/" + "A" * 20, "share", None),
    # unsupported paths and hosts
    ("https://www.tiktok.com", None, None, "unsupported_path"),
    ("https://www.tiktok.com/", None, None, "unsupported_path"),
    ("https://vm.tiktok.com", None, None, "unsupported_path"),
    ("https://vt.tiktok.com/", None, None, "unsupported_path"),
    ("https://www.tiktok.com/@tiktok", None, None, "unsupported_path"),
    ("https://www.tiktok.com/@tiktok/?lang=en", None, None, "unsupported_path"),
    (f"https://www.tiktok.com/@tiktok/photo/{TTID}", None, None, "unsupported_path"),
    (f"https://www.tiktok.com/photo/{TTID}", None, None, "unsupported_path"),
    ("https://www.tiktok.com/music/Original-Sound-6812345678901234567", None, None, "unsupported_path"),
    ("https://www.tiktok.com/tag/cats", None, None, "unsupported_path"),
    ("https://www.tiktok.com/discover", None, None, "unsupported_path"),
    ("https://www.tiktok.com/discover/funny-cats", None, None, "unsupported_path"),
    ("https://www.tiktok.com/live", None, None, "unsupported_path"),
    ("https://www.tiktok.com/@tiktok/live", None, None, "unsupported_path"),
    ("https://www.tiktok.com/search?q=cats", None, None, "unsupported_path"),
    ("https://www.tiktok.com/foryou", None, None, "unsupported_path"),
    ("https://www.tiktok.com/following", None, None, "unsupported_path"),
    ("https://www.tiktok.com/explore", None, None, "unsupported_path"),
    ("https://www.tiktok.com/upload", None, None, "unsupported_path"),
    ("https://www.tiktok.com/login", None, None, "unsupported_path"),
    ("https://www.tiktok.com/legal/terms", None, None, "unsupported_path"),
    (f"https://www.tiktok.com/@us$er/video/{TTID}", None, None, "unsupported_path"),
    (f"https://ads.tiktok.com/video/{TTID}", None, None, "unsupported_path"),
    ("https://business.tiktok.com/", None, None, "unsupported_path"),
    # missing or malformed id / code
    ("https://www.tiktok.com/@tiktok/video/", None, None, "no_shortcode"),
    ("https://www.tiktok.com/@tiktok/video", None, None, "no_shortcode"),
    ("https://www.tiktok.com/@tiktok/video/?lang=en", None, None, "no_shortcode"),
    ("https://www.tiktok.com/@tiktok/video/abc", None, None, "no_shortcode"),
    ("https://www.tiktok.com/@tiktok/video/12345", None, None, "no_shortcode"),
    ("https://www.tiktok.com/@tiktok/video/" + "1" * 14, None, None, "no_shortcode"),
    ("https://www.tiktok.com/@tiktok/video/" + "1" * 26, None, None, "no_shortcode"),
    ("https://www.tiktok.com/@tiktok/video/71065943122924x3675", None, None, "no_shortcode"),
    ("https://www.tiktok.com/@/video/", None, None, "no_shortcode"),
    ("https://www.tiktok.com/video/", None, None, "no_shortcode"),
    ("https://www.tiktok.com/embed/", None, None, "no_shortcode"),
    ("https://www.tiktok.com/embed/v2/", None, None, "no_shortcode"),
    ("https://www.tiktok.com/embed/abc", None, None, "no_shortcode"),
    ("https://m.tiktok.com/v/", None, None, "no_shortcode"),
    ("https://m.tiktok.com/v/abc.html", None, None, "no_shortcode"),
    ("https://www.tiktok.com/t/", None, None, "no_shortcode"),
    ("https://www.tiktok.com/t/abc", None, None, "no_shortcode"),
    ("https://www.tiktok.com/t/" + "a" * 21, None, None, "no_shortcode"),
    ("https://vm.tiktok.com/abc", None, None, "no_shortcode"),
    ("https://vm.tiktok.com/ab$cdefg", None, None, "no_shortcode"),
    ("https://vt.tiktok.com/" + "a" * 21, None, None, "no_shortcode"),
]


class TikTokLinkTests(unittest.TestCase):
    def test_table(self):
        self.assertGreaterEqual(len(TIKTOK_CASES), 60)
        for raw, clean, kind, error in TIKTOK_CASES:
            with self.subTest(raw=raw):
                res = clean_link(raw)
                self.assertEqual(res.original, raw)
                self.assertEqual(res.clean, clean)
                self.assertEqual(res.kind, kind)
                self.assertEqual(res.error, error)
                self.assertEqual(res.platform, "tiktok")
                if clean:
                    self.assertEqual(res.shortcode, clean.rsplit("/", 1)[1])
                else:
                    self.assertIsNone(res.shortcode)

    def test_case_handling(self):
        self.assertEqual(clean_link("https://VM.TikTok.com/ZmAbCdEf1").clean, "https://vm.tiktok.com/ZmAbCdEf1")
        self.assertEqual(clean_link(f"https://www.tiktok.com/@MixedCase/video/{TTID}").clean,
                         f"https://www.tiktok.com/@MixedCase/video/{TTID}")

    def test_extract_scheme_less_and_glued(self):
        text = (f"a tiktok.com/@tiktok/video/{TTID}?lang=en, b vm.tiktok.com/ZMabcdEF1! c www.tiktok.com/t/ZT8abcdef."
                f" d m.tiktok.com/v/{TTID}.html [md](https://vt.tiktok.com/ZSabc12345/)"
                f"https://www.tiktok.com/@tiktok/video/{TTID2}https://vm.tiktok.com/ZMabcdEF1")
        self.assertEqual(extract_candidates(text), [
            f"tiktok.com/@tiktok/video/{TTID}?lang=en",
            "vm.tiktok.com/ZMabcdEF1",
            "www.tiktok.com/t/ZT8abcdef",
            f"m.tiktok.com/v/{TTID}.html",
            "https://vt.tiktok.com/ZSabc12345/",
            f"https://www.tiktok.com/@tiktok/video/{TTID2}",
            "https://vm.tiktok.com/ZMabcdEF1",
        ])

    def test_lookalike_hosts(self):
        self.assertEqual(clean_link(f"https://nottiktok.com/@a/video/{TTID}").error, "not_supported")
        self.assertEqual(clean_link(f"https://tiktok.com.evil.com/@a/video/{TTID}").error, "not_supported")
        self.assertEqual(clean_link(f"https://tiktok.co/@a/video/{TTID}").error, "not_supported")
        self.assertEqual(extract_candidates(f"faketiktok.com/@a/video/{TTID}"), [])


XID = "1577855540407197696"
XP = f"https://x.com/oshtru/status/{XID}"
XI = f"https://x.com/i/status/{XID}"

# (raw, clean, kind, error); every row is an X link (platform "twitter")
TWITTER_CASES = [
    # posts with a user
    (f"https://x.com/oshtru/status/{XID}?s=20&t=abc", XP, "post", None),
    (f"https://x.com/oshtru/status/{XID}", XP, "post", None),
    (f"https://x.com/oshtru/status/{XID}/", XP, "post", None),
    (f"https://twitter.com/oshtru/status/{XID}", XP, "post", None),
    (f"http://www.twitter.com/oshtru/status/{XID}?ref_src=twsrc%5Etfw%7Ctwcamp%5Etweetembed", XP, "post", None),
    (f"https://www.x.com/oshtru/status/{XID}", XP, "post", None),
    (f"https://mobile.twitter.com/oshtru/status/{XID}", XP, "post", None),
    (f"https://m.twitter.com/oshtru/status/{XID}/", XP, "post", None),
    (f"https://mobile.x.com/oshtru/status/{XID}", XP, "post", None),
    (f"x.com/oshtru/status/{XID}", XP, "post", None),
    (f"twitter.com/oshtru/status/{XID}?s=46", XP, "post", None),
    (f"www.twitter.com/oshtru/status/{XID}#frag", XP, "post", None),
    (f"https://X.COM/oshtru/STATUS/{XID}", XP, "post", None),
    (f"https://x.com/OshTru/status/{XID}", f"https://x.com/OshTru/status/{XID}", "post", None),
    (f"https://x.com/oshtru/statuses/{XID}", XP, "post", None),
    (f"https://twitter.com/oshtru/Statuses/{XID}", XP, "post", None),
    (f"https://x.com//oshtru//status//{XID}//", XP, "post", None),
    (f"https://x.com/%6Fshtru/status/{XID}", XP, "post", None),
    (f"https://x.com/a_b1/status/{XID}", f"https://x.com/a_b1/status/{XID}", "post", None),
    (f"https://x.com/{'u' * 15}/status/{XID}", f"https://x.com/{'u' * 15}/status/{XID}", "post", None),
    ("https://x.com/oshtru/status/1234567890", "https://x.com/oshtru/status/1234567890", "post", None),
    ("https://x.com/oshtru/status/" + "7" * 25, "https://x.com/oshtru/status/" + "7" * 25, "post", None),
    # tails
    (f"https://x.com/oshtru/status/{XID}/video/1", XP, "post", None),
    (f"https://x.com/oshtru/status/{XID}/photo/2", XP, "post", None),
    (f"https://twitter.com/oshtru/status/{XID}/photo/1?s=20", XP, "post", None),
    (f"https://x.com/oshtru/status/{XID}/analytics", XP, "post", None),
    (f"https://x.com/oshtru/status/{XID}/retweets", XP, "post", None),
    (f"https://x.com/oshtru/status/{XID}/retweets/with_comments", XP, "post", None),
    (f"https://x.com/oshtru/status/{XID}/likes", XP, "post", None),
    (f"https://x.com/oshtru/status/{XID}/history", XP, "post", None),
    # forms without a user
    (f"https://x.com/i/status/{XID}", XI, "post", None),
    (f"https://twitter.com/i/web/status/{XID}", XI, "post", None),
    (f"twitter.com/i/web/status/{XID}?s=20", XI, "post", None),
    (f"https://x.com/I/Web/Status/{XID}", XI, "post", None),
    (f"https://twitter.com/statuses/{XID}", XI, "post", None),
    (f"https://mobile.twitter.com/i/status/{XID}/", XI, "post", None),
    # unsupported paths and hosts
    ("https://x.com", None, None, "unsupported_path"),
    ("https://x.com/", None, None, "unsupported_path"),
    ("https://twitter.com", None, None, "unsupported_path"),
    ("https://x.com/oshtru", None, None, "unsupported_path"),
    ("https://x.com/oshtru/", None, None, "unsupported_path"),
    ("https://x.com/home", None, None, "unsupported_path"),
    ("https://x.com/explore", None, None, "unsupported_path"),
    ("https://x.com/search?q=cats", None, None, "unsupported_path"),
    ("https://x.com/i/spaces/1OdKrBXXXXXXX", None, None, "unsupported_path"),
    ("https://x.com/i/broadcasts/1yNGaNVZXXXXX", None, None, "unsupported_path"),
    ("https://x.com/i/lists/1234567890", None, None, "unsupported_path"),
    ("https://x.com/i/communities/1234567890", None, None, "unsupported_path"),
    ("https://x.com/i/flow/login", None, None, "unsupported_path"),
    ("https://x.com/i/web", None, None, "unsupported_path"),
    ("https://x.com/hashtag/cats", None, None, "unsupported_path"),
    ("https://x.com/oshtru/media", None, None, "unsupported_path"),
    ("https://x.com/oshtru/with_replies", None, None, "unsupported_path"),
    ("https://x.com/oshtru/likes", None, None, "unsupported_path"),
    ("https://x.com/messages", None, None, "unsupported_path"),
    ("https://x.com/notifications", None, None, "unsupported_path"),
    ("https://x.com/settings", None, None, "unsupported_path"),
    ("https://x.com/login", None, None, "unsupported_path"),
    (f"https://x.com/{'u' * 16}/status/{XID}", None, None, "unsupported_path"),
    (f"https://x.com/bad-user/status/{XID}", None, None, "unsupported_path"),
    ("https://help.twitter.com/en/using-x", None, None, "unsupported_path"),
    ("https://developer.x.com/en/docs", None, None, "unsupported_path"),
    ("https://business.twitter.com/", None, None, "unsupported_path"),
    (f"https://pic.twitter.com/oshtru/status/{XID}", None, None, "unsupported_path"),
    ("https://t.co/AbCdEfGhIj", None, None, "unsupported_path"),
    ("http://t.co/AbCdEfGhIj?amp=1", None, None, "unsupported_path"),
    ("https://t.co/", None, None, "unsupported_path"),
    # missing or malformed id
    ("https://x.com/oshtru/status/", None, None, "no_shortcode"),
    ("https://x.com/oshtru/status", None, None, "no_shortcode"),
    ("https://x.com/oshtru/status/?s=20", None, None, "no_shortcode"),
    ("https://x.com/oshtru/status/abc", None, None, "no_shortcode"),
    ("https://x.com/oshtru/status/123", None, None, "no_shortcode"),
    ("https://x.com/oshtru/status/123456789", None, None, "no_shortcode"),
    ("https://x.com/oshtru/status/" + "1" * 26, None, None, "no_shortcode"),
    ("https://x.com/oshtru/status/157785554x407197696", None, None, "no_shortcode"),
    ("https://x.com/oshtru/statuses/", None, None, "no_shortcode"),
    ("https://x.com/i/status/", None, None, "no_shortcode"),
    ("https://x.com/i/status/abc", None, None, "no_shortcode"),
    ("https://twitter.com/i/web/status/", None, None, "no_shortcode"),
    ("https://twitter.com/statuses/", None, None, "no_shortcode"),
    ("https://twitter.com/statuses/abc", None, None, "no_shortcode"),
]


class TwitterLinkTests(unittest.TestCase):
    def test_table(self):
        self.assertGreaterEqual(len(TWITTER_CASES), 60)
        for raw, clean, kind, error in TWITTER_CASES:
            with self.subTest(raw=raw):
                res = clean_link(raw)
                self.assertEqual(res.original, raw)
                self.assertEqual(res.clean, clean)
                self.assertEqual(res.kind, kind)
                self.assertEqual(res.error, error)
                self.assertEqual(res.platform, "twitter")
                if clean:
                    self.assertEqual(res.shortcode, clean.rsplit("/", 1)[1])
                else:
                    self.assertIsNone(res.shortcode)

    def test_extract_scheme_less_and_glued(self):
        text = (f"a x.com/oshtru/status/{XID}?s=20, b twitter.com/i/web/status/{XID}! c mobile.twitter.com/u/status/{XID}."
                f" d www.x.com/home [md](https://m.twitter.com/oshtru/status/{XID}/)"
                f"https://x.com/oshtru/status/{XID}https://t.co/AbCdEfGhIj")
        self.assertEqual(extract_candidates(text), [
            f"x.com/oshtru/status/{XID}?s=20",
            f"twitter.com/i/web/status/{XID}",
            f"mobile.twitter.com/u/status/{XID}",
            "www.x.com/home",
            f"https://m.twitter.com/oshtru/status/{XID}/",
            f"https://x.com/oshtru/status/{XID}",
            "https://t.co/AbCdEfGhIj",
        ])

    def test_bare_x_com_needs_a_path_query_or_fragment(self):
        self.assertEqual(extract_candidates("visit x.com today"), [])
        self.assertEqual(extract_candidates("go to x.com."), [])
        self.assertEqual(extract_candidates("x.com"), [])
        self.assertEqual(extract_candidates("see x.com?s=20 now"), ["x.com?s=20"])
        self.assertEqual(extract_candidates("see x.com#top now"), ["x.com#top"])
        self.assertEqual(extract_candidates("see x.com/ now"), ["x.com/"])

    def test_x_com_inside_words_and_addresses_is_not_a_link(self):
        for text in ("box.com/oshtru/status/1577855540407197696", "max.com/home", "https://box.com/a",
                     "me@x.com/oshtru", "foo-x.com/home", "https://evil.com/?u=x.com/home"):
            with self.subTest(text=text):
                found = extract_candidates(text)
                self.assertFalse([c for c in found if clean_link(c).platform == "twitter"], found)

    def test_lookalike_hosts(self):
        self.assertEqual(clean_link(f"https://notx.com/oshtru/status/{XID}").error, "not_supported")
        self.assertEqual(clean_link(f"https://x.com.evil.com/oshtru/status/{XID}").error, "not_supported")
        self.assertEqual(clean_link(f"https://nottwitter.com/oshtru/status/{XID}").error, "not_supported")
        self.assertEqual(clean_link(f"https://twitter.com.evil.com/oshtru/status/{XID}").error, "not_supported")
        self.assertEqual(clean_link(f"https://x.co/oshtru/status/{XID}").error, "not_supported")
        self.assertEqual(clean_link(f"https://at.co/{XID}").error, "not_supported")


class FourPlatformTests(unittest.TestCase):
    TEXT = (
        f"1) {R}/?igsh=aaa\n"
        f"2) https://youtu.be/{YID}?t=10\n"
        f"3) {TTV}?is_from_webapp=1&sender_device=pc\n"
        f"4) {TTS}/\n"
        f"5) {XP}?s=20\n"
        f"6) twitter.com/oshtru/status/{XID}\n"      # same URL as 5): twitter.com is rewritten to x.com
        "7) https://x.com/home\n"
        "8) https://vimeo.com/1"
    )
    ITEMS = [("instagram", "reel"), ("youtube", "video"), ("tiktok", "video"), ("tiktok", "share"),
             ("twitter", "post")]
    CLEAN = [R, YV, TTV, TTS, XP]

    def test_all_platforms(self):
        res = clean_text(self.TEXT)
        self.assertEqual([(i["platform"], i["kind"], i["error"]) for i in res["items"]],
                         [(p, k, None) for p, k in self.ITEMS] + [("twitter", None, "unsupported_path"),
                                                                  (None, None, "not_supported")])
        self.assertEqual((res["valid"], res["invalid"], res["duplicates"]), (5, 2, 1))
        self.assertEqual(res["clean_text"].split("\n"), self.CLEAN)

    def test_platform_filters(self):
        for platform in ("instagram", "youtube", "tiktok", "twitter"):
            with self.subTest(platform=platform):
                res = clean_text(self.TEXT, platform)
                errors = [i["error"] for i in res["items"]]
                expected = [None if p == platform else "other_platform" for p, _ in self.ITEMS]
                self.assertEqual(errors, expected + ["unsupported_path", "not_supported"])
                for item, (p, k), clean in zip(res["items"], self.ITEMS, self.CLEAN):
                    self.assertEqual((item["platform"], item["kind"], item["clean"]), (p, k, clean))
                    self.assertTrue(item["shortcode"])
                own = [c for (p, _), c in zip(self.ITEMS, self.CLEAN) if p == platform]
                self.assertEqual((res["valid"], res["invalid"], res["duplicates"]), (len(own), 7 - len(own), 1))
                self.assertEqual(res["clean_text"], "\n".join(own))

    def test_unknown_platform_still_rejected(self):
        with self.assertRaises(ValueError):
            clean_text(R, "vimeo")

    def test_x_com_and_twitter_com_are_one_post(self):
        text = (f"x.com/u/status/{XID}\ntwitter.com/u/status/{XID}\nhttps://x.com/u/status/{XID}?s=20\n"
                f"https://mobile.twitter.com/u/status/{XID}/photo/1")
        res = clean_text(text)
        self.assertEqual(res["clean_text"], f"https://x.com/u/status/{XID}")
        self.assertEqual((res["valid"], res["duplicates"]), (1, 3))

    def test_i_web_status_is_a_different_url_than_the_user_form(self):
        text = (f"{TTV}?is_from_webapp=1 https://x.com/oshtru/status/{XID}?s=20 "
                f"https://x.com/oshtru/status/{XID}/ twitter.com/i/web/status/{XID}")
        res = clean_text(text)
        self.assertEqual(res["clean_text"].split("\n"), [TTV, XP, XI])
        self.assertEqual((res["valid"], res["duplicates"]), (3, 1))

    def test_tiktok_full_url_and_short_link_are_different_links(self):
        res = clean_text(f"{TTV} {TTS} {TTN} vt.tiktok.com/ZMabcdEF1 https://www.tiktok.com/t/ZMabcdEF1")
        self.assertEqual(res["clean_text"].split("\n"), [
            TTV, TTS, TTN, "https://vt.tiktok.com/ZMabcdEF1", "https://www.tiktok.com/t/ZMabcdEF1"])
        self.assertEqual((res["valid"], res["duplicates"]), (5, 0))

    def test_cli_example_prints_three_lines(self):
        text = ("see https://www.tiktok.com/@tiktok/video/7106594312292453675?is_from_webapp=1 and "
                "https://x.com/oshtru/status/1577855540407197696?s=20 and "
                "twitter.com/i/web/status/1577855540407197696\n")
        res = clean_text(text)
        self.assertEqual(res["clean_text"].split("\n"), [
            "https://www.tiktok.com/@tiktok/video/7106594312292453675",
            "https://x.com/oshtru/status/1577855540407197696",
            "https://x.com/i/status/1577855540407197696"])
        root = Path(__file__).resolve().parent.parent
        out = subprocess.run([sys.executable, "-m", "app.core.linkclean"], input=text.encode("utf-8"),
                             cwd=root, capture_output=True, timeout=60)
        self.assertEqual(out.returncode, 0)
        self.assertEqual(out.stdout.decode("utf-8").splitlines(), res["clean_text"].split("\n"))


if __name__ == "__main__":
    unittest.main()
