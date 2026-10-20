"""Instagram session cookies: Netscape cookies.txt writer / reader and auth status (BRIDGE_API.md `Auth`).

The login window of the shell (app/login.py) calls `write_netscape_cookies`; the engine reads the status with
`read_auth_status`. A sidecar `auth.json` next to cookies.txt keeps where the cookies came from
("session" = in-app login, "import" = user file), when they were saved, and whether a download found the
session invalid.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import time
from typing import Any, Iterable

from .paths import atomic_write_json, atomic_write_text

log = logging.getLogger("reels.core.auth")

HEADER = "# Netscape HTTP Cookie File"
HTTPONLY_PREFIX = "#HttpOnly_"
SESSION_COOKIE = "sessionid"
USER_COOKIE = "ds_user_id"
IG_DOMAIN = "instagram.com"
META_NAME = "auth.json"
MAX_IMPORT_BYTES = 2 * 1024 * 1024


def _meta_path(cookies_path: str) -> str:
    return os.path.join(os.path.dirname(os.path.abspath(cookies_path)), META_NAME)


def read_meta(cookies_path: str) -> dict[str, Any]:
    try:
        with open(_meta_path(cookies_path), "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def write_meta(cookies_path: str, **fields: Any) -> None:
    meta = read_meta(cookies_path)
    meta.update(fields)
    atomic_write_json(_meta_path(cookies_path), meta)


def _clean_field(value: Any) -> str:
    # Tabs and newlines would break the line format; Instagram values never contain them.
    return str(value).replace("\t", " ").replace("\r", "").replace("\n", "")


def format_netscape(cookies: Iterable[dict[str, Any]]) -> str:
    lines = [HEADER, "# Written by Reels Saver. Do not share this file: it contains your login.", ""]
    for c in cookies:
        name = _clean_field(c.get("name", ""))
        if not name:
            continue
        domain = _clean_field(c.get("domain") or "")
        if not domain:
            continue
        include_sub = "TRUE" if domain.startswith(".") else "FALSE"
        path = _clean_field(c.get("path") or "/")
        secure = "TRUE" if c.get("secure") else "FALSE"
        expires = c.get("expires")
        try:
            expires_i = int(float(expires)) if expires not in (None, "") else 0
        except (TypeError, ValueError):
            expires_i = 0
        if expires_i < 0:
            expires_i = 0
        value = _clean_field(c.get("value", ""))
        # The HttpOnly flag has no meaning for CLI downloaders; the plain form is read by every parser.
        lines.append("\t".join([domain, include_sub, path, secure, str(expires_i), name, value]))
    return "\n".join(lines) + "\n"


def write_netscape_cookies(cookies: list[dict[str, Any]], path: str, source: str = "session") -> None:
    """Write cookies (dicts: name, value, domain, path, secure, http_only, expires epoch|None) atomically.

    Also records the source and save time in the sidecar meta and clears a previous "invalid" mark.
    """
    atomic_write_text(path, format_netscape(cookies))
    write_meta(path, source=source, saved_at=time.time(), invalid=False)


def parse_netscape(text: str) -> list[dict[str, Any]]:
    """Parse cookies.txt text; skips comments and malformed lines. Accepts the #HttpOnly_ prefix."""
    cookies: list[dict[str, Any]] = []
    for raw in text.splitlines():
        line = raw.strip("\r\n")
        http_only = False
        if line.startswith(HTTPONLY_PREFIX):
            line = line[len(HTTPONLY_PREFIX):]
            http_only = True
        elif not line.strip() or line.lstrip().startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 7:
            continue
        if len(parts) > 7:  # a value with tabs: keep everything after the name
            parts = parts[:6] + ["\t".join(parts[6:])]
        domain, _sub, path, secure, expires, name, value = parts
        try:
            exp = float(expires) if expires.strip() else 0.0
        except ValueError:
            continue
        cookies.append({
            "domain": domain.strip(),
            "path": path or "/",
            "secure": secure.strip().upper() == "TRUE",
            "http_only": http_only,
            "expires": exp if exp > 0 else None,
            "name": name.strip(),
            "value": value,
        })
    return cookies


def _is_ig(domain: str) -> bool:
    d = domain.lstrip(".").lower()
    return d == IG_DOMAIN or d.endswith("." + IG_DOMAIN)


def _session_cookie(cookies: list[dict[str, Any]], now: float) -> dict[str, Any] | None:
    for c in cookies:
        if c["name"] == SESSION_COOKIE and _is_ig(c["domain"]) and c["value"]:
            exp = c.get("expires")
            if exp is None or exp > now:
                return c
    return None


def _user_id(cookies: list[dict[str, Any]]) -> str | None:
    for c in cookies:
        if c["name"] == USER_COOKIE and _is_ig(c["domain"]) and c["value"]:
            return c["value"]
    return None


def logged_out_status() -> dict[str, Any]:
    return {"logged_in": False, "user_id": None, "source": None, "saved_at": None}


def read_auth_status(path: str, now: float | None = None) -> dict[str, Any]:
    """Auth payload for cookies.txt at `path`: logged_in when a non-expired instagram.com sessionid exists."""
    now = time.time() if now is None else now
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            cookies = parse_netscape(fh.read())
    except OSError:
        return logged_out_status()
    meta = read_meta(path)
    session = _session_cookie(cookies, now)
    if session is None or meta.get("invalid"):
        status = logged_out_status()
        return status
    saved_at = meta.get("saved_at")
    if not isinstance(saved_at, (int, float)):
        try:
            saved_at = os.path.getmtime(path)
        except OSError:
            saved_at = None
    source = meta.get("source") if meta.get("source") in ("session", "import") else "session"
    return {"logged_in": True, "user_id": _user_id(cookies), "source": source, "saved_at": saved_at}


def has_session(path: str) -> bool:
    return read_auth_status(path)["logged_in"]


def jar_has_session(path: str, now: float | None = None) -> bool:
    """True when a cookies.txt (e.g. a per-job copy written back by yt-dlp) still holds a live sessionid."""
    now = time.time() if now is None else now
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return _session_cookie(parse_netscape(fh.read()), now) is not None
    except OSError:
        return False


def mark_invalid(path: str) -> None:
    """A download proved the saved session dead (Instagram dropped sessionid)."""
    if os.path.exists(path):
        write_meta(path, invalid=True)


def validate_cookies_text(text: str, now: float | None = None) -> list[dict[str, Any]]:
    """Validate user-supplied cookies.txt content; returns the instagram.com cookies or raises ValueError (ru)."""
    now = time.time() if now is None else now
    if not text.strip():
        raise ValueError("The cookies file is empty")
    cookies = parse_netscape(text)
    if not cookies:
        raise ValueError("This is not a cookies file in Netscape format (cookies.txt)")
    ig = [c for c in cookies if _is_ig(c["domain"])]
    if not ig:
        raise ValueError("The file has no Instagram cookies")
    if not any(c["name"] == SESSION_COOKIE for c in ig):
        raise ValueError("The file has no sessionid cookie: sign in to Instagram in your browser and export the cookies again")
    if _session_cookie(ig, now) is None:
        raise ValueError("The Instagram session in the file has expired: sign in again and re-export the cookies")
    return ig


def import_cookies_file(src: str, dest: str) -> dict[str, Any]:
    """Validate a user cookies.txt and store its instagram.com cookies at `dest`. Returns Auth."""
    try:
        size = os.path.getsize(src)
    except OSError:
        raise ValueError("Cookies file not found") from None
    if size > MAX_IMPORT_BYTES:
        raise ValueError("The file is too large for cookies.txt")
    try:
        with open(src, "r", encoding="utf-8-sig", errors="replace") as fh:
            text = fh.read()
    except OSError as exc:
        raise ValueError(f"Could not read the file: {exc.strerror or exc}") from None
    ig = validate_cookies_text(text)
    write_netscape_cookies(ig, dest, source="import")
    return read_auth_status(dest)


def copy_for_job(src: str, dest: str) -> bool:
    """Per-job copy of the jar (yt-dlp writes the jar back); False when there is no saved jar."""
    if not os.path.exists(src):
        return False
    shutil.copyfile(src, dest)
    return True


def delete_cookies(path: str) -> None:
    for p in (path, _meta_path(path)):
        try:
            os.remove(p)
        except FileNotFoundError:
            pass
