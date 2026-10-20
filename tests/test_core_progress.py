import unittest

from app.core.downloader import (DOWNLOAD_TEMPLATE, ProgressModel, parse_ffmpeg_progress, parse_ffprobe_json,
                                 parse_marker_line, stage_weights)

# Real stdout / stderr lines of yt-dlp nightly 2026.10.10 with the templates of downloader.progress_args().
LINE_START = ('[[IDLP]]{"s":"downloading","d":1024,"t":6347229,"te":NA,"sp":NA,"eta":NA,'
              '"f":"dash-1355353572836622v","vc":"vp09.00.40.08.00.01.01.01.00"}')
LINE_DONE = ('[[IDLP]]{"s":"finished","d":6347229,"t":6347229,"te":NA,"sp":88615155.14226845,"eta":NA,'
             '"f":"dash-1355353572836622v","vc":"vp09.00.40.08.00.01.01.01.00"}')
LINE_AUDIO = ('[[IDLP]]{"s":"downloading","d":1024,"t":239411,"te":NA,"sp":NA,"eta":NA,'
              '"f":"dash-24903773259308438a","vc":"none"}')
LINE_PP = '[[IDLPP]]{"s":"started","pp":"Merger"}'
LINE_FILE = '[[IDLFILE]]"C:\\\\Users\\\\x\\\\out\\\\DQCkNLtgqEe.mp4"'


class MarkerTest(unittest.TestCase):
    def test_download_lines(self):
        kind, data = parse_marker_line(LINE_START)
        self.assertEqual(kind, "download")
        self.assertEqual(data["d"], 1024)
        self.assertEqual(data["t"], 6347229)
        self.assertIsNone(data["te"])
        self.assertIsNone(data["sp"])
        kind, data = parse_marker_line(LINE_DONE)
        self.assertEqual(data["s"], "finished")
        self.assertAlmostEqual(data["sp"], 88615155.14, places=1)
        self.assertEqual(parse_marker_line(LINE_AUDIO)[1]["vc"], "none")

    def test_postprocess_and_file(self):
        self.assertEqual(parse_marker_line(LINE_PP), ("postprocess", {"s": "started", "pp": "Merger"}))
        self.assertEqual(parse_marker_line(LINE_FILE), ("file", "C:\\Users\\x\\out\\DQCkNLtgqEe.mp4"))

    def test_other_lines(self):
        for line in ("", "[info] DQCkNLtgqEe: Downloading 1 format(s)", "[[IDLP]]{broken", "WARNING: x"):
            self.assertIsNone(parse_marker_line(line))

    def test_template_contains_marker(self):
        self.assertTrue(DOWNLOAD_TEMPLATE.startswith("download:[[IDLP]]{"))


class ModelTest(unittest.TestCase):
    def test_weights_sum_to_one_and_monotonic(self):
        m = ProgressModel(stage_weights(has_audio=True, transcode=True))
        self.assertAlmostEqual(sum(m.weights.values()), 1.0)
        values = [m.set("probe", 1.0), m.set("video", 0.5), m.set("video", 0.2), m.set("video", 1.0),
                  m.set("audio", 0.5), m.set("merge", 1.0), m.set("transcode", 0.5), m.set("finalize", 1.0)]
        self.assertEqual(values, sorted(values))
        self.assertEqual(values[1], values[2])  # no going back
        self.assertEqual(m.value, 1.0)  # finishing the last stage completes the earlier ones

    def test_later_stage_completes_earlier(self):
        m = ProgressModel(stage_weights(has_audio=False, transcode=False))
        self.assertNotIn("audio", m.weights)
        m.set("finalize", 1.0)
        self.assertEqual(m.value, 1.0)

    def test_unknown_stage_ignored(self):
        m = ProgressModel(stage_weights(False, False))
        self.assertEqual(m.set("transcode", 1.0), 0.0)


class FfmpegTest(unittest.TestCase):
    def test_progress_kv(self):
        self.assertEqual(parse_ffmpeg_progress("out_time_us=12500000"), ("out_time_us", "12500000"))
        self.assertEqual(parse_ffmpeg_progress("progress=end"), ("progress", "end"))
        self.assertIsNone(parse_ffmpeg_progress("frame  100"))

    def test_ffprobe(self):
        text = """{"streams":[{"codec_type":"video","codec_name":"h264","width":1080,"height":1920,
        "avg_frame_rate":"24000/1001","r_frame_rate":"24000/1001"},
        {"codec_type":"audio","codec_name":"aac"}],"format":{"duration":"26.19","size":"26508123"}}"""
        d = parse_ffprobe_json(text)
        self.assertEqual((d["width"], d["height"], d["vcodec"], d["acodec"]), (1080, 1920, "h264", "aac"))
        self.assertEqual(d["fps"], 23.976)
        self.assertEqual(d["filesize"], 26508123)
        d = parse_ffprobe_json('{"streams":[{"codec_type":"video","codec_name":"vp9","avg_frame_rate":"30/1"}]}')
        self.assertEqual(d["fps"], 30)
        self.assertIsNone(d["acodec"])


if __name__ == "__main__":
    unittest.main()
