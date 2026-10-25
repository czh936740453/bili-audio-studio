"""ffmpeg 封装：探测、归一化、拼接、章节与 cue 导出。"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, Iterable, Sequence

from .config import TMP_DIR, VENDOR_DIR
from .net import CancelledError

CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0
_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d\d):(\d\d(?:\.\d+)?)")
_TIME_RE = re.compile(r"time=\s*(\d+):(\d\d):(\d\d(?:\.\d+)?)")


class FFmpegError(RuntimeError):
    pass


def find_ffmpeg() -> str | None:
    env = os.environ.get("FFMPEG_BINARY", "").strip()
    if env and Path(env).is_file():
        return env
    found = shutil.which("ffmpeg")
    if found:
        return found
    vendor = VENDOR_DIR / "ffmpeg" / "ffmpeg.exe"
    if vendor.is_file():
        return str(vendor)
    try:  # 可选：若用户自己装了 imageio-ffmpeg
        import imageio_ffmpeg  # type: ignore

        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe and Path(exe).is_file():
            return exe
    except Exception:  # noqa: BLE001
        pass
    return None


def find_ffprobe() -> str | None:
    found = shutil.which("ffprobe")
    if found:
        return found
    exe = find_ffmpeg()
    if exe:
        candidate = Path(exe).with_name("ffprobe.exe")
        if candidate.is_file():
            return str(candidate)
    vendor = VENDOR_DIR / "ffmpeg" / "ffprobe.exe"
    return str(vendor) if vendor.is_file() else None


def _run(cmd: Sequence[str], cancel: Callable[[], bool] | None = None) -> tuple[int, str, str]:
    proc = subprocess.Popen(
        list(cmd),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=CREATE_NO_WINDOW,
    )
    while True:
        try:
            out, err = proc.communicate(timeout=0.25)
            return proc.returncode, out.decode("utf-8", "replace"), err.decode("utf-8", "replace")
        except subprocess.TimeoutExpired:
            if cancel and cancel():
                proc.kill()
                proc.communicate()
                raise CancelledError("已取消")


def _run_with_progress(
    cmd: Sequence[str],
    cancel: Callable[[], bool] | None,
    total_seconds: float,
    on_progress: Callable[[float], None] | None,
) -> tuple[int, str]:
    """运行 ffmpeg 并解析 -progress 输出；stderr 写临时文件避免管道死锁。"""
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    err_path = TMP_DIR / f"ffmpeg-{os.getpid()}-{int(time.time() * 1000) % 100000}.err"
    with open(err_path, "wb") as err_file:
        proc = subprocess.Popen(
            list(cmd),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=err_file,
            creationflags=CREATE_NO_WINDOW,
        )
        assert proc.stdout is not None
        try:
            for raw in iter(proc.stdout.readline, b""):
                if cancel and cancel():
                    proc.kill()
                    proc.wait()
                    raise CancelledError("已取消")
                line = raw.decode("utf-8", "replace").strip()
                if line.startswith("out_time_us=") and on_progress and total_seconds > 0:
                    try:
                        micros = int(line.split("=", 1)[1])
                    except ValueError:
                        continue
                    on_progress(min(1.0, micros / 1e6 / total_seconds))
            proc.wait()
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
    try:
        err = err_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        err = ""
    err_path.unlink(missing_ok=True)
    if on_progress and proc.returncode == 0:
        on_progress(1.0)
    return proc.returncode, err


def _timecode(match: re.Match) -> float:
    return int(match.group(1)) * 3600 + int(match.group(2)) * 60 + float(match.group(3))


def probe_duration(path: Path, cancel: Callable[[], bool] | None = None) -> float:
    """读取音视频时长（秒）。优先 ffprobe，退化为解析 ffmpeg 输出。"""
    ffprobe = find_ffprobe()
    if ffprobe:
        code, out, err = _run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            cancel,
        )
        text = out.strip()
        if code == 0 and text:
            try:
                return float(text)
            except ValueError:
                pass

    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        raise FFmpegError("找不到 ffmpeg")
    _, out, err = _run([ffmpeg, "-hide_banner", "-i", str(path)], cancel)
    match = _DURATION_RE.search(err or out)
    if match:
        return _timecode(match)
    # 最后手段：完整解码一遍求时长
    _, out2, err2 = _run([ffmpeg, "-hide_banner", "-i", str(path), "-f", "null", "-"], cancel)
    matches = list(_TIME_RE.finditer(err2 or out2))
    if matches:
        return _timecode(matches[-1])
    raise FFmpegError(f"无法读取时长：{path.name}")


def normalize(
    src: Path,
    dest: Path,
    bitrate_kbps: int = 192,
    cancel: Callable[[], bool] | None = None,
    on_progress: Callable[[float], None] | None = None,
    total_seconds: float = 0.0,
) -> Path:
    """统一为 44.1kHz 立体声 AAC，作为拼接素材。"""
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        raise FFmpegError("找不到 ffmpeg，请先运行 python scripts\\fetch_ffmpeg.py")
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".tmp.m4a")
    cmd = [
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-nostats",
        "-progress", "pipe:1",
        "-i", str(src),
        "-vn", "-ac", "2", "-ar", "44100",
        "-c:a", "aac", "-b:a", f"{int(bitrate_kbps)}k",
        "-movflags", "+faststart",
        "-f", "mp4", str(tmp),
    ]
    if total_seconds <= 0:
        try:
            total_seconds = probe_duration(src, cancel)
        except FFmpegError:
            total_seconds = 0.0
    code, err = _run_with_progress(cmd, cancel, total_seconds, on_progress)
    if code != 0 or not tmp.exists():
        tmp.unlink(missing_ok=True)
        raise FFmpegError(f"转码失败：{err.strip()[-400:]}")
    os.replace(tmp, dest)
    return dest


def _ffconcat_quote(path: Path) -> str:
    return str(path).replace("\\", "/").replace("'", r"'\''")


def merge(
    parts: Sequence[tuple[Path, str, float]],
    out_path: Path,
    bitrate_kbps: int = 192,
    title: str = "",
    cancel: Callable[[], bool] | None = None,
    on_progress: Callable[[float], None] | None = None,
    container: str = "m4a",
) -> dict:
    """把多个已归一化音频按顺序拼成一个文件，并写入章节。

    parts: [(文件路径, 曲目标题, 时长秒), ...]
    container: "m4a"（AAC，内置编码器）或 "mp3"（需要 libmp3lame）
    返回 {"duration": 秒, "markers": [{"title","start","duration"}, ...]}
    """
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        raise FFmpegError("找不到 ffmpeg")
    if not parts:
        raise FFmpegError("没有可拼接的曲目")

    container = container.lower()
    if container == "mp3":
        if not has_encoder("libmp3lame"):
            raise FFmpegError("当前 ffmpeg 不支持 MP3 编码（缺少 libmp3lame），请改用 M4A")
        codec_args = ["-c:a", "libmp3lame"]
        format_args = ["-f", "mp3", "-id3v2_version", "3"]
    else:
        container = "m4a"
        codec_args = ["-c:a", "aac"]
        format_args = ["-f", "mp4", "-movflags", "+faststart"]

    TMP_DIR.mkdir(parents=True, exist_ok=True)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    durations: list[float] = []
    markers: list[dict] = []
    cursor = 0.0
    for path, part_title, duration in parts:
        if duration <= 0:
            duration = probe_duration(path, cancel)
        durations.append(duration)
        markers.append({"title": part_title, "start": round(cursor, 3), "duration": round(duration, 3)})
        cursor += duration

    total = cursor
    first_tmp = out_path.with_name(out_path.stem + ".merge.tmp" + out_path.suffix)
    final_tmp = out_path.with_name(out_path.stem + ".final.tmp" + out_path.suffix)

    # 1) 解码后精确拼接（重新编码一次，保证采样级对齐、无累积漂移）
    inputs: list[str] = []
    for path, _t, _d in parts:
        inputs.extend(["-i", str(path)])
    if len(parts) == 1:
        filter_expr = "[0:a]anull[outa]"
    else:
        streams = "".join(f"[{i}:a]" for i in range(len(parts)))
        filter_expr = f"{streams}concat=n={len(parts)}:v=0:a=1[outa]"
    cmd = [
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-nostats",
        "-progress", "pipe:1",
        *inputs,
        "-filter_complex", filter_expr,
        "-map", "[outa]",
        *codec_args, "-b:a", f"{int(bitrate_kbps)}k", "-ar", "44100", "-ac", "2",
        *format_args, str(first_tmp),
    ]
    code, err = _run_with_progress(cmd, cancel, total, on_progress)
    if code != 0 or not first_tmp.exists():
        first_tmp.unlink(missing_ok=True)
        raise FFmpegError(f"拼接失败：{err.strip()[-400:]}")

    # 2) 写入章节（定位符）
    chapter_file = TMP_DIR / f"{out_path.stem}.chapters.txt"
    lines = [";FFMETADATA1"]
    if title:
        lines.append(f"title={_ffmeta_escape(title)}")
    for marker in markers:
        start_ms = int(round(marker["start"] * 1000))
        end_ms = int(round((marker["start"] + marker["duration"]) * 1000))
        lines += [
            "[CHAPTER]",
            "TIMEBASE=1/1000",
            f"START={start_ms}",
            f"END={end_ms}",
            f"title={_ffmeta_escape(marker['title'])}",
        ]
    chapter_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

    cmd2 = [
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(first_tmp),
        "-f", "ffmetadata", "-i", str(chapter_file),
        "-map", "0", "-map_metadata", "1", "-map_chapters", "1",
        "-c", "copy",
        *format_args, str(final_tmp),
    ]
    code2, _out2, err2 = _run(cmd2, cancel)
    first_tmp.unlink(missing_ok=True)
    chapter_file.unlink(missing_ok=True)
    if code2 != 0 or not final_tmp.exists():
        final_tmp.unlink(missing_ok=True)
        raise FFmpegError(f"写入章节失败：{err2.strip()[-400:]}")
    os.replace(final_tmp, out_path)

    actual = probe_duration(out_path, cancel)
    return {"duration": round(actual or total, 3), "markers": markers, "path": str(out_path)}


def _ffmeta_escape(text: str) -> str:
    return (text or "").replace("\\", "\\\\").replace("=", "\\=").replace(";", "\\;").replace("#", "\\#").replace("\n", " ")


def cue_time(seconds: float) -> str:
    """CUE 时间码 mm:ss:ff（75 帧/秒）。"""
    if seconds < 0:
        seconds = 0.0
    total_frames = int(round(seconds * 75))
    minutes, rem = divmod(total_frames, 75 * 60)
    secs, frames = divmod(rem, 75)
    return f"{minutes:02d}:{secs:02d}:{frames:02d}"


def build_cue(album: str, performer: str, audio_filename: str, markers: Sequence[dict]) -> str:
    lines = [
        f'REM GENRE "Bilibili"',
        f'PERFORMER "{performer}"',
        f'TITLE "{album}"',
        f'FILE "{audio_filename}" M4A',
    ]
    for index, marker in enumerate(markers, start=1):
        lines += [
            f"  TRACK {index:02d} AUDIO",
            f'    TITLE "{_cue_quote(marker["title"])}"',
            f'    PERFORMER "{performer}"',
            f"    INDEX 01 {cue_time(float(marker['start']))}",
        ]
    return "\n".join(lines) + "\n"


def _cue_quote(text: str) -> str:
    return (text or "").replace('"', "'").replace("\n", " ")


def build_index(project_name: str, audio_path: Path, duration: float, markers: Sequence[dict],
                tracks: Sequence[dict]) -> str:
    payload = {
        "version": 1,
        "title": project_name,
        "audio": audio_path.name,
        "duration": duration,
        "markers": [
            {
                "index": i,
                "title": m["title"],
                "start": m["start"],
                "duration": m["duration"],
                "start_text": _hms(float(m["start"])),
            }
            for i, m in enumerate(markers)
        ],
        "tracks": list(tracks),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _hms(seconds: float) -> str:
    total = int(round(seconds))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h:d}:{m:02d}:{s:02d}" if h else f"{m:d}:{s:02d}"


_STATUS_CACHE: dict | None = None


def has_encoder(name: str) -> bool:
    exe = find_ffmpeg()
    if not exe:
        return False
    code, out, err = _run([exe, "-hide_banner", "-encoders"])
    return name in (out or err)


def ffmpeg_status(force: bool = False) -> dict:
    """ffmpeg 探测结果（缓存，避免界面轮询时反复启动子进程）。"""
    global _STATUS_CACHE
    if _STATUS_CACHE is not None and not force:
        return _STATUS_CACHE
    exe = find_ffmpeg()
    status = {"ok": bool(exe), "path": exe or "", "version": "", "mp3": False, "ffprobe": bool(find_ffprobe())}
    if exe:
        code, out, err = _run([exe, "-hide_banner", "-version"])
        text = (out or err).splitlines()
        if text:
            status["version"] = text[0].strip()
        code, out, err = _run([exe, "-hide_banner", "-encoders"])
        status["mp3"] = "libmp3lame" in (out or err)
    _STATUS_CACHE = status
    return status
