import os
import shutil
import tempfile
import time
import unittest

from app.core import auth


def ig_cookies(expires=None, with_session=True):
    exp = expires if expires is not None else time.time() + 3600 * 24 * 300
    cookies = [
        {"name": "csrftoken", "value": "abc", "domain": ".instagram.com", "path": "/", "secure": True,
         "http_only": False, "expires": exp},
        {"name": "ds_user_id", "value": "1234567", "domain": ".instagram.com", "path": "/", "secure": True,
         "http_only": False, "expires": exp},
        {"name": "mid", "value": "Zz\tq", "domain": "www.instagram.com", "path": "/", "secure": True,
         "http_only": True, "expires": None},
    ]
    if with_session:
        cookies.append({"name": "sessionid", "value": "1234567%3Aabc%3A1%3AAYf", "domain": ".instagram.com",
                        "path": "/", "secure": True, "http_only": True, "expires": exp})
    return cookies


class AuthTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="idl_auth_")
        self.path = os.path.join(self.tmp, "auth", "cookies.txt")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_roundtrip(self):
        src = ig_cookies()
        auth.write_netscape_cookies(src, self.path)
        with open(self.path, encoding="utf-8") as fh:
            text = fh.read()
        self.assertTrue(text.startswith("# Netscape HTTP Cookie File"))
        back = {c["name"]: c for c in auth.parse_netscape(text)}
        self.assertEqual(set(back), {"csrftoken", "ds_user_id", "mid", "sessionid"})
        self.assertEqual(back["sessionid"]["value"], "1234567%3Aabc%3A1%3AAYf")
        self.assertEqual(back["sessionid"]["domain"], ".instagram.com")
        self.assertTrue(back["sessionid"]["secure"])
        self.assertIsNone(back["mid"]["expires"])
        self.assertEqual(back["mid"]["value"], "Zz q")  # tab stripped
        self.assertEqual(int(back["csrftoken"]["expires"]), int(src[0]["expires"]))
        self.assertIn("\tFALSE\t", [l for l in text.splitlines() if l.startswith("www.instagram.com")][0])

    def test_status_logged_in(self):
        auth.write_netscape_cookies(ig_cookies(), self.path)
        st = auth.read_auth_status(self.path)
        self.assertTrue(st["logged_in"])
        self.assertEqual(st["user_id"], "1234567")
        self.assertEqual(st["source"], "session")
        self.assertIsInstance(st["saved_at"], float)
        self.assertEqual(set(st), {"logged_in", "user_id", "source", "saved_at"})

    def test_status_missing_expired_or_no_session(self):
        self.assertEqual(auth.read_auth_status(self.path), auth.logged_out_status())
        auth.write_netscape_cookies(ig_cookies(expires=time.time() - 10), self.path)
        self.assertFalse(auth.read_auth_status(self.path)["logged_in"])
        auth.write_netscape_cookies(ig_cookies(with_session=False), self.path)
        self.assertFalse(auth.read_auth_status(self.path)["logged_in"])

    def test_other_domain_session_ignored(self):
        auth.write_netscape_cookies([{"name": "sessionid", "value": "x", "domain": ".example.com", "path": "/",
                                      "secure": True, "http_only": True, "expires": None}], self.path)
        self.assertFalse(auth.read_auth_status(self.path)["logged_in"])

    def test_httponly_prefix_written_back_by_ytdlp(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        exp = int(time.time()) + 1000
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write("# Netscape HTTP Cookie File\n\n"
                     f"#HttpOnly_.instagram.com\tTRUE\t/\tTRUE\t{exp}\tsessionid\tS1\n"
                     f".instagram.com\tTRUE\t/\tTRUE\t{exp}\tds_user_id\t42\n")
        st = auth.read_auth_status(self.path)
        self.assertTrue(st["logged_in"])
        self.assertEqual(st["user_id"], "42")
        self.assertTrue(auth.jar_has_session(self.path))

    def test_mark_invalid_and_relogin(self):
        auth.write_netscape_cookies(ig_cookies(), self.path)
        auth.mark_invalid(self.path)
        self.assertFalse(auth.read_auth_status(self.path)["logged_in"])
        auth.write_netscape_cookies(ig_cookies(), self.path)
        self.assertTrue(auth.read_auth_status(self.path)["logged_in"])

    def test_import_validation(self):
        src = os.path.join(self.tmp, "user_cookies.txt")
        cases = {
            "": "empty",
            "hello world\n": "not a cookies file",
            ".example.com\tTRUE\t/\tTRUE\t0\tsid\tx\n": "no Instagram cookies",
            ".instagram.com\tTRUE\t/\tTRUE\t0\tcsrftoken\tx\n": "sessionid",
            f".instagram.com\tTRUE\t/\tTRUE\t{int(time.time()) - 5}\tsessionid\tx\n": "expired",
        }
        for text, needle in cases.items():
            with self.subTest(needle=needle):
                with open(src, "w", encoding="utf-8") as fh:
                    fh.write(text)
                with self.assertRaises(ValueError) as ctx:
                    auth.import_cookies_file(src, self.path)
                self.assertIn(needle, str(ctx.exception))
        with self.assertRaises(ValueError):
            auth.import_cookies_file(os.path.join(self.tmp, "missing.txt"), self.path)

    def test_import_keeps_instagram_only(self):
        src = os.path.join(self.tmp, "user_cookies.txt")
        exp = int(time.time()) + 1000
        with open(src, "w", encoding="utf-8-sig") as fh:
            fh.write("# Netscape HTTP Cookie File\n"
                     f".google.com\tTRUE\t/\tTRUE\t{exp}\tSID\tsecret\n"
                     f".instagram.com\tTRUE\t/\tTRUE\t{exp}\tsessionid\tS\n"
                     f".instagram.com\tTRUE\t/\tTRUE\t{exp}\tds_user_id\t77\n")
        st = auth.import_cookies_file(src, self.path)
        self.assertEqual((st["logged_in"], st["source"], st["user_id"]), (True, "import", "77"))
        with open(self.path, encoding="utf-8") as fh:
            self.assertNotIn("google", fh.read())

    def test_delete(self):
        auth.write_netscape_cookies(ig_cookies(), self.path)
        auth.delete_cookies(self.path)
        self.assertFalse(os.path.exists(self.path))
        self.assertFalse(auth.read_auth_status(self.path)["logged_in"])
        auth.delete_cookies(self.path)  # idempotent


if __name__ == "__main__":
    unittest.main()
