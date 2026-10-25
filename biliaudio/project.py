"""项目模型：曲目列表、顺序、定位符，以及磁盘持久化。"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

from .config import PROJECT_DIR, atomic_write_text, ensure_dirs

CURRENT_FILE = PROJECT_DIR / "current.json"


def _now() -> float:
    return time.time()


def new_project(name: str = "未命名合辑") -> dict:
    project = {
        "id": uuid.uuid4().hex[:12],
        "name": name,
        "created": _now(),
        "updated": _now(),
        "tracks": [],
        "markers": [],
        "output": None,
        "build": {"state": "idle", "message": "", "updated": 0},
    }
    save_project(project)
    set_current(project["id"])
    return project


def project_path(project_id: str) -> Path:
    return PROJECT_DIR / f"{project_id}.json"


def save_project(project: dict) -> None:
    ensure_dirs()
    project["updated"] = _now()
    atomic_write_text(
        project_path(project["id"]),
        json.dumps(project, ensure_ascii=False, indent=2),
    )


def load_project(project_id: str) -> dict | None:
    path = project_path(project_id)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    data.setdefault("tracks", [])
    data.setdefault("markers", [])
    data.setdefault("output", None)
    return data


def list_projects() -> list[dict]:
    ensure_dirs()
    items = []
    for path in sorted(PROJECT_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        if path.name == "current.json":
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        items.append(
            {
                "id": data.get("id"),
                "name": data.get("name"),
                "tracks": len(data.get("tracks") or []),
                "updated": data.get("updated", 0),
            }
        )
    return items


def set_current(project_id: str) -> None:
    ensure_dirs()
    atomic_write_text(CURRENT_FILE, json.dumps({"id": project_id}))


def get_current() -> str | None:
    try:
        return json.loads(CURRENT_FILE.read_text(encoding="utf-8")).get("id")
    except (OSError, ValueError):
        return None


def open_or_create() -> dict:
    current = get_current()
    if current:
        project = load_project(current)
        if project:
            return project
    projects = list_projects()
    if projects:
        project = load_project(projects[0]["id"])
        if project:
            set_current(project["id"])
            return project
    return new_project()


def add_tracks(project: dict, metas: list[dict], skip_duplicates: bool = False) -> list[dict]:
    """metas: bilibili.TrackMeta 序列化后的字典列表。

    skip_duplicates=True 时，已存在的 bvid+cid 直接跳过（不新增记录）。
    """
    existing = {
        (t.get("bvid"), t.get("cid"))
        for t in project["tracks"]
    }
    added = []
    for meta in metas:
        key = (meta.get("bvid"), meta.get("cid"))
        if skip_duplicates and key in existing:
            continue
        track = {
            "id": uuid.uuid4().hex[:10],
            "bvid": meta.get("bvid", ""),
            "aid": meta.get("aid", 0),
            "cid": meta.get("cid", 0),
            "page": meta.get("page", 1),
            "title": meta.get("title") or "未命名",
            "part": meta.get("part") or "",
            "owner": meta.get("owner") or "",
            "duration": float(meta.get("duration") or 0),
            "audio_id": int(meta.get("audio_id") or 0),
            "codecs": meta.get("codecs") or "",
            "quality_label": meta.get("quality_label") or "",
            "source_url": meta.get("source_url") or "",
            "status": "new",
            "cache_file": "",
            "norm_file": "",
            "error": "",
            "duplicate": key in existing,
        }
        project["tracks"].append(track)
        added.append(track)
        existing.add(key)
    if added:
        recompute_markers(project)
        save_project(project)
    return added


def remove_track(project: dict, track_id: str) -> bool:
    before = len(project["tracks"])
    project["tracks"] = [t for t in project["tracks"] if t.get("id") != track_id]
    if len(project["tracks"]) == before:
        return False
    if project.get("output"):
        project["build"] = {"state": "stale", "message": "曲目已变更，需重新合并", "updated": _now()}
    recompute_markers(project)
    save_project(project)
    return True


def reorder(project: dict, order: list[str]) -> bool:
    index = {t["id"]: t for t in project["tracks"]}
    if set(order) != set(index.keys()):
        return False
    project["tracks"] = [index[i] for i in order]
    if project.get("output"):
        project["build"] = {"state": "stale", "message": "顺序已变更，需重新合并", "updated": _now()}
    recompute_markers(project)
    save_project(project)
    return True


def recompute_markers(project: dict) -> list[dict]:
    markers = []
    cursor = 0.0
    for track in project["tracks"]:
        duration = float(track.get("duration") or 0)
        markers.append(
            {
                "track_id": track["id"],
                "title": track.get("part") or track.get("title") or "未命名",
                "start": round(cursor, 3),
                "duration": round(duration, 3),
            }
        )
        cursor += duration
    project["markers"] = markers
    project["total_duration"] = round(cursor, 3)
    return markers


def find_track(project: dict, track_id: str) -> dict | None:
    return next((t for t in project["tracks"] if t.get("id") == track_id), None)


def track_key(track: dict) -> tuple[str, int]:
    return (str(track.get("bvid") or ""), int(track.get("cid") or 0))
