#!/usr/bin/env python
"""Bili Audio Studio 启动入口。

    python app.py                 # 正常启动并打开应用窗口
    python app.py --no-open       # 只起服务，不开窗口
    python app.py --port 9000     # 指定端口
"""

from __future__ import annotations

import argparse
import shutil
import socket
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from biliaudio import audio, project as project_mod, server  # noqa: E402
from biliaudio.config import WORKSPACE, ensure_dirs, load_settings  # noqa: E402

PROFILE_DIR = WORKSPACE / "browser-profile"

BROWSERS = [
    Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
    Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
    Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
    Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
]


def find_browser() -> str | None:
    for candidate in BROWSERS:
        if candidate.is_file():
            return str(candidate)
    for name in ("msedge", "chrome"):
        found = shutil.which(name)
        if found:
            return found
    return None


def window_size() -> tuple[int, int]:
    """按屏幕尺寸给一个合适的初始窗口大小（之后随窗口自适应）。"""
    try:
        if sys.platform == "win32":
            import ctypes

            user32 = ctypes.windll.user32
            screen_w = int(user32.GetSystemMetrics(0))
            screen_h = int(user32.GetSystemMetrics(1))
            if screen_w > 0 and screen_h > 0:
                width = max(1024, min(1440, int(screen_w * 0.82)))
                height = max(660, min(940, int(screen_h * 0.84)))
                return width, height
    except Exception:  # noqa: BLE001 - 取不到就用默认值
        pass
    return 1200, 860


def open_window(url: str) -> str:
    """优先用 Chromium 系浏览器的 --app 模式，退化到默认浏览器。"""
    browser = find_browser()
    if browser:
        PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        width, height = window_size()
        try:
            subprocess.Popen(
                [
                    browser,
                    f"--app={url}",
                    f"--window-size={width},{height}",
                    f"--user-data-dir={PROFILE_DIR}",
                    "--no-first-run",
                    "--no-default-browser-check",
                    "--disable-features=Translate,MediaRouter",
                ],
                creationflags=0x08000000 if sys.platform == "win32" else 0,
                close_fds=True,
            )
            return "应用窗口"
        except OSError:
            pass
    webbrowser.open(url)
    return "浏览器标签页"


def wait_port(host: str, port: int, timeout: float = 5.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.2)
            if sock.connect_ex((host, port)) == 0:
                return True
        time.sleep(0.1)
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description="B 站音频合辑工作台")
    parser.add_argument("--port", type=int, default=0, help="监听端口（默认自动）")
    parser.add_argument("--no-open", action="store_true", help="不打开界面窗口")
    args = parser.parse_args()

    ensure_dirs()
    settings = load_settings()
    project_mod.open_or_create()

    status = audio.ffmpeg_status()
    if not status["ok"]:
        print("⚠ 未找到 ffmpeg。正在自动下载（约 110MB，只需一次）…")
        code = subprocess.call([sys.executable, str(ROOT / "scripts" / "fetch_ffmpeg.py")])
        status = audio.ffmpeg_status() if code == 0 else status
        if not status["ok"]:
            print("✗ ffmpeg 不可用。请手动运行： python scripts\\fetch_ffmpeg.py")
            print("  （没有 ffmpeg 无法转码与合并，音频解析仍可进行）")

    httpd, port = server.create_server(settings, args.port or None)
    url = f"http://127.0.0.1:{port}/?t={settings.get('token', '')}"
    print("=" * 62)
    print("  Bili Audio Studio  ·  B 站纯音频合辑工作台")
    print(f"  界面地址：http://127.0.0.1:{port}/")
    print(f"  ffmpeg  ：{status.get('path') or '未安装'}")
    print("  关闭此窗口或按 Ctrl+C 退出")
    print("=" * 62)

    if not args.no_open and wait_port("127.0.0.1", port):
        print(f"  已用{open_window(url)}打开界面")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n正在退出…")
    finally:
        httpd.shutdown()
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
