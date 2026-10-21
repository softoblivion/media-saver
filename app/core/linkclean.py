"""Link cleaning for Instagram, YouTube, TikTok and X: messy text -> canonical media URLs.

Canonical output (scheme, host, kind, id, nothing after it)::

    https://www.instagram.com/reel/<shortcode>      platform "instagram", kind "reel"
    https://www.instagram.com/p/<shortcode>         platform "instagram", kind "p"
    https://www.youtube.com/watch?v=<id>            platform "youtube",   kind "video"
    https://www.youtube.com/shorts/<id>             platform "youtube",   kind "shorts"
    https://www.tiktok.com/@<user>/video/<id>       platform "tiktok",    kind "video"
    https://www.tiktok.com/@/video/<id>             platform "tiktok",    kind "video" (input had no user)
    https://vm.tiktok.com/<code>                    platform "tiktok",    kind "share" (also vt.tiktok.com)
    https://www.tiktok.com/t/<code>                 platform "tiktok",    kind "share"
    https://x.com/<user>/status/<id>                platform "twitter",   kind "post"
    https://x.com/i/status/<id>                     platform "twitter",   kind "post" (input had no user)

Instagram rules
---------------
* Anything after the shortcode is an account referral tail or noise and is dropped: trailing slash, query (igsh,
  utm_source, img_index ...), fragment, /embed, /embed/captioned, /liked_by and so on.
* Shortcode charset ``[A-Za-z0-9_-]``, length 5..64. Its case is preserved (it is case-sensitive); the host is
  case-insensitive and always rewritten to ``www.instagram.com``.
* Accepted input: http/https or no scheme; ``www.`` / ``m.`` / no subdomain; ``instagram.com`` and ``instagr.am``.
  Paths: ``/reel/<sc>``, ``/reels/<sc>``, ``/tv/<sc>``, ``/p/<sc>``, ``/<username>/reel/<sc>``, ``/<username>/p/<sc>``
  (``/<username>/tv/<sc>`` too). ``reel``, ``reels`` and ``tv`` map to kind ``reel`` (``/reel/``); ``p`` stays ``p``.
* ``l.instagram.com/?u=<percent-encoded url>`` is unwrapped and the inner URL is cleaned.
* ``unsupported_path``: profile, /stories/, /explore/, /reels/audio/<id>, bare /reels/, /share/... redirect tokens
  that need a network round trip, other subdomains. ``no_shortcode``: /reel/, /reels/, /tv/ or /p/ without a valid
  shortcode.

YouTube rules
-------------
* Video id: exactly 11 characters of ``[A-Za-z0-9_-]``, case preserved.
* Hosts: ``youtube.com`` with no subdomain, ``www.``, ``m.`` or ``music.``; ``youtu.be``; ``youtube-nocookie.com``
  (with or without ``www.``). Other ``youtube.com`` subdomains -> ``unsupported_path``.
* Kind ``video``: ``/watch?v=<id>`` (``v`` anywhere in the query, other parameters such as ``t``, ``list``,
  ``index``, ``si``, ``pp``, ``feature`` are dropped; a ``list`` next to ``v`` still means the single video),
  ``youtu.be/<id>``, ``/embed/<id>``, ``/v/<id>``, ``/live/<id>``, ``/e/<id>``, ``/watch/<id>``.
  Kind ``shorts``: ``/shorts/<id>``. An old-style ``&param`` glued to the id in the path (``/v/<id>&hl=en``) is
  dropped. ``/attribution_link?u=/watch%3Fv%3D<id>`` is unwrapped.
* ``unsupported_path``: bare host, ``/playlist``, ``/channel/``, ``/@handle``, ``/c/``, ``/user/``, ``/results``,
  ``/feed/``, ``/embed/videoseries`` and any other path. ``no_shortcode``: ``/watch`` without a valid ``v``, and
  ``/shorts/``, ``/embed/``, ``/v/``, ``/live/``, ``/e/``, ``youtu.be/`` with a missing or malformed id.

TikTok rules
------------
* Hosts: ``tiktok.com`` with no subdomain, ``www.`` or ``m.``; the short-link hosts ``vm.tiktok.com`` and
  ``vt.tiktok.com``. Other subdomains -> ``unsupported_path``.
* Kind ``video``: ``/@<user>/video/<id>`` (``<user>`` kept as given: word characters, ``.`` and ``-``), ``/@/video/<id>``,
  ``/video/<id>``, ``/embed/<id>``, ``/embed/v2/<id>``, ``/v/<id>.html`` (old ``m.tiktok.com`` form; the ``.html``
  is stripped). The id is 15..25 digits, anything else -> ``no_shortcode``. Input without a user comes out as
  ``https://www.tiktok.com/@/video/<id>``.
* Kind ``share``: ``tiktok.com/t/<code>``, ``vm.tiktok.com/<code>``, ``vt.tiktok.com/<code>``; the target id is
  unknown until the engine follows the link, so the host is kept (``shortcode`` = ``<code>``). The code is 6..20
  characters of ``[A-Za-z0-9]``, else ``no_shortcode``. A short-link host without a path -> ``unsupported_path``.
* ``unsupported_path``: bare host, profile ``/@<user>``, photo posts ``/@<user>/photo/<id>`` and ``/photo/<id>``,
  ``/music/``, ``/tag/``, ``/discover``, ``/live``, ``/@<user>/live``, ``/search``, ``/foryou``, ``/following``,
  ``/explore``, ``/upload``, ``/login`` and any other path. ``/@<user>/video/`` with a missing or malformed id ->
  ``no_shortcode``.
* Query (``is_from_webapp``, ``sender_device``, ``_r``, ``_t``, ``lang`` ...), fragment and trailing slash are dropped.
  Segment keywords are case-insensitive, ids and users keep their case.

X (Twitter) rules
-----------------
* Hosts: ``x.com`` and ``twitter.com`` with no subdomain, ``www.``, ``m.`` or ``mobile.``. Other subdomains
  (``help.``, ``developer.``, ``business.`` ...) -> ``unsupported_path``. ``t.co`` short links need a network round
  trip: host ``t.co`` -> ``unsupported_path`` with platform ``twitter``.
* Kind ``post``: ``/<user>/status/<id>`` and ``/<user>/statuses/<id>`` (``<user>`` kept as given, charset
  ``[A-Za-z0-9_]{1,15}``) -> ``https://x.com/<user>/status/<id>``; ``/i/status/<id>``, ``/i/web/status/<id>`` and
  ``/statuses/<id>`` -> ``https://x.com/i/status/<id>``. The id is 10..25 digits, else ``no_shortcode``.
* Tails (``/video/1``, ``/photo/2``, ``/analytics``, ``/retweets``, ``/likes``, ``/history``), query (``s``, ``t``,
  ``ref_src`` ...), fragment and trailing slash are dropped.
* ``unsupported_path``: bare host, profile ``/<user>``, ``/home``, ``/explore``, ``/search``, ``/i/spaces/``,
  ``/i/broadcasts/``, ``/i/lists/``, ``/i/communities/``, ``/hashtag/``, ``/<user>/media``, ``/<user>/with_replies``,
  ``/messages``, ``/notifications``, ``/settings``, ``/login`` and any other path. ``/<user>/status/`` with a missing
  or malformed id -> ``no_shortcode``.

Common
------
* ``not_supported``: a URL-looking token whose host belongs to none of the four platforms (``t.co`` counts as X).
* ``other_platform`` (``clean_text`` with a ``platform`` filter only): a valid link of another platform;
  ``clean``, ``shortcode``, ``platform`` and ``kind`` stay filled, the item counts as invalid and is left out of
  ``clean_text``.
* Extraction from free text tolerates surrounding text in any language, emoji, quotes, brackets, angle brackets,
  markdown ``[text](url)``, trailing punctuation, CR/LF/tabs, and links glued together without a separator
  (``...?igsh=abchttps://...``). A token is split at every ``http(s)://`` (except one directly after ``=``, which
  belongs to a ``?u=`` query) and at every scheme-less host of the four platforms.
* ``clean_text`` de-duplicates by clean URL keeping the first occurrence (``youtu.be/<id>`` and ``watch?v=<id>``
  are the same URL; ``shorts/<id>`` is a different one; ``x.com/u/status/1``, ``twitter.com/u/status/1`` and
  ``x.com/u/status/1?s=20`` are the same URL while ``x.com/i/status/1`` is another; a TikTok full URL and a short
  link are different links); invalid items are de-duplicated by their original token;
  ``duplicates`` counts all dropped repeats.

CLI: ``python -m app.core.linkclean`` reads stdin and prints the clean links, one per line.
"""

from __future__ import annotations

import re
import sys
from dataclasses import asdict, dataclass
from urllib.parse import parse_qs, unquote

__all__ = ["LinkResult", "PLATFORMS", "extract_candidates", "clean_link", "clean_text"]

PLATFORMS = ("instagram", "youtube", "tiktok", "twitter")

# --- Instagram ---------------------------------------------------------------------------------------
_IG_HOSTS = ("instagram.com", "instagr.am")
_KIND_SEGMENTS = {"reel": "reel", "reels": "reel", "tv": "reel", "p": "p"}
_USER_KIND_SEGMENTS = {"reel": "reel", "tv": "reel", "p": "p"}
_RESERVED_FIRST = {"stories", "explore", "share", "accounts", "direct", "web", "s", "audio", "tags", "locations"}
_SHORTCODE_RE = re.compile(r"[A-Za-z0-9_-]{5,64}\Z")

# --- YouTube -----------------------------------------------------------------------------------------
_YT_HOSTS = ("youtube.com", "youtu.be", "youtube-nocookie.com")
_YT_SUBDOMAINS = ("", "www", "m", "music")
_YT_ID_RE = re.compile(r"[A-Za-z0-9_-]{11}\Z")
_YT_ID_PATHS = {"embed": "video", "v": "video", "live": "video", "e": "video", "watch": "video",
                "shorts": "shorts"}
_YT_NOT_IDS = {"videoseries"}  # /embed/videoseries?list=... is a playlist player

# --- TikTok ------------------------------------------------------------------------------------------
_TT_HOSTS = ("tiktok.com",)
_TT_SUBDOMAINS = ("", "www", "m")
_TT_SHORT_SUBDOMAINS = ("vm", "vt")
_TT_USER_RE = re.compile(r"[\w.-]*\Z")
_TT_ID_RE = re.compile(r"[0-9]{15,25}\Z")
_TT_CODE_RE = re.compile(r"[A-Za-z0-9]{6,20}\Z")

# --- X / Twitter -------------------------------------------------------------------------------------
_TW_HOSTS = ("x.com", "twitter.com", "t.co")
_TW_SUBDOMAINS = ("", "www", "m", "mobile")
_TW_USER_RE = re.compile(r"[A-Za-z0-9_]{1,15}\Z")
_TW_ID_RE = re.compile(r"[0-9]{10,25}\Z")
_TW_STATUS_SEGMENTS = ("status", "statuses")

_MAX_UNWRAP = 3

# Positions where a candidate token may start.
_START_RE = re.compile(
    r"(?<!=)https?://"
    r"|(?<![\w@./:%=-])(?:[a-z0-9-]+\.)?(?:instagram\.com|instagr\.am|youtube\.com|youtu\.be|youtube-nocookie\.com|tiktok\.com|x\.com|twitter\.com)"
    r"(?=[/?#])"
    r"|(?<![\w@./:%=-])www\.[a-z0-9-]+(?:\.[a-z0-9-]+)+",
    re.IGNORECASE,
)
# Characters that end a token inside free text.
_STOP_RE = re.compile("[\\s<>\"'`«»“”„‘’\\[\\]()（）{}|\\\\^]")
_TRAIL = ".,;:!?…*~"
_SCHEME_RE = re.compile(r"^[a-z][a-z0-9+.-]*://", re.IGNORECASE)


@dataclass(frozen=True)
class LinkResult:
    original: str
    clean: str | None
    shortcode: str | None
    platform: str | None  # "instagram" | "youtube" | "tiktok" | "twitter" | None
    kind: str | None  # "reel" | "p" | "video" | "shorts" | "share" (TikTok short link) | "post" (X) | None
    error: str | None  # None | "not_supported" | "unsupported_path" | "no_shortcode" | "other_platform"

    def to_dict(self) -> dict:
        return asdict(self)


def extract_candidates(text: str) -> list[str]:
    """Return URL-like tokens of ``text`` in order of appearance (duplicates kept)."""
    starts = [m.start() for m in _START_RE.finditer(text)]
    out: list[str] = []
    for i, s in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else len(text)
        token = text[s:end]
        stop = _STOP_RE.search(token)
        if stop:
            token = token[: stop.start()]
        token = token.rstrip(_TRAIL)
        if _SCHEME_RE.sub("", token):
            out.append(token)
    return out


def _fail(original: str, error: str, platform: str | None = None) -> LinkResult:
    return LinkResult(original, None, None, platform, None, error)


def _split(raw: str) -> tuple[str, str, str, str]:
    """-> (host, path, query, original authority-less tail) of a URL with or without a scheme."""
    rest = _SCHEME_RE.sub("", raw.strip(), count=1)
    cut = re.search(r"[/?#]", rest)
    authority, tail = (rest[: cut.start()], rest[cut.start():]) if cut else (rest, "")
    host = authority.rsplit("@", 1)[-1].split(":", 1)[0].lower().rstrip(".")
    path, _, query = tail.partition("?")
    path = path.partition("#")[0]
    query = query.partition("#")[0]
    return host, path, query, tail


def _match_host(host: str, bases: tuple[str, ...]) -> tuple[str, str] | None:
    """-> (base, subdomain) when host is one of bases or a subdomain of it."""
    for base in bases:
        if host == base or host.endswith("." + base):
            return base, host[: -len(base)].rstrip(".")
    return None


def _clean(original: str, raw: str, depth: int) -> LinkResult:
    host, path, query, _ = _split(raw)
    ig = _match_host(host, _IG_HOSTS)
    if ig is not None:
        return _clean_instagram(original, ig[0], ig[1], path, query, depth)
    yt = _match_host(host, _YT_HOSTS)
    if yt is not None:
        return _clean_youtube(original, yt[0], yt[1], path, query, depth)
    tt = _match_host(host, _TT_HOSTS)
    if tt is not None:
        return _clean_tiktok(original, tt[1], path)
    tw = _match_host(host, _TW_HOSTS)
    if tw is not None:
        return _clean_twitter(original, tw[0], tw[1], path)
    return _fail(original, "not_supported")


def _clean_instagram(original: str, base: str, sub: str, path: str, query: str, depth: int) -> LinkResult:
    if sub == "l" and base == "instagram.com":
        inner = parse_qs(query).get("u")
        if inner and depth < _MAX_UNWRAP:
            return _clean(original, inner[0], depth + 1)
        return _fail(original, "unsupported_path", "instagram")
    if sub not in ("", "www", "m"):
        return _fail(original, "unsupported_path", "instagram")

    segs = [unquote(s) for s in path.split("/") if s]
    if not segs:
        return _fail(original, "unsupported_path", "instagram")
    first = segs[0].lower()
    if first in _KIND_SEGMENTS:
        kind, sc_segs = _KIND_SEGMENTS[first], segs[1:]
        if first == "reels" and sc_segs and sc_segs[0].lower() == "audio":
            return _fail(original, "unsupported_path", "instagram")
        if first == "reels" and not sc_segs:
            return _fail(original, "unsupported_path", "instagram")
    elif first not in _RESERVED_FIRST and len(segs) >= 2 and segs[1].lower() in _USER_KIND_SEGMENTS:
        kind, sc_segs = _USER_KIND_SEGMENTS[segs[1].lower()], segs[2:]
    else:
        return _fail(original, "unsupported_path", "instagram")

    code = sc_segs[0] if sc_segs else ""
    if not _SHORTCODE_RE.match(code):
        return _fail(original, "no_shortcode", "instagram")
    clean = f"https://www.instagram.com/{kind if kind == 'p' else 'reel'}/{code}"
    return LinkResult(original, clean, code, "instagram", kind, None)


def _yt_result(original: str, code: str, kind: str) -> LinkResult:
    if not _YT_ID_RE.match(code) or code.lower() in _YT_NOT_IDS:
        return _fail(original, "no_shortcode", "youtube")
    if kind == "shorts":
        return LinkResult(original, f"https://www.youtube.com/shorts/{code}", code, "youtube", "shorts", None)
    return LinkResult(original, f"https://www.youtube.com/watch?v={code}", code, "youtube", "video", None)


def _clean_youtube(original: str, base: str, sub: str, path: str, query: str, depth: int) -> LinkResult:
    segs = [unquote(s) for s in path.split("/") if s]
    params = parse_qs(query, keep_blank_values=True)
    if base == "youtu.be":
        if sub not in ("", "www"):
            return _fail(original, "unsupported_path", "youtube")
        if not segs:
            return _fail(original, "unsupported_path", "youtube")
        return _yt_result(original, segs[0].split("&", 1)[0], "video")
    if base == "youtube-nocookie.com":
        if sub not in ("", "www"):
            return _fail(original, "unsupported_path", "youtube")
    elif sub not in _YT_SUBDOMAINS:
        return _fail(original, "unsupported_path", "youtube")

    first = segs[0].lower() if segs else ""
    if first == "attribution_link":
        inner = params.get("u")
        if inner and depth < _MAX_UNWRAP:
            target = inner[0]
            if target.startswith("/"):
                target = "www.youtube.com" + target
            res = _clean(original, target, depth + 1)
            if res.platform == "youtube":
                return res
        return _fail(original, "unsupported_path", "youtube")
    if first == "watch" and len(segs) == 1 or (not segs and "v" in params):
        values = params.get("v") or []
        code = next((v for v in values if v), "")
        return _yt_result(original, code.strip(), "video")
    if first in _YT_ID_PATHS:
        if first == "embed" and len(segs) >= 2 and segs[1].lower() in _YT_NOT_IDS:
            return _fail(original, "unsupported_path", "youtube")
        code = segs[1].split("&", 1)[0] if len(segs) >= 2 else ""
        return _yt_result(original, code, _YT_ID_PATHS[first])
    return _fail(original, "unsupported_path", "youtube")


def _tt_video(original: str, user: str, code: str) -> LinkResult:
    if not _TT_ID_RE.match(code):
        return _fail(original, "no_shortcode", "tiktok")
    return LinkResult(original, f"https://www.tiktok.com/@{user}/video/{code}", code, "tiktok", "video", None)


def _tt_share(original: str, host: str, code: str, path_prefix: str = "") -> LinkResult:
    if not _TT_CODE_RE.match(code):
        return _fail(original, "no_shortcode", "tiktok")
    return LinkResult(original, f"https://{host}{path_prefix}/{code}", code, "tiktok", "share", None)


def _clean_tiktok(original: str, sub: str, path: str) -> LinkResult:
    segs = [unquote(s) for s in path.split("/") if s]
    if sub in _TT_SHORT_SUBDOMAINS:
        if not segs:
            return _fail(original, "unsupported_path", "tiktok")
        return _tt_share(original, f"{sub}.tiktok.com", segs[0])
    if sub not in _TT_SUBDOMAINS or not segs:
        return _fail(original, "unsupported_path", "tiktok")

    first = segs[0].lower()
    if first.startswith("@"):
        user = segs[0][1:]
        if len(segs) < 2 or segs[1].lower() != "video" or not _TT_USER_RE.match(user):
            return _fail(original, "unsupported_path", "tiktok")
        return _tt_video(original, user, segs[2] if len(segs) >= 3 else "")
    if first == "video":
        return _tt_video(original, "", segs[1] if len(segs) >= 2 else "")
    if first == "embed":
        if len(segs) >= 2 and segs[1].lower() == "v2":
            return _tt_video(original, "", segs[2] if len(segs) >= 3 else "")
        return _tt_video(original, "", segs[1] if len(segs) >= 2 else "")
    if first == "v":
        code = segs[1] if len(segs) >= 2 else ""
        if code.lower().endswith(".html"):
            code = code[: -len(".html")]
        return _tt_video(original, "", code)
    if first == "t":
        return _tt_share(original, "www.tiktok.com", segs[1] if len(segs) >= 2 else "", "/t")
    return _fail(original, "unsupported_path", "tiktok")


def _tw_post(original: str, user: str | None, code: str) -> LinkResult:
    if not _TW_ID_RE.match(code):
        return _fail(original, "no_shortcode", "twitter")
    return LinkResult(original, f"https://x.com/{user or 'i'}/status/{code}", code, "twitter", "post", None)


def _clean_twitter(original: str, base: str, sub: str, path: str) -> LinkResult:
    if base == "t.co" or sub not in _TW_SUBDOMAINS:
        return _fail(original, "unsupported_path", "twitter")
    segs = [unquote(s) for s in path.split("/") if s]
    if not segs:
        return _fail(original, "unsupported_path", "twitter")

    first = segs[0].lower()
    if first == "i":
        rest = segs[1:]
        if rest and rest[0].lower() == "web":
            rest = rest[1:]
        if rest and rest[0].lower() == "status":
            return _tw_post(original, None, rest[1] if len(rest) >= 2 else "")
        return _fail(original, "unsupported_path", "twitter")
    if len(segs) >= 2 and segs[1].lower() in _TW_STATUS_SEGMENTS:
        if not _TW_USER_RE.match(segs[0]):
            return _fail(original, "unsupported_path", "twitter")
        return _tw_post(original, segs[0], segs[2] if len(segs) >= 3 else "")
    if first == "statuses":
        return _tw_post(original, None, segs[1] if len(segs) >= 2 else "")
    return _fail(original, "unsupported_path", "twitter")


def clean_link(raw: str) -> LinkResult:
    """Clean one URL-like string. ``original`` is ``raw`` unchanged."""
    return _clean(raw, raw, 0)


def clean_text(text: str, platform: str | None = None) -> dict:
    """Extract and clean every link in ``text``; returns the CleanResult object of BRIDGE_API.md.

    ``platform`` None accepts links of every platform; otherwise valid links of the other platforms come back with
    error ``other_platform`` (counted as invalid, not in ``clean_text``).
    """
    if platform is not None and platform not in PLATFORMS:
        raise ValueError(f"Unknown platform: {platform!r}")
    items: list[dict] = []
    seen: set[str] = set()
    valid = invalid = duplicates = 0
    for cand in extract_candidates(text or ""):
        res = clean_link(cand)
        key = res.clean if res.clean else "\0" + res.original
        if key in seen:
            duplicates += 1
            continue
        seen.add(key)
        item = res.to_dict()
        if res.clean and platform is not None and res.platform != platform:
            item["error"] = "other_platform"
        items.append(item)
        if res.clean and item["error"] is None:
            valid += 1
        else:
            invalid += 1
    return {
        "items": items,
        "clean_text": "\n".join(i["clean"] for i in items if i["clean"] and i["error"] is None),
        "valid": valid,
        "invalid": invalid,
        "duplicates": duplicates,
    }


def main() -> int:
    data = sys.stdin.buffer.read().decode("utf-8", "replace")
    out = clean_text(data)["clean_text"]
    sys.stdout.buffer.write((out + "\n" if out else "").encode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
