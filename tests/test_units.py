"""单元测试：解析、命名、时间码、定位符、Range。

运行： python -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import shutil
import sys
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from biliaudio import audio, bilibili, project as project_mod  # noqa: E402
from biliaudio.config import safe_filename  # noqa: E402
from biliaudio.server import parse_range  # noqa: E402


class TestParseInput(unittest.TestCase):
    def test_bvid_in_url(self):
        ref = bilibili.parse_input("https://www.bilibili.com/video/BV1GJ411x7h7")
        self.assertEqual(ref.kind, "video")
        self.assertEqual(ref.bvid, "BV1GJ411x7h7")
        self.assertEqual(ref.page, 1)

    def test_bvid_with_page(self):
        ref = bilibili.parse_input("https://www.bilibili.com/video/BV1GJ411x7h7?p=3&spm_id_from=x")
        self.assertEqual(ref.page, 3)

    def test_bare_bvid(self):
        ref = bilibili.parse_input("BV1GJ411x7h7")
        self.assertEqual(ref.kind, "video")
        self.assertEqual(ref.bvid, "BV1GJ411x7h7")

    def test_av_number(self):
        ref = bilibili.parse_input("https://www.bilibili.com/video/av170001")
        self.assertEqual(ref.kind, "video")
        self.assertEqual(ref.aid, 170001)

    def test_short_link(self):
        ref = bilibili.parse_input("https://b23.tv/abcd1234")
        self.assertEqual(ref.kind, "short")

    def test_bangumi(self):
        ref = bilibili.parse_input("https://www.bilibili.com/bangumi/play/ep123456")
        self.assertEqual(ref.kind, "bangumi")
        self.assertEqual(ref.ep_id, 123456)

    def test_garbage(self):
        self.assertEqual(bilibili.parse_input("hello world").kind, "unknown")
        self.assertEqual(bilibili.parse_input("").kind, "unknown")

    def test_cache_name(self):
        meta = bilibili.TrackMeta(bvid="BV1xx411c7mD", aid=1, cid=2, title="t", part="p",
                                  duration=1.0, audio_id=30280)
        self.assertEqual(bilibili.cache_name(meta), "BV1xx411c7mD_2_30280.m4s")


class TestTimecode(unittest.TestCase):
    def test_cue_time(self):
        self.assertEqual(audio.cue_time(0), "00:00:00")
        self.assertEqual(audio.cue_time(60), "01:00:00")
        self.assertEqual(audio.cue_time(207 + 14 / 75), "03:27:14")
        self.assertEqual(audio.cue_time(3661.5), "61:01:37")

    def test_cue_time_negative(self):
        self.assertEqual(audio.cue_time(-5), "00:00:00")

    def test_build_cue(self):
        markers = [
            {"title": 'A "quoted" song', "start": 0.0, "duration": 60.0},
            {"title": "第二首", "start": 60.0, "duration": 90.0},
        ]
        text = audio.build_cue("合辑", "UP主", "out.m4a", markers)
        self.assertIn('FILE "out.m4a" M4A', text)
        self.assertIn("TRACK 01 AUDIO", text)
        self.assertIn("TRACK 02 AUDIO", text)
        self.assertIn('TITLE "A \'quoted\' song"', text)
        self.assertIn("INDEX 01 01:00:00", text)

    def test_build_index(self):
        markers = [{"title": "x", "start": 0.0, "duration": 5.0}]
        payload = json.loads(audio.build_index("专辑", Path("out.m4a"), 5.0, markers, []))
        self.assertEqual(payload["title"], "专辑")
        self.assertEqual(payload["markers"][0]["start_text"], "0:00")

    def test_ffmeta_escape(self):
        self.assertEqual(audio._ffmeta_escape("a=b;c#d"), r"a\=b\;c\#d")


class TestSafeFilename(unittest.TestCase):
    def test_invalid_chars(self):
        self.assertEqual(safe_filename('a/b\\c:d*e?f"g<h>i|j'), "a_b_c_d_e_f_g_h_i_j")

    def test_reserved(self):
        self.assertTrue(safe_filename("CON").startswith("_"))
        self.assertTrue(safe_filename("lpt1.txt").startswith("_"))

    def test_long(self):
        self.assertLessEqual(len(safe_filename("长" * 200)), 80)

    def test_empty(self):
        self.assertEqual(safe_filename("   "), "audio")
        self.assertEqual(safe_filename("", "合辑"), "合辑")


class TestRange(unittest.TestCase):
    def test_normal(self):
        self.assertEqual(parse_range("bytes=0-99", 1000), (0, 99))

    def test_open_end(self):
        self.assertEqual(parse_range("bytes=900-", 1000), (900, 999))

    def test_suffix(self):
        self.assertEqual(parse_range("bytes=-100", 1000), (900, 999))
        self.assertEqual(parse_range("bytes=-5000", 1000), (0, 999))

    def test_clamped(self):
        self.assertEqual(parse_range("bytes=100-5000", 1000), (100, 999))

    def test_invalid(self):
        for header in ("bytes=1000-", "bytes=500-100", "bytes=-", "bytes=", "items=0-5", "bytes=abc-def"):
            with self.assertRaises(ValueError, msg=header):
                parse_range(header, 1000)


class TestProject(unittest.TestCase):
    def setUp(self):
        base = ROOT / "workspace" / ".tmp"
        self.tmp = base / f"bst-test-{uuid.uuid4().hex[:8]}"
        self.tmp.mkdir(parents=True, exist_ok=True)
        self._old_dir = project_mod.PROJECT_DIR
        self._old_current = project_mod.CURRENT_FILE
        project_mod.PROJECT_DIR = self.tmp
        project_mod.CURRENT_FILE = self.tmp / "current.json"

    def tearDown(self):
        project_mod.PROJECT_DIR = self._old_dir
        project_mod.CURRENT_FILE = self._old_current
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _meta(self, cid, duration, title):
        return {"bvid": f"BV{cid}", "aid": cid, "cid": cid, "title": title,
                "part": title, "duration": duration, "page": 1, "source_url": "u"}

    def test_markers_follow_order(self):
        project = project_mod.new_project("测试")
        added = project_mod.add_tracks(project, [
            self._meta(1, 60.0, "A"), self._meta(2, 90.0, "B"), self._meta(3, 30.0, "C"),
        ])
        self.assertEqual(len(added), 3)
        starts = [m["start"] for m in project["markers"]]
        self.assertEqual(starts, [0.0, 60.0, 150.0])
        self.assertEqual(project["total_duration"], 180.0)

        order = [added[2]["id"], added[0]["id"], added[1]["id"]]
        self.assertTrue(project_mod.reorder(project, order))
        self.assertEqual([m["start"] for m in project["markers"]], [0.0, 30.0, 90.0])

    def test_reorder_rejects_mismatch(self):
        project = project_mod.new_project("测试")
        project_mod.add_tracks(project, [self._meta(1, 10.0, "A")])
        self.assertFalse(project_mod.reorder(project, ["not-a-real-id"]))

    def test_remove_track(self):
        project = project_mod.new_project("测试")
        added = project_mod.add_tracks(project, [self._meta(1, 10.0, "A"), self._meta(2, 20.0, "B")])
        self.assertTrue(project_mod.remove_track(project, added[0]["id"]))
        self.assertEqual(project["total_duration"], 20.0)
        self.assertFalse(project_mod.remove_track(project, "missing"))

    def test_duplicate_flag(self):
        project = project_mod.new_project("测试")
        project_mod.add_tracks(project, [self._meta(1, 10.0, "A")])
        added = project_mod.add_tracks(project, [self._meta(1, 10.0, "A")])
        self.assertTrue(added[0]["duplicate"])

    def test_skip_duplicates(self):
        project = project_mod.new_project("测试")
        project_mod.add_tracks(project, [self._meta(1, 10.0, "A")])
        added = project_mod.add_tracks(
            project,
            [self._meta(1, 10.0, "A"), self._meta(2, 20.0, "B")],
            skip_duplicates=True,
        )
        self.assertEqual(len(added), 1)
        self.assertEqual(len(project["tracks"]), 2)
        self.assertEqual(project["total_duration"], 30.0)

    def test_persistence_roundtrip(self):
        project = project_mod.new_project("保存测试")
        project_mod.add_tracks(project, [self._meta(9, 12.5, "Z")])
        loaded = project_mod.load_project(project["id"])
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded["name"], "保存测试")
        self.assertEqual(len(loaded["tracks"]), 1)
        self.assertEqual(project_mod.get_current(), project["id"])


if __name__ == "__main__":
    unittest.main()
