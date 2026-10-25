"""编排：解析 → 下载 → 归一化 → 拼接 → 侧车文件。"""

from __future__ import annotations

import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from . import audio, bilibili, project as project_mod
from .bilibili import TrackMeta
from .config import CACHE_DIR, NORM_DIR, OUTPUT_DIR, ensure_dirs, safe_filename
from .net import CancelledError, Client


def _client(settings: dict) -> Client:
    client = Client(settings.get("sessdata", ""))
    client.bootstrap()
    return client


def _meta(track: dict) -> TrackMeta:
    return TrackMeta(
        bvid=track.get("bvid", ""),
        aid=int(track.get("aid") or 0),
        cid=int(track.get("cid") or 0),
        title=track.get("title") or "",
        part=track.get("part") or "",
        duration=float(track.get("duration") or 0),
        page=int(track.get("page") or 1),
        owner=track.get("owner") or "",
        source_url=track.get("source_url") or "",
        audio_id=int(track.get("audio_id") or 0),
    )


def _norm_path(track: dict) -> Path:
    return NORM_DIR / f"{track['id']}.m4a"


def _cache_path(track: dict) -> Path | None:
    value = track.get("cache_file")
    return Path(value) if value else None


def _save(project: dict) -> None:
    project_mod.save_project(project)


def _download_one(client: Client, track: dict, settings: dict, job,
                  on_progress=None) -> Path:
    """确保单曲音频在本地。返回缓存文件路径。"""
    existing = _cache_path(track)
    if existing and existing.is_file() and existing.stat().st_size > 1024:
        return existing

    meta = _meta(track)
    bilibili.attach_audio(client, meta, settings)
    if not meta.audio_url:
        raise bilibili.BiliError("没有取到音频地址")

    cache = CACHE_DIR / bilibili.cache_name(meta)
    track.update(
        audio_id=meta.audio_id,
        codecs=meta.codecs,
        quality_label=meta.quality_label,
    )
    if not (cache.is_file() and cache.stat().st_size > 1024):
        urls = [meta.audio_url, *meta.backup_urls]
        last_error: Exception | None = None
        for url in urls:
            try:
                client.download(
                    url,
                    cache,
                    progress=on_progress,
                    cancel=job.check_cancel,
                )
                last_error = None
                break
            except CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - 尝试备用线路
                last_error = exc
                job.log(f"线路失败，换备用地址：{exc}", "warn")
        if last_error is not None:
            raise last_error
    track["cache_file"] = str(cache)
    return cache


def _ensure_norm(track: dict, settings: dict, job, force: bool = False,
                 on_progress=None) -> Path:
    src = _cache_path(track)
    if not src or not src.is_file():
        raise RuntimeError("音频尚未下载")
    dest = _norm_path(track)
    if (not force) and dest.is_file() and dest.stat().st_size > 1024 and dest.stat().st_mtime >= src.stat().st_mtime:
        track["norm_file"] = str(dest)
        return dest
    duration = float(track.get("duration") or 0)
    audio.normalize(
        src,
        dest,
        bitrate_kbps=int(settings.get("bitrate_kbps") or 192),
        cancel=job.check_cancel,
        on_progress=on_progress,
        total_seconds=duration,
    )
    track["norm_file"] = str(dest)
    return dest


def run_download(job, project_id: str, settings: dict) -> dict:
    ensure_dirs()
    project = project_mod.load_project(project_id)
    if not project:
        raise RuntimeError("项目不存在")
    pending = [t for t in project["tracks"] if t.get("status") != "done"]
    if not pending:
        job.log("所有曲目都已下载")
        return {"downloaded": 0}

    client = _client(settings)
    total = len(pending)
    per_track = {t["id"]: 0.0 for t in pending}
    lock = threading.Lock()
    cancelled = threading.Event()

    def update_progress() -> None:
        job.progress = (sum(per_track.values()) / total) * 0.95

    def work(track: dict) -> None:
        if cancelled.is_set():
            return
        name = track.get("part") or track.get("title")
        try:
            job.message = f"下载：{name}"

            def on_progress(written: int, size: int) -> None:
                with lock:
                    per_track[track["id"]] = (written / size) if size else 0.5
                    update_progress()

            _download_one(client, track, settings, job, on_progress)
            with lock:
                track["status"] = "done"
                track["error"] = ""
                per_track[track["id"]] = 1.0
                update_progress()
            job.log(f"完成：{name}（{track.get('quality_label') or ''}）")
        except CancelledError:
            cancelled.set()
            raise
        except Exception as exc:  # noqa: BLE001
            with lock:
                track["status"] = "error"
                track["error"] = str(exc)
                per_track[track["id"]] = 1.0
                update_progress()
            job.log(f"失败：{name} — {exc}", "error")

    workers = max(1, min(int(settings.get("parallel") or 3), 6, total))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(work, t) for t in pending]
        for future in as_completed(futures):
            try:
                future.result()
            except CancelledError:
                cancelled.set()
        _save(project)

    if cancelled.is_set():
        raise CancelledError("已取消")
    failed = [t for t in pending if t.get("status") == "error"]
    job.progress = 1.0
    if failed:
        job.message = f"完成，{len(failed)} 个失败"
    return {"downloaded": total - len(failed), "failed": len(failed)}


def run_build(job, project_id: str, settings: dict, force: bool = False) -> dict:
    ensure_dirs()
    project = project_mod.load_project(project_id)
    if not project:
        raise RuntimeError("项目不存在")
    tracks = [t for t in project["tracks"] if t.get("status") != "error" or t.get("cache_file")]
    if not tracks:
        raise RuntimeError("曲目列表为空，请先添加并下载")
    if not audio.find_ffmpeg():
        raise RuntimeError("找不到 ffmpeg，请先运行 python scripts\\fetch_ffmpeg.py")

    client = _client(settings)
    total = len(tracks)
    parts: list[tuple[Path, str, float]] = []

    # 0 - 0.75：下载 + 转码
    for index, track in enumerate(tracks):
        if job.check_cancel():
            raise CancelledError("已取消")
        name = track.get("part") or track.get("title")
        job.message = f"准备音频：{name}"
        base = index / total * 0.75

        def on_dl(written: int, size: int, _base=base, _index=index) -> None:
            frac = (written / size) if size else 0.5
            job.progress = _base + frac * (0.75 / total) * 0.35

        _download_one(client, track, settings, job, on_dl)
        track["status"] = "done"
        track["error"] = ""

        def on_norm(p: float, _base=base, _index=index) -> None:
            job.progress = _base + (0.75 / total) * (0.35 + 0.65 * max(0.0, min(1.0, p)))

        norm = _ensure_norm(track, settings, job, force=force, on_progress=on_norm)
        duration = audio.probe_duration(norm, job.check_cancel)
        track["duration"] = round(duration, 3)
        parts.append((norm, name, duration))
        job.progress = (index + 1) / total * 0.75
        job.log(f"就绪：{name}（{duration:.1f}s）")
        _save(project)

    # 磁盘空间预估
    total_duration = sum(p[2] for p in parts)
    bitrate = int(settings.get("bitrate_kbps") or 192)
    estimate = total_duration * bitrate * 1000 / 8 * 1.2
    out_dir = Path(settings.get("output_dir") or OUTPUT_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(out_dir).free
    if free < estimate + 50 * 1024 * 1024:
        raise RuntimeError(
            f"磁盘空间不足：需要约 {estimate / 1024 / 1024:.0f}MB，可用 {free / 1024 / 1024:.0f}MB"
        )

    job.message = "拼接中…"
    container = str(settings.get("format") or "m4a").lower()
    if container == "mp3" and not audio.has_encoder("libmp3lame"):
        job.log("当前 ffmpeg 不支持 MP3 编码，已改用 M4A", "warn")
        container = "m4a"
    if container not in ("m4a", "mp3"):
        container = "m4a"
    suffix = ".mp3" if container == "mp3" else ".m4a"
    out_name = safe_filename(project.get("name") or "合辑", fallback="合辑") + suffix
    out_path = out_dir / out_name

    def on_merge(p: float) -> None:
        job.progress = 0.75 + max(0.0, min(1.0, p)) * 0.25

    result = audio.merge(
        parts,
        out_path,
        bitrate_kbps=bitrate,
        title=project.get("name") or "合辑",
        cancel=job.check_cancel,
        on_progress=on_merge,
        container=container,
    )

    markers = result["markers"]
    for index, marker in enumerate(markers):
        if index < len(tracks):
            marker["track_id"] = tracks[index]["id"]
    project_mod.recompute_markers(project)
    project["markers"] = markers

    performer = tracks[0].get("owner") or "Bilibili"
    cue_text = audio.build_cue(
        project.get("name") or "合辑",
        performer,
        out_path.name,
        markers,
    )
    cue_path = out_path.with_suffix(".cue")
    cue_path.write_text(cue_text, encoding="utf-8")

    index_text = audio.build_index(
        project.get("name") or "合辑",
        out_path,
        result["duration"],
        markers,
        [
            {
                "title": t.get("part") or t.get("title"),
                "bvid": t.get("bvid"),
                "cid": t.get("cid"),
                "url": t.get("source_url"),
                "quality": t.get("quality_label"),
                "duration": t.get("duration"),
            }
            for t in tracks
        ],
    )
    index_path = out_path.with_suffix(".index.json")
    index_path.write_text(index_text, encoding="utf-8")

    project["output"] = {
        "path": str(out_path),
        "name": out_path.name,
        "duration": result["duration"],
        "size": out_path.stat().st_size,
        "built_at": time.time(),
        "cue": str(cue_path),
        "index": str(index_path),
    }
    project["build"] = {"state": "done", "message": "合并完成", "updated": time.time()}
    for track in project["tracks"]:
        if track.get("cache_file"):
            track["status"] = "done"
    _save(project)
    job.progress = 1.0
    job.message = "合并完成"
    job.log(f"输出：{out_path}（{result['duration']:.1f}s，{len(markers)} 个定位符）")
    return {
        "path": str(out_path),
        "duration": result["duration"],
        "markers": markers,
        "size": out_path.stat().st_size,
    }
