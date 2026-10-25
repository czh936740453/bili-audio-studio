"""路径、设置与通用工具。"""

from __future__ import annotations

import json
import os
import re
import secrets
import tempfile
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parent.parent
WORKSPACE = APP_ROOT / "workspace"
CACHE_DIR = WORKSPACE / "cache"
NORM_DIR = CACHE_DIR / "norm"
OUTPUT_DIR = WORKSPACE / "output"
PROJECT_DIR = WORKSPACE / "projects"
TMP_DIR = WORKSPACE / ".tmp"
VENDOR_DIR = APP_ROOT / "vendor"
SETTINGS_FILE = WORKSPACE / "settings.json"
WEB_DIR = Path(__file__).resolve().parent / "web"

AUDIO_QUALITY_ORDER = (30280, 30232, 30216)

DEFAULT_SETTINGS: dict = {
    "sessdata": "",
    "audio_id": 0,          # 0 = 自动挑选最高可用音质
    "bitrate_kbps": 192,    # 归一化输出码率
    "format": "m4a",        # 产物容器：m4a（默认）或 mp3
    "parallel": 3,          # 并发下载数
    "output_dir": "",       # 空 = workspace/output
    "host": "127.0.0.1",
    "port": 8787,
    "token": "",
}


def ensure_dirs() -> None:
    for d in (WORKSPACE, CACHE_DIR, NORM_DIR, OUTPUT_DIR, PROJECT_DIR, TMP_DIR):
        d.mkdir(parents=True, exist_ok=True)


def load_settings() -> dict:
    ensure_dirs()
    data = dict(DEFAULT_SETTINGS)
    try:
        raw = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            data.update({k: v for k, v in raw.items() if k in DEFAULT_SETTINGS})
    except (OSError, ValueError):
        pass
    if not data.get("token"):
        data["token"] = secrets.token_urlsafe(16)
        save_settings(data)
    return data


def save_settings(settings: dict) -> None:
    ensure_dirs()
    merged = dict(DEFAULT_SETTINGS)
    merged.update({k: v for k, v in settings.items() if k in DEFAULT_SETTINGS})
    atomic_write_text(SETTINGS_FILE, json.dumps(merged, ensure_ascii=False, indent=2))


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f".tmp-{os.getpid()}-{secrets.token_hex(4)}.part"
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


_INVALID_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def safe_filename(name: str, fallback: str = "audio", max_len: int = 80) -> str:
    """清洗 Windows 非法字符、保留名与超长名。"""
    cleaned = _INVALID_NAME.sub("_", (name or "").strip())
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    if not cleaned:
        cleaned = fallback
    if cleaned.upper().split(".")[0] in _RESERVED:
        cleaned = f"_{cleaned}"
    if len(cleaned) > max_len:
        cleaned = cleaned[:max_len].rstrip(" .")
    return cleaned or fallback


def human_size(num: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if num < 1024 or unit == "GB":
            return f"{num:.1f}{unit}"
        num /= 1024
    return f"{num:.1f}GB"
