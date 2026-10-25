#!/usr/bin/env python
"""下载并安装 ffmpeg（Windows 静态构建），无需 pip。

用法：
    python scripts\\fetch_ffmpeg.py

行为：
    1. 依次尝试若干个静态构建下载地址；
    2. 解压其中的 bin/ffmpeg.exe（以及 ffprobe.exe / ffplay.exe，若存在）到 vendor/ffmpeg/；
    3. 打印最终路径。

只依赖 Python 标准库。
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import threading
import time
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENDOR = ROOT / "vendor" / "ffmpeg"
# 优先 GitHub 的 BtbN 构建（实测下载速度最快），备选 gyan.dev 精简版
SOURCES = [
    "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-n9.0-latest-win64-gpl-shared-9.0.zip",
    "https://github.com/BtbN/FFmpeg-Builds/releases/latest/download/ffmpeg-master-latest-win64-gpl-shared.zip",
    "https://github.com/BtbN/FFmpeg-Builds/releases/latest/download/ffmpeg-master-latest-win64-gpl.zip",
    "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip",
]
WANTED = ("ffmpeg.exe", "ffprobe.exe", "ffplay.exe")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"


def _human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}GB"


def download(url: str, dest: Path, connections: int = 6) -> None:
    """多线程分段下载（GitHub CDN 单连接会被限速）。"""
    meta = urllib.request.Request(url, headers={"User-Agent": UA}, method="HEAD")
    size = 0
    accepts_ranges = False
    try:
        with urllib.request.urlopen(meta, timeout=30) as resp:
            size = int(resp.headers.get("Content-Length") or 0)
            accepts_ranges = (resp.headers.get("Accept-Ranges") or "").lower() == "bytes"
    except Exception:  # noqa: BLE001 - 退化为单线程
        pass

    if size and accepts_ranges and connections > 1:
        _download_parallel(url, dest, size, connections)
        return

    req = urllib.request.Request(url, headers={"User-Agent": UA})
    start = time.time()
    with urllib.request.urlopen(req, timeout=60) as resp, open(dest, "wb") as fh:
        total = int(resp.headers.get("Content-Length") or 0)
        done = 0
        last = 0.0
        while True:
            chunk = resp.read(1 << 20)
            if not chunk:
                break
            fh.write(chunk)
            done += len(chunk)
            now = time.time()
            if now - last > 1.0:
                last = now
                pct = f" {done * 100 // total:3d}%" if total else ""
                speed = _human(done / max(now - start, 0.001)) + "/s"
                print(f"\r  下载中 {_human(done)}{pct}  {speed}   ", end="", flush=True)
    print()


def _download_parallel(url: str, dest: Path, size: int, connections: int) -> None:
    chunk_size = max(4 << 20, size // connections + 1)
    ranges = [(off, min(off + chunk_size, size) - 1) for off in range(0, size, chunk_size)]
    part_files = [dest.with_name(f"{dest.name}.part{i}") for i in range(len(ranges))]
    progress = {"done": 0, "start": time.time()}
    lock = threading.Lock()

    def report() -> None:
        elapsed = max(time.time() - progress["start"], 0.001)
        speed = _human(progress["done"] / elapsed) + "/s"
        pct = progress["done"] * 100 // size if size else 0
        print(f"\r  下载中 {_human(progress['done'])} {pct:3d}%  {speed}   ", end="", flush=True)

    def fetch(index: int, start_byte: int, end_byte: int) -> None:
        path = part_files[index]
        have = path.stat().st_size if path.exists() else 0
        expected = end_byte - start_byte + 1
        if have >= expected:
            with lock:
                progress["done"] += expected
            return
        first = start_byte + have
        for attempt in range(4):
            try:
                req = urllib.request.Request(
                    url,
                    headers={"User-Agent": UA, "Range": f"bytes={first}-{end_byte}"},
                )
                with urllib.request.urlopen(req, timeout=60) as resp, open(path, "ab") as fh:
                    while first <= end_byte:
                        block = resp.read(min(1 << 20, end_byte - first + 1))
                        if not block:
                            break
                        fh.write(block)
                        first += len(block)
                        with lock:
                            progress["done"] += len(block)
                if first > end_byte:
                    return
            except Exception:  # noqa: BLE001
                time.sleep(1 + attempt)
        raise RuntimeError(f"分片 {index} 下载失败")

    print(f"  分段下载（{connections} 线程，{len(ranges)} 段）…")
    threads = [
        threading.Thread(target=fetch, args=(i, s, e), daemon=True)
        for i, (s, e) in enumerate(ranges)
    ]
    for thread in threads:
        thread.start()
    last_print = 0.0
    while any(t.is_alive() for t in threads):
        time.sleep(0.4)
        now = time.time()
        if now - last_print > 0.6:
            last_print = now
            with lock:
                report()
    for thread in threads:
        thread.join()
    with open(dest, "wb") as out:
        for path in part_files:
            with open(path, "rb") as src:
                shutil.copyfileobj(src, out)
            path.unlink(missing_ok=True)
    print()
    if dest.stat().st_size != size:
        raise RuntimeError(f"下载大小不符：{dest.stat().st_size} != {size}")


def install(zip_path: Path) -> Path | None:
    """从压缩包的 bin/ 目录取出可执行文件与依赖 DLL。"""
    found: dict[str, bytes] = {}
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            norm = info.filename.replace("\\", "/")
            if info.is_dir() or "/bin/" not in norm:
                continue
            name = norm.rsplit("/", 1)[-1]
            if not name or name in found:
                continue
            if name.lower().endswith((".exe", ".dll")):
                found[name] = zf.read(info)
    if "ffmpeg.exe" not in found:
        return None
    VENDOR.mkdir(parents=True, exist_ok=True)
    exe = VENDOR / "ffmpeg.exe"
    for name, data in found.items():
        target = VENDOR / name
        target.write_bytes(data)
        if name.lower() == "ffmpeg.exe":
            exe = target
    return exe


def main() -> int:
    if sys.platform != "win32":
        print("本脚本只处理 Windows 构建；其它平台请用包管理器安装 ffmpeg。")
        return 2
    existing = VENDOR / "ffmpeg.exe"
    if existing.exists() and "--force" not in sys.argv:
        print(f"ffmpeg 已存在：{existing}")
        return 0

    lock = VENDOR / ".download.lock"
    VENDOR.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(fd)
    except FileExistsError:
        age = time.time() - lock.stat().st_mtime if lock.exists() else 0
        if age < 1800:
            print("另一个进程正在下载 ffmpeg，请稍候再试。")
            return 0
        lock.unlink(missing_ok=True)

    try:
        return _download_and_install()
    finally:
        lock.unlink(missing_ok=True)


def _download_and_install() -> int:
    tmp_dir = Path(os.environ.get("TEMP") or tempfile.gettempdir())
    tmp_dir.mkdir(parents=True, exist_ok=True)
    for url in SOURCES:
        archive = tmp_dir / "ffmpeg-build.zip"
        print(f"尝试下载：{url}")
        try:
            download(url, archive)
        except Exception as exc:  # noqa: BLE001 - 换下一个源
            print(f"  失败：{type(exc).__name__}: {exc}")
            continue
        size = archive.stat().st_size
        if size < 20 * 1024 * 1024:
            print(f"  文件过小（{_human(size)}），可能下载出错，换下一个源。")
            continue
        print(f"  解压中（{_human(size)}）…")
        try:
            exe = install(archive)
        except Exception as exc:  # noqa: BLE001
            print(f"  解压失败：{type(exc).__name__}: {exc}")
            continue
        finally:
            try:
                archive.unlink()
            except OSError:
                pass
        if exe:
            print(f"完成：{exe}")
            print("现在可以运行 run.bat 启动程序。")
            return 0
        print("  压缩包内未找到 ffmpeg.exe。")
    print("所有下载源均失败。请手动下载 ffmpeg，解压后把 ffmpeg.exe 放到：")
    print(f"  {VENDOR}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
