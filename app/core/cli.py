"""Headless test harness:
`python -m app.core.cli [--home DIR] [--out DIR] [--platform instagram|youtube] [--format mp4|mp3] [--update-only]
<text or links...>`.

Prints every engine event as one line and exits when the update pass is over and the queue is drained.
Exit code: 0 all jobs done, 1 some job failed, 2 engine not usable.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time


def _fmt_event(event: str, payload: dict) -> str:
    if event == "job":
        p = payload
        bits = [f"job {p['id']} {p.get('platform', '?')}/{p.get('format', '?')} {p['status']:<11} "
                f"{p['progress'] * 100:5.1f}% {p['stage_text']}"]
        if p.get("previewed") and p["status"] == "queued":
            dur = p.get("duration_s")
            bits.append(f"preview: {p.get('title')!r} by {p.get('author')!r}"
                        f"{f', {dur:.0f} s' if isinstance(dur, (int, float)) else ''}"
                        f"{', thumb' if p.get('thumbnail') else ''}")
        if p.get("speed_bps"):
            bits.append(f"{p['speed_bps'] / 1048576:.1f} MiB/s")
        if p["status"] == "done":
            media = (f"{p['width']}x{p['height']} {p['fps']}fps {p['vcodec']}/{p['acodec']}" if p.get("vcodec")
                     else f"audio {p['acodec']}")
            bits.append(f"-> {p['filepath']} {media} {(p['filesize'] or 0) / 1048576:.2f} MiB "
                        f"transcoded={p['transcoded']} existed={p['already_existed']} via {p['attempt']}")
        if p.get("error"):
            bits.append(f"ERROR {json.dumps(p['error'], ensure_ascii=False)}")
        return " | ".join(bits)
    if event == "engine":
        comps = " ".join(f"{k}={v.get('version')}({'ok' if v.get('ok') else 'missing'})"
                         for k, v in payload.get("components", {}).items())
        prog = payload.get("progress")
        prog_s = f" {prog * 100:.0f}%" if isinstance(prog, (int, float)) else ""
        return f"engine {payload['stage']}{prog_s}: {payload['message']} [{comps}]"
    return f"{event} {json.dumps(payload, ensure_ascii=False)}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.core.cli", description="Media Saver core harness")
    ap.add_argument("--home", help="runtime data root (sets MEDIA_SAVER_HOME)")
    ap.add_argument("--out", help="output directory for this run (saved to settings)")
    ap.add_argument("--update-only", action="store_true", help="run the engine update pass and exit")
    ap.add_argument("--platform", choices=("instagram", "youtube"),
                    help="accept links of this platform only (default: both)")
    ap.add_argument("--format", choices=("mp4", "mp3"), dest="fmt",
                    help="output format of the queued links (default: the format setting)")
    ap.add_argument("--force-update", action="store_true", help="force the update pass")
    ap.add_argument("--set", action="append", default=[], metavar="KEY=JSON",
                    help='settings patch, e.g. --set quality_youtube=720 --set h264_compat=false')
    ap.add_argument("--timeout", type=float, default=1800.0)
    ap.add_argument("--json", action="store_true", help="print raw JSON events")
    ap.add_argument("links", nargs="*")
    args = ap.parse_args(argv)

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass
    if args.home:
        os.environ["MEDIA_SAVER_HOME"] = os.path.abspath(args.home)

    from .engine import Engine

    print_lock = threading.Lock()
    last_job_line: dict[str, str] = {}

    def emit(event: str, payload: dict) -> None:
        line = json.dumps({"type": event, "payload": payload}, ensure_ascii=False) if args.json else _fmt_event(event, payload)
        if event == "job" and not args.json:
            if last_job_line.get(payload["id"]) == line:
                return
            last_job_line[payload["id"]] = line
        with print_lock:
            print(f"{time.strftime('%H:%M:%S')} {line}", flush=True)

    engine = Engine(emit, "cli", open_folder=lambda p: None, app_updates=False)
    patch: dict = {}
    if args.out:
        patch["output_dir"] = os.path.abspath(args.out)
    for item in args.set:
        key, _, raw = item.partition("=")
        try:
            patch[key] = json.loads(raw)
        except ValueError:
            patch[key] = raw
    if patch:
        engine.set_settings(patch)
    if args.update_only:
        engine.updater.run(force=args.force_update)
        status = engine.updater.status()
        print(_fmt_event("engine", status), flush=True)
        return 0 if status["stage"] in ("ready", "offline") else 2

    engine.start()
    if args.force_update:
        engine.update_engine()
    text = " ".join(args.links)
    added: set[str] = set()
    if text:
        result = engine.enqueue(text, args.platform, args.fmt)
        added = {j["id"] for j in result["added"]}
        for s in result["skipped"]:
            print(f"skipped {s['reason']}: {s['input']}", flush=True)
    try:
        ok = engine.wait_idle(timeout=args.timeout)
    except KeyboardInterrupt:
        ok = False
    engine.shutdown()
    jobs = [j for j in engine.downloader.jobs() if j["id"] in added]
    if not ok and engine.updater.status()["stage"] == "error":
        return 2
    return 0 if all(j["status"] == "done" for j in jobs) else 1


if __name__ == "__main__":
    sys.exit(main())
