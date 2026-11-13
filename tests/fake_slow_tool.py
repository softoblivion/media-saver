"""A stand-in for a long yt-dlp or ffmpeg run, started as a real child process by the stall tests.

Usage: python fake_slow_tool.py <mode> <seconds> <out_path>
  lines   print a yt-dlp progress line every 0.1 s for <seconds>, then write <out_path> and print its file mark
  writes  print nothing, append to <out_path> every 0.2 s for <seconds> (a merge or an encode that only writes),
          then print the file mark
  silent  print one progress line, then nothing for <seconds>
  pause   print one progress line, nothing for <seconds>, then progress lines for 0.5 s
"""
import json
import sys
import time

PROGRESS = '[[IDLP]]{"s":"downloading","d":%d,"t":100000,"te":NA,"sp":5000,"eta":9,"f":"299","vc":"avc1"}'


def say(line: str) -> None:
    sys.stdout.write(line + "\n")
    sys.stdout.flush()


def main() -> int:
    mode, seconds, out = sys.argv[1], float(sys.argv[2]), sys.argv[3]
    end = time.monotonic() + seconds
    if mode == "lines":
        n = 0
        while time.monotonic() < end:
            n += 1
            say(PROGRESS % n)
            time.sleep(0.1)
        with open(out, "wb") as fh:
            fh.write(b"MP4" * 100)
    elif mode == "writes":
        while time.monotonic() < end:
            with open(out, "ab") as fh:
                fh.write(b"x" * 64)
            time.sleep(0.2)
    elif mode in ("silent", "pause"):
        say(PROGRESS % 1)
        time.sleep(seconds)
        if mode == "silent":
            return 0
        for n in range(2, 7):
            say(PROGRESS % n)
            time.sleep(0.1)
        return 0
    else:
        return 2
    say("[[IDLFILE]]" + json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
