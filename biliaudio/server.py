"""本地 HTTP 服务：REST API + 支持 Range 的音频流。"""

from __future__ import annotations

import json
import mimetypes
import os
import re
import socket
import threading
import time
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import audio, bilibili, jobs, pipeline, project as project_mod
from .config import CACHE_DIR, WEB_DIR, ensure_dirs, load_settings, save_settings

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".m4a": "audio/mp4",
    ".m4s": "audio/mp4",
    ".mp3": "audio/mpeg",
    ".cue": "text/plain; charset=utf-8",
}


def parse_range(header: str, size: int) -> tuple[int, int]:
    """解析 HTTP Range 头，返回闭区间 (start, end)；非法时抛 ValueError。"""
    if size <= 0:
        raise ValueError("empty file")
    match = re.match(r"^bytes=(\d*)-(\d*)$", (header or "").strip())
    if not match:
        raise ValueError("unsupported range")
    first, second = match.group(1), match.group(2)
    if first == "" and second == "":
        raise ValueError("empty range")
    if first == "":
        length = int(second)
        if length <= 0:
            raise ValueError("bad suffix")
        start, end = max(0, size - length), size - 1
    else:
        start = int(first)
        end = min(int(second), size - 1) if second else size - 1
    if start >= size or start > end:
        raise ValueError("out of range")
    return start, end


class Context:
    def __init__(self, settings: dict) -> None:
        self.settings = settings
        self.lock = threading.RLock()

    def project(self) -> dict:
        with self.lock:
            return project_mod.open_or_create()


class Handler(BaseHTTPRequestHandler):
    server_version = "BiliAudioStudio/0.1"
    protocol_version = "HTTP/1.1"
    ctx: Context

    # ---------- 基础工具 ----------
    def log_message(self, fmt: str, *args) -> None:  # noqa: A003 - 静音默认日志
        pass

    def _query(self) -> dict:
        parsed = urllib.parse.urlparse(self.path)
        return {k: v[-1] for k, v in urllib.parse.parse_qs(parsed.query).items()}

    def _path(self) -> str:
        return urllib.parse.urlparse(self.path).path

    def _authorized(self) -> bool:
        token = self.ctx.settings.get("token") or ""
        if not token:
            return True
        provided = self.headers.get("X-Token") or self._query().get("t") or ""
        return provided == token

    def _json(self, payload, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _error(self, message: str, status: int = 400) -> None:
        self._json({"error": message}, status)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
            return data if isinstance(data, dict) else {}
        except ValueError:
            return {}

    def _busy(self) -> str | None:
        for job in jobs.list_jobs():
            if job.state == "running":
                return job.kind
        return None

    def _guard_write(self) -> bool:
        busy = self._busy()
        if busy:
            self._error(f"有任务正在进行（{busy}），请等待完成或取消", 409)
            return False
        return True

    @staticmethod
    def _decorate(project: dict) -> dict:
        """给每个曲目补上 playable 标记，前端据此决定能不能点播放。"""
        for track in project.get("tracks") or []:
            cache = track.get("cache_file") or ""
            norm = track.get("norm_file") or ""
            try:
                playable = bool(cache and Path(cache).is_file()) or bool(
                    norm and Path(norm).is_file()
                )
            except OSError:
                playable = False
            track["playable"] = playable
        return project

    # ---------- 分发 ----------
    def do_GET(self) -> None:  # noqa: N802
        path = self._path()
        if path == "/":
            return self._serve_index()
        if path.startswith("/static/"):
            return self._serve_static(path[len("/static/"):])
        if path in ("/media/output", "/media/track"):
            if not self._authorized():
                return self._error("token 无效", 403)
            return self._serve_media(path)
        if path.startswith("/api/"):
            if not self._authorized():
                return self._error("token 无效", 403)
            return self._api_get(path, self._query())
        self._error("未找到", 404)

    def do_HEAD(self) -> None:  # noqa: N802
        path = self._path()
        if path == "/" or path.startswith("/static/"):
            return self.do_GET()
        if path.startswith("/media/"):
            return self._serve_media(path, head_only=True)
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_POST(self) -> None:  # noqa: N802
        path = self._path()
        if not path.startswith("/api/"):
            return self._error("未找到", 404)
        if not self._authorized():
            return self._error("token 无效", 403)
        return self._api_post(path, self._read_json())

    def do_DELETE(self) -> None:  # noqa: N802
        path = self._path()
        if not path.startswith("/api/"):
            return self._error("未找到", 404)
        if not self._authorized():
            return self._error("token 无效", 403)
        return self._api_delete(path)

    # ---------- 静态 ----------
    def _serve_index(self) -> None:
        index = WEB_DIR / "index.html"
        if not index.is_file():
            return self._error("缺少界面文件", 500)
        body = index.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", CONTENT_TYPES[".html"])
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _serve_static(self, rel: str) -> None:
        target = (WEB_DIR / rel).resolve()
        try:
            target.relative_to(WEB_DIR.resolve())
        except ValueError:
            return self._error("非法路径", 403)
        if not target.is_file():
            return self._error("未找到", 404)
        body = target.read_bytes()
        ctype = CONTENT_TYPES.get(target.suffix.lower()) or mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    # ---------- 媒体（Range） ----------
    def _project_audio(self, path: str) -> Path | None:
        project = self.ctx.project()
        if path == "/media/output":
            output = project.get("output") or {}
            target = Path(output.get("path") or "")
            return target if target.is_file() else None
        track_id = self._query().get("id") or ""
        track = project_mod.find_track(project, track_id)
        if not track:
            return None
        cache = track.get("cache_file")
        if cache and Path(cache).is_file():
            return Path(cache)
        norm = track.get("norm_file")
        if norm and Path(norm).is_file():
            return Path(norm)
        return None

    def _serve_media(self, path: str, head_only: bool = False) -> None:
        target = self._project_audio(path)
        if not target:
            return self._error("文件不存在或尚未生成", 404)
        try:
            size = target.stat().st_size
        except OSError:
            return self._error("文件不可读", 404)
        ctype = CONTENT_TYPES.get(target.suffix.lower(), "audio/mp4")
        start, end = 0, size - 1
        status = HTTPStatus.OK
        range_header = self.headers.get("Range")
        if range_header:
            try:
                start, end = parse_range(range_header, size)
            except ValueError:
                self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            status = HTTPStatus.PARTIAL_CONTENT

        length = end - start + 1
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        if status == HTTPStatus.PARTIAL_CONTENT:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if head_only:
            return
        try:
            with open(target, "rb") as fh:
                fh.seek(start)
                remaining = length
                while remaining > 0:
                    chunk = fh.read(min(1 << 16, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass

    # ---------- API: GET ----------
    def _api_get(self, path: str, query: dict) -> None:
        if path == "/api/state":
            project = self.ctx.project()
            output = project.get("output") or {}
            if output and not Path(output.get("path") or "").is_file():
                project["output"] = None
                project["build"] = {
                    "state": "missing",
                    "message": "音频文件已丢失，请重新合并",
                    "updated": time.time(),
                }
            return self._json(
                {
                    "project": self._decorate(project),
                    "jobs": [j.to_dict(with_logs=False) for j in jobs.list_jobs()[-8:]],
                    "ffmpeg": audio.ffmpeg_status(),
                    "settings": {k: v for k, v in self.ctx.settings.items() if k != "token"},
                }
            )
        if path.startswith("/api/jobs/"):
            job_id = path[len("/api/jobs/"):]
            job = jobs.get(job_id)
            return self._json(job.to_dict()) if job else self._error("任务不存在", 404)
        if path == "/api/jobs":
            return self._json({"jobs": [j.to_dict(with_logs=False) for j in jobs.list_jobs()]})
        if path == "/api/projects":
            return self._json({"projects": project_mod.list_projects(), "current": self.ctx.project()["id"]})
        if path == "/api/settings":
            return self._json({"settings": {k: v for k, v in self.ctx.settings.items() if k != "token"}})
        if path == "/api/output":
            project = self.ctx.project()
            output = project.get("output") or {}
            target = Path(output.get("path") or "")
            if not target.is_file():
                return self._error("尚未生成音频文件", 404)
            return self._json(
                {
                    "output": output,
                    "exists": True,
                    "url": f"/media/output?t={self.ctx.settings.get('token', '')}",
                }
            )
        self._error("未找到", 404)

    # ---------- API: POST ----------
    def _api_post(self, path: str, body: dict) -> None:
        if path == "/api/parse":
            return self._parse(body)
        if path == "/api/tracks":
            if not self._guard_write():
                return None
            project = self.ctx.project()
            items = body.get("items") or []
            if not isinstance(items, list) or not items:
                return self._error("没有要添加的曲目")
            skip = bool(body.get("skip_duplicates"))
            added = project_mod.add_tracks(project, items, skip_duplicates=skip)
            return self._json({
                "project": self._decorate(project),
                "added": len(added),
                "skipped": max(0, len(items) - len(added)) if skip else 0,
            })
        if path == "/api/tracks/reorder":
            if not self._guard_write():
                return None
            project = self.ctx.project()
            order = body.get("order") or []
            ok = project_mod.reorder(project, list(order))
            return self._json({"project": self._decorate(project)}) if ok else self._error("排序参数不匹配")
        if path == "/api/download":
            if not self._guard_write():
                return None
            project = self.ctx.project()
            if not project["tracks"]:
                return self._error("请先添加曲目")
            job = jobs.start("download", "下载音频", pipeline.run_download, project["id"], self.ctx.settings)
            return self._json({"job": job.to_dict(with_logs=False)})
        if path == "/api/build":
            if not self._guard_write():
                return None
            project = self.ctx.project()
            if not project["tracks"]:
                return self._error("请先添加曲目")
            job = jobs.start(
                "build", "下载并合并", pipeline.run_build, project["id"], self.ctx.settings,
                bool(body.get("force")),
            )
            return self._json({"job": job.to_dict(with_logs=False)})
        if path.startswith("/api/jobs/") and path.endswith("/cancel"):
            job_id = path[len("/api/jobs/"):-len("/cancel")]
            ok = jobs.cancel(job_id)
            return self._json({"ok": ok}) if ok else self._error("无法取消", 400)
        if path == "/api/project":
            action = body.get("action") or ""
            if action == "new":
                project = project_mod.new_project(body.get("name") or "未命名合辑")
                return self._json({"project": self._decorate(project)})
            if action == "open":
                project = project_mod.load_project(str(body.get("id") or ""))
                if not project:
                    return self._error("项目不存在", 404)
                project_mod.set_current(project["id"])
                return self._json({"project": self._decorate(project)})
            if action == "rename":
                project = self.ctx.project()
                project["name"] = (body.get("name") or project["name"]).strip()[:60] or project["name"]
                project_mod.save_project(project)
                return self._json({"project": self._decorate(project)})
            return self._error("未知操作")
        if path == "/api/settings":
            for key in ("sessdata", "audio_id", "bitrate_kbps", "parallel", "output_dir", "format"):
                if key in body:
                    self.ctx.settings[key] = body[key]
            save_settings(self.ctx.settings)
            self.ctx.settings = load_settings()
            return self._json({"settings": {k: v for k, v in self.ctx.settings.items() if k != "token"}})
        if path == "/api/reveal":
            project = self.ctx.project()
            output = project.get("output") or {}
            target = Path(output.get("path") or "")
            folder = target.parent if target.is_file() else Path(project_mod.PROJECT_DIR)
            try:
                if hasattr(os, "startfile"):
                    os.startfile(str(folder))  # noqa: S606
                return self._json({"ok": True, "folder": str(folder)})
            except OSError as exc:
                return self._error(f"无法打开文件夹：{exc}")
        self._error("未找到", 404)

    # ---------- API: DELETE ----------
    def _api_delete(self, path: str) -> None:
        if path.startswith("/api/tracks/"):
            if not self._guard_write():
                return None
            track_id = path[len("/api/tracks/"):]
            project = self.ctx.project()
            if not project_mod.remove_track(project, track_id):
                return self._error("曲目不存在", 404)
            return self._json({"project": self._decorate(project)})
        self._error("未找到", 404)

    # ---------- 解析 ----------
    def _parse(self, body: dict) -> None:
        raw = body.get("urls")
        if isinstance(raw, list):
            lines = [str(x) for x in raw]
        else:
            lines = str(raw or "").splitlines()
        lines = [line.strip() for line in lines if line and line.strip()]
        if not lines:
            return self._error("请粘贴至少一个链接")
        if len(lines) > 60:
            return self._error("一次最多解析 60 条链接")

        settings = self.ctx.settings
        client = pipeline._client(settings)
        results = []
        for line in lines:
            try:
                ref = bilibili.parse_input(line)
                metas = bilibili.resolve(client, ref)
                for meta in metas:
                    results.append(
                        {
                            "ok": True,
                            "input": line,
                            "bvid": meta.bvid,
                            "aid": meta.aid,
                            "cid": meta.cid,
                            "page": meta.page,
                            "title": meta.title,
                            "part": meta.part,
                            "owner": meta.owner,
                            "duration": meta.duration,
                            "source_url": meta.source_url,
                            "display": meta.display_title,
                        }
                    )
            except Exception as exc:  # noqa: BLE001 - 单条失败不影响其它
                results.append({"ok": False, "input": line, "error": str(exc)})
        self._json({"items": results})


class LocalHTTPServer(ThreadingHTTPServer):
    """Windows 上独占绑定端口，避免第二个实例"看起来启动成功"却收不到请求。"""

    daemon_threads = True
    allow_reuse_address = os.name != "nt"

    def server_bind(self):
        exclusive = getattr(socket, "SO_EXCLUSIVEADDRUSE", None)
        if exclusive is not None:
            try:
                self.socket.setsockopt(socket.SOL_SOCKET, exclusive, 1)
            except OSError:
                pass
        super().server_bind()


def create_server(settings: dict, port: int | None = None) -> tuple[ThreadingHTTPServer, int]:
    ensure_dirs()
    context = Context(settings)
    handler = type("BoundHandler", (Handler,), {"ctx": context})
    last_error: Exception | None = None
    if port is not None:
        start = int(port)
    else:
        start = int(settings.get("port") or 8787)
    candidates = [0] if start == 0 else list(range(start, start + 20))
    for candidate in candidates:
        try:
            httpd = LocalHTTPServer((settings.get("host", "127.0.0.1"), candidate), handler)
            httpd.daemon_threads = True
            return httpd, int(httpd.server_address[1])
        except OSError as exc:
            last_error = exc
            continue
    raise RuntimeError(f"无法绑定端口 {start}~{start + 19}：{last_error}")
