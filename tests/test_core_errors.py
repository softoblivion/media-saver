"""stderr -> ErrorCode classification. Samples are real yt-dlp nightly 2026.10.10 / gallery-dl 1.32.17 output."""
import unittest

from app.core.downloader import classify_error

EMPTY_MEDIA = (
    "WARNING: [Instagram] DZZZZZZZZZZ: Instagram API is not granting access\n"
    "ERROR: [Instagram] DZZZZZZZZZZ: Instagram sent an empty media response. Check if this post is accessible in "
    "your browser without being logged-in. If it is not, then use --cookies-from-browser or --cookies for the "
    "authentication. See  https://github.com/yt-dlp/yt-dlp/wiki/FAQ#how-do-i-pass-cookies-to-yt-dlp  for how to "
    "manually pass cookies. Otherwise, if the post is accessible in browser without being logged-in, please report "
    "this issue on  https://github.com/yt-dlp/yt-dlp/issues?q= , filling out the appropriate issue template. "
    "Confirm you are on the latest version using  yt-dlp -U")
PRIVATE = (
    "ERROR: [Instagram] DAbCdEfGhIjKlMnOpQrStUvWxYz0123456789abcd: This content is only available for registered "
    "users who follow this account. Use --cookies-from-browser or --cookies for the authentication. See  "
    "https://github.com/yt-dlp/yt-dlp/wiki/FAQ#how-do-i-pass-cookies-to-yt-dlp  for how to manually pass cookies")
NETWORK = (
    "ERROR: [Instagram] DQCkNLtgqEe: Unable to download webpage: Failed to perform, curl: (7) Failed to connect to "
    "www.instagram.com:443 over proxy 127.0.0.1 after 2034 ms: Could not connect to server. See "
    "https://curl.se/libcurl/c/libcurl-errors.html first for more details. (caused by TransportError('Failed to "
    "perform, curl: (7) Failed to connect to www.instagram.com:443 over proxy 127.0.0.1 after 2034 ms'))")
BAD_COOKIES_JSON = (
    "ERROR: [Instagram] DQCkNLtgqEe: Failed to parse JSON (caused by JSONDecodeError(\"Expecting value in '': line 1 "
    "column 1 (char 0)\")); please report this issue on  https://github.com/yt-dlp/yt-dlp/issues?q= , filling out "
    "the appropriate issue template. Confirm you are on the latest version using  yt-dlp -U")
LOGIN_REDIRECT_RATE = (
    "ERROR: [Instagram] DQCkNLtgqEe: The webpage request was redirected to the login page. You have exceeded the "
    "rate-limit for accessing posts anonymously. Use --cookies-from-browser or --cookies for the authentication.")
OLD_RATE_OR_LOGIN = (
    "ERROR: [Instagram] C1a2b3c4d5e: Requested content is not available, rate-limit reached or login required. "
    "Use --cookies, --cookies-from-browser, --username and --password, --netrc-cmd, or --netrc (instagram) to "
    "provide account credentials")
HTTP_429 = "ERROR: [Instagram] DQCkNLtgqEe: Unable to download JSON metadata: HTTP Error 429: Too Many Requests"
HTTP_404 = "ERROR: [Instagram] DQCkNLtgqEe: Unable to download webpage: HTTP Error 404: Not Found (caused by <HTTPError 404: Not Found>)"
NO_VIDEO = "ERROR: [Instagram] DAbC123: There is no video in this post"
RESTRICTED = "ERROR: [Instagram] DAbC123: Restricted Video: You must be 18 years old or over to see this video. Use --cookies"
FFMPEG = ("ERROR: You have requested merging of multiple formats but ffmpeg is not installed. Aborting due to "
          "--abort-on-error")
DISK = "ERROR: unable to write data: [Errno 28] No space left on device"
DISK_PERM = "ERROR: unable to open for writing: [Errno 13] Permission denied: 'C:\\\\out\\\\a.mp4'"
TIMEOUT = "ERROR: [Instagram] X: Unable to download webpage: The read operation timed out (caused by TransportError('The read operation timed out'))"
DNS = "ERROR: [Instagram] X: Unable to download webpage: [Errno 11001] getaddrinfo failed (caused by TransportError('[Errno 11001] getaddrinfo failed'))"
EXTRACT = "ERROR: [Instagram] X: Unable to extract shared data; please report this issue on  https://github.com/yt-dlp/yt-dlp/issues?q="
FORMAT_GONE = "ERROR: [info] DQCkNLtgqEe: Requested format is not available. Use --list-formats for a list of available formats"
GDL_LOGIN = "[instagram][error] HTTP redirect to login page (https://www.instagram.com/accounts/login/)"
GDL_404 = "[instagram][error] HttpError: '404 Not Found' for 'https://www.instagram.com/api/v1/media/1/info/'"
GDL_AUTH = "[instagram][error] AuthorizationError: Login required"


class ClassifyTest(unittest.TestCase):
    def check(self, text, code):
        got, msg = classify_error(text)
        self.assertEqual(got, code, text[:80])
        self.assertRegex(msg, "[A-Za-z]")

    def test_samples(self):
        cases = [
            (EMPTY_MEDIA, "login_required"),
            (PRIVATE, "private"),
            (NETWORK, "network"),
            (BAD_COOKIES_JSON, "extractor"),
            (LOGIN_REDIRECT_RATE, "rate_limited"),
            (OLD_RATE_OR_LOGIN, "login_required"),
            (HTTP_429, "rate_limited"),
            (HTTP_404, "not_found"),
            (NO_VIDEO, "not_found"),
            (RESTRICTED, "login_required"),
            (FFMPEG, "ffmpeg_missing"),
            (DISK, "disk"),
            (DISK_PERM, "disk"),
            (TIMEOUT, "network"),
            (DNS, "network"),
            (EXTRACT, "extractor"),
            (FORMAT_GONE, "extractor"),
            (GDL_LOGIN, "login_required"),
            (GDL_404, "not_found"),
            (GDL_AUTH, "login_required"),
            ("", "unknown"),
            ("something odd happened", "unknown"),
        ]
        for text, code in cases:
            with self.subTest(code=code, text=text[:50]):
                self.check(text, code)

    def test_error_lines_win_over_warnings(self):
        text = ("WARNING: [Instagram] X: The provided Instagram account cookies are no longer valid\n" + NETWORK)
        self.assertEqual(classify_error(text)[0], "network")

    def test_no_video_message(self):
        self.assertIn("no video", classify_error(NO_VIDEO)[1])


if __name__ == "__main__":
    unittest.main()
