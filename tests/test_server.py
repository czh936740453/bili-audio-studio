"""HTTP 层集成测试：鉴权、API、Range 音频流（不需要 ffmpeg）。"""

from __future__ import annotations

import http.client
import json
import secrets
import shutil
import sys
import threading
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from biliaudio import config, project as project_mod, server  # noqa: E402

_DEFAULT_TOKEN = object()


class TestHttpServer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        base = ROOT / "workspace" / ".tmp"
        cls.tmp = base / f"bst-http-{uuid.uuid4().hex[:8]}"
        cls.tmp.mkdir(parents=True, exist_ok=True)
        cls.old_dir = project_mod.PROJECT_DIR
        cls.old_current = project_mod.CURRENT_FILE
        project_mod.PROJECT_DIR = cls.tmp / "projects"
        project_mod.CURRENT_FILE = cls.tmp / "projects" / "current.json"
        project_mod.PROJECT_DIR.mkdir(parents=True, exist_ok=True)

        # 测试期间把设置文件也重定向到临时目录，避免把测试 token 写进真实的
        # workspace/settings.json。
        cls.old_settings_file = config.SETTINGS_FILE
        config.SETTINGS_FILE = cls.tmp / "settings.json"
        cls.token = secrets.token_urlsafe(16)

        project = project_mod.new_project("HTTP 测试")
        project_mod.add_tracks(project, [
            {"bvid": "BV1xx411c7mD", "aid": 1, "cid": 11, "title": "曲目一", "part": "曲目一",
             "duration": 60.0, "page": 1, "source_url": "u"},
            {"bvid": "BV1xx411c7mE", "aid": 2, "cid": 22, "title": "曲目二", "part": "曲目二",
             "duration": 90.0, "page": 1, "source_url": "u"},
        ])
        cls.audio_file = cls.tmp / "fake.m4a"
        cls.audio_file.write_bytes(bytes(range(256)) * 40)   # 10240 字节
        project["output"] = {
            "path": str(cls.audio_file),
            "name": "fake.m4a",
            "duration": 150.0,
            "size": cls.audio_file.stat().st_size,
            "built_at": 1700000000.0,
        }
        project_mod.save_project(project)

        cls.settings = {
            "sessdata": "", "audio_id": 0, "bitrate_kbps": 192, "parallel": 3,
            "output_dir": "", "host": "127.0.0.1", "port": 0, "token": cls.token,
        }
        cls.httpd, cls.port = server.create_server(cls.settings, 0)
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        project_mod.PROJECT_DIR = cls.old_dir
        project_mod.CURRENT_FILE = cls.old_current
        config.SETTINGS_FILE = cls.old_settings_file
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def request(self, method, path, body=None, headers=None, token=_DEFAULT_TOKEN):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        head = dict(headers or {})
        if token is _DEFAULT_TOKEN:
            token = self.token
        if token:
            head["X-Token"] = token
        payload = None
        if body is not None:
            payload = json.dumps(body).encode()
            head["Content-Type"] = "application/json"
        conn.request(method, path, body=payload, headers=head)
        resp = conn.getresponse()
        data = resp.read()
        conn.close()
        return resp, data

    def test_index_served(self):
        resp, data = self.request("GET", "/", token=None)
        self.assertEqual(resp.status, 200)
        self.assertIn("Bili Audio Studio".encode(), data)

    def test_static_served(self):
        resp, data = self.request("GET", "/static/app.js", token=None)
        self.assertEqual(resp.status, 200)
        self.assertIn(b"Bili Audio Studio", data)

    def test_auth_required(self):
        resp, _ = self.request("GET", "/api/state", token=None)
        self.assertEqual(resp.status, 403)

    def test_state(self):
        resp, data = self.request("GET", "/api/state")
        self.assertEqual(resp.status, 200)
        payload = json.loads(data)
        self.assertEqual(payload["project"]["name"], "HTTP 测试")
        self.assertEqual(len(payload["project"]["tracks"]), 2)
        self.assertEqual(len(payload["project"]["markers"]), 2)
        self.assertNotIn("token", payload["settings"])
        # 曲目列表带上"能否播放"标记
        for track in payload["project"]["tracks"]:
            self.assertIn("playable", track)
            self.assertIsInstance(track["playable"], bool)

    def test_media_full(self):
        resp, data = self.request("GET", "/media/output")
        self.assertEqual(resp.status, 200)
        self.assertEqual(resp.getheader("Accept-Ranges"), "bytes")
        self.assertEqual(len(data), 10240)

    def test_media_range(self):
        resp, data = self.request("GET", "/media/output", headers={"Range": "bytes=100-199"})
        self.assertEqual(resp.status, 206)
        self.assertEqual(resp.getheader("Content-Range"), "bytes 100-199/10240")
        self.assertEqual(len(data), 100)
        self.assertEqual(data, (bytes(range(256)) * 40)[100:200])

    def test_media_range_suffix(self):
        resp, data = self.request("GET", "/media/output", headers={"Range": "bytes=-64"})
        self.assertEqual(resp.status, 206)
        self.assertEqual(resp.getheader("Content-Range"), "bytes 10176-10239/10240")
        self.assertEqual(len(data), 64)

    def test_media_range_invalid(self):
        resp, _ = self.request("GET", "/media/output", headers={"Range": "bytes=99999-"})
        self.assertEqual(resp.status, 416)

    def test_reorder_api(self):
        payload = json.loads(self.request("GET", "/api/state")[1])
        ids = [t["id"] for t in payload["project"]["tracks"]]
        resp, data = self.request("POST", "/api/tracks/reorder", body={"order": [ids[1], ids[0]]})
        self.assertEqual(resp.status, 200)
        project = json.loads(data)["project"]
        self.assertEqual(project["tracks"][0]["id"], ids[1])
        self.assertEqual([m["start"] for m in project["markers"]], [0.0, 90.0])

    def test_settings_roundtrip(self):
        resp, data = self.request("POST", "/api/settings", body={"bitrate_kbps": 256, "parallel": 4})
        self.assertEqual(resp.status, 200)
        settings = json.loads(data)["settings"]
        self.assertEqual(settings["bitrate_kbps"], 256)
        self.assertEqual(settings["parallel"], 4)
        # 还原，避免影响配置文件
        self.request("POST", "/api/settings", body={"bitrate_kbps": 192, "parallel": 3})

    def test_unknown_api(self):
        resp, _ = self.request("GET", "/api/nope")
        self.assertEqual(resp.status, 404)

    def test_parse_requires_urls(self):
        resp, _ = self.request("POST", "/api/parse", body={"urls": "  "})
        self.assertEqual(resp.status, 400)


if __name__ == "__main__":
    unittest.main()
