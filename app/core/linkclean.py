"""Instagram link cleaning: messy text -> canonical reel/post URLs.

Canonical output (scheme, host, kind, id, nothing after it)::

    https://www.instagram.com/reel/<shortcode>
    https://www.instagram.com/p/<shortcode>

Rules
-----
* Anything after the shortcode is an account referral tail or noise and is dropped: trailing slash, query (igsh,
  utm_source, img_index ...), fragment, /embed, /embed/captioned, /liked_by and so on.
* Shortcode charset ``[A-Za-z0-9_-]``, length 5..64. Its case is preserved (it is case-sensitive); the host is
  case-insensitive and always rewritten to ``www.instagram.com``.
* Accepted input: http/https or no scheme; ``www.`` / ``m.`` / no subdomain; ``instagram.com`` and ``instagr.am``.
  Paths: ``/reel/<sc>``, ``/reels/<sc>``, ``/tv/<sc>``, ``/p/<sc>``, ``/<username>/reel/<sc>``, ``/<username>/p/<sc>``
  (``/<username>/tv/<sc>`` too). ``reel``, ``reels`` and ``tv`` map to kind ``reel`` (``/reel/``); ``p`` stays ``p``.
* ``l.instagram.com/?u=<percent-encoded url>`` is unwrapped and the inner URL is cleaned.
* Errors (``LinkResult.error``):
  - ``not_instagram``: a URL-looking token whose host is not Instagram;
  - ``unsupported_path``: Instagram URL that is not a single reel/post (profile, /stories/, /explore/,
    /reels/audio/<id>, bare /reels/, /share/... redirect tokens that need a network round trip, other subdomains);
  - ``no_shortcode``: /reel/, /reels/, /tv/ or /p/ without a valid shortcode.
* Extraction from free text tolerates surrounding Russian text, emoji, quotes, brackets, angle brackets, markdown
  ``[text](url)``, trailing punctuation, CR/LF/tabs, and links glued together without a separator
  (``...?igsh=abchttps://...``). A token is split at every ``http(s)://`` (except one directly after ``=``, which
  belongs to a ``?u=`` query) and at every scheme-less Instagram host.
* ``clean_text`` de-duplicates by clean URL keeping the first occurrence; invalid items are de-duplicated by their
  original token; ``duplicates`` counts all dropped repeats.

CLI: ``python -m app.core.linkclean`` reads stdin and prints the clean links, one per line.
"""

from __future__ import annotations

import re
import sys
from dataclasses import asdict, dataclass
from urllib.parse import parse_qs, unquote

__all__ = ["LinkResult", "extract_candidates", "clean_link", "clean_text"]

_HOSTS = ("instagram.com", "instagr.am")
_KIND_SEGMENTS = {"reel": "reel", "reels": "reel", "tv": "reel", "p": "p"}
_USER_KIND_SEGMENTS = {"reel": "reel", "tv": "reel", "p": "p"}
_RESERVED_FIRST = {"stories", "explore", "share", "accounts", "direct", "web", "s", "audio", "tags", "locations"}
_SHORTCODE_RE = re.compile(r"[A-Za-z0-9_-]{5,64}\Z")
_MAX_UNWRAP = 3

# Positions where a candidate token may start.
_START_RE = re.compile(
    r"(?<!=)https?://"
    r"|(?<![\w@./:%=-])(?:[a-z0-9-]+\.)?(?:instagram\.com|instagr\.am)(?=[/?#])"
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
    kind: str | None  # "reel" | "p" | None
    error: str | None  # None | "not_instagram" | "unsupported_path" | "no_shortcode"

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


def _fail(original: str, error: str) -> LinkResult:
    return LinkResult(original, None, None, None, error)


def _clean(original: str, raw: str, depth: int) -> LinkResult:
    rest = _SCHEME_RE.sub("", raw.strip(), count=1)
    cut = re.search(r"[/?#]", rest)
    authority, tail = (rest[: cut.start()], rest[cut.start():]) if cut else (rest, "")
    host = authority.rsplit("@", 1)[-1].split(":", 1)[0].lower().rstrip(".")
    base = next((h for h in _HOSTS if host == h or host.endswith("." + h)), None)
    if base is None:
        return _fail(original, "not_instagram")
    sub = host[: -len(base)].rstrip(".")

    path, _, query = tail.partition("?")
    path = path.partition("#")[0]
    query = query.partition("#")[0]

    if sub == "l" and base == "instagram.com":
        inner = parse_qs(query).get("u")
        if inner and depth < _MAX_UNWRAP:
            return _clean(original, inner[0], depth + 1)
        return _fail(original, "unsupported_path")
    if sub not in ("", "www", "m"):
        return _fail(original, "unsupported_path")

    segs = [unquote(s) for s in path.split("/") if s]
    if not segs:
        return _fail(original, "unsupported_path")
    first = segs[0].lower()
    if first in _KIND_SEGMENTS:
        kind, sc_segs = _KIND_SEGMENTS[first], segs[1:]
        if first == "reels" and sc_segs and sc_segs[0].lower() == "audio":
            return _fail(original, "unsupported_path")
        if first == "reels" and not sc_segs:
            return _fail(original, "unsupported_path")
    elif first not in _RESERVED_FIRST and len(segs) >= 2 and segs[1].lower() in _USER_KIND_SEGMENTS:
        kind, sc_segs = _USER_KIND_SEGMENTS[segs[1].lower()], segs[2:]
    else:
        return _fail(original, "unsupported_path")

    code = sc_segs[0] if sc_segs else ""
    if not _SHORTCODE_RE.match(code):
        return _fail(original, "no_shortcode")
    return LinkResult(original, f"https://www.instagram.com/{kind if kind == 'p' else 'reel'}/{code}", code, kind, None)


def clean_link(raw: str) -> LinkResult:
    """Clean one URL-like string. ``original`` is ``raw`` unchanged."""
    return _clean(raw, raw, 0)


def clean_text(text: str) -> dict:
    """Extract and clean every link in ``text``; returns the CleanResult object of BRIDGE_API.md."""
    items: list[dict] = []
    seen: set[str] = set()
    valid = invalid = duplicates = 0
    for cand in extract_candidates(text):
        res = clean_link(cand)
        key = res.clean if res.clean else "\0" + res.original
        if key in seen:
            duplicates += 1
            continue
        seen.add(key)
        items.append(res.to_dict())
        if res.clean:
            valid += 1
        else:
            invalid += 1
    return {
        "items": items,
        "clean_text": "\n".join(i["clean"] for i in items if i["clean"]),
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
