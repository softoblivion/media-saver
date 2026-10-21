"""File helpers shared by the updater and the host app: atomic writes, retried replace, SHA-256, checksum files."""
from __future__ import annotations

import hashlib
import json
import os
import re
import time


def atomic_write_bytes(path: str, data: bytes) -> None:
    """Write via <path>.tmp + os.replace so readers never see a half-written file."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    last_err: OSError | None = None
    for _ in range(5):  # another process (antivirus, indexer) may hold the target briefly
        try:
            os.replace(tmp, path)
            return
        except PermissionError as exc:
            last_err = exc
            time.sleep(0.1)
    try:
        os.remove(tmp)
    except OSError:
        pass
    raise last_err  # type: ignore[misc]


def atomic_write_text(path: str, text: str) -> None:
    atomic_write_bytes(path, text.encode("utf-8"))


def atomic_write_json(path: str, obj) -> None:
    atomic_write_text(path, json.dumps(obj, ensure_ascii=False, indent=1))


def replace_file(src: str, dest: str, attempts: int = 10) -> None:
    """os.replace with retries: a just-finished process or antivirus may still hold the target."""
    last: OSError | None = None
    for _ in range(attempts):
        try:
            os.replace(src, dest)
            return
        except PermissionError as exc:
            last = exc
            time.sleep(0.5)
    raise last  # type: ignore[misc]


def sha256_file(path: str) -> str | None:
    """Lower-case SHA-256 hex of a file; None when it cannot be read."""
    try:
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def parse_sums(text: str) -> dict[str, str]:
    """`<sha256>  <name>` (or `<sha256> *<name>`) lines -> {name: sha}."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        m = re.match(r"^\s*([0-9a-fA-F]{64})\s+\*?(.+?)\s*$", line)
        if m:
            out.setdefault(m.group(2), m.group(1).lower())
    return out
