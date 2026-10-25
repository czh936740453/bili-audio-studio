"""基于标准库 urllib 的 HTTP 客户端：Cookie、重试、断点续传、可取消。"""

from __future__ import annotations

import http.cookiejar
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
REFERER = "https://www.bilibili.com/"


class NetworkError(RuntimeError):
    pass


class CancelledError(RuntimeError):
    pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401
        return None


def resolve_redirect(url: str, max_hops: int = 5) -> str:
    """解析 b23.tv 之类的短链，返回最终 URL。"""
    current = url
    for _ in range(max_hops):
        req = urllib.request.Request(current, headers={"User-Agent": UA}, method="GET")
        opener = urllib.request.build_opener(_NoRedirect)
        try:
            with opener.open(req, timeout=15) as resp:
                return resp.geturl()
        except urllib.error.HTTPError as exc:
            location = None
            if exc.headers is not None:
                location = exc.headers.get("Location")
            if exc.code in (301, 302, 303, 307, 308) and location:
                current = urllib.parse.urljoin(current, location)
                continue
            raise NetworkError(f"短链解析失败：HTTP {exc.code}") from exc
        except urllib.error.URLError as exc:
            raise NetworkError(f"短链解析失败：{exc.reason}") from exc
    return current


class Client:
    """带 Cookie 与会话状态的最小 HTTP 客户端。"""

    def __init__(self, sessdata: str = "", referer: str = REFERER) -> None:
        self.referer = referer
        self.cookies = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.cookies)
        )
        self.sessdata = (sessdata or "").strip()
        self._spi_done = False

    # ---------- 内部 ----------
    def _headers(self, extra: dict | None = None) -> dict:
        headers = {
            "User-Agent": UA,
            "Referer": self.referer,
            "Accept": "*/*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Origin": "https://www.bilibili.com",
        }
        if self.sessdata:
            headers["Cookie"] = f"SESSDATA={self.sessdata}"
        if extra:
            headers.update(extra)
        return headers

    def _open(self, url: str, extra: dict | None = None, method: str = "GET", timeout: int = 20):
        req = urllib.request.Request(url, headers=self._headers(extra), method=method)
        return self.opener.open(req, timeout=timeout)

    # ---------- 对外 ----------
    def bootstrap(self) -> None:
        """领取 buvid 指纹 Cookie，降低接口风控概率。"""
        if self._spi_done:
            return
        self._spi_done = True
        try:
            self.get_json("https://api.bilibili.com/x/frontend/finger/spi", retries=1)
        except Exception:  # noqa: BLE001 - 非必需
            pass

    def get_json(self, url: str, retries: int = 3, extra_headers: dict | None = None) -> dict:
        last: Exception | None = None
        for attempt in range(retries):
            try:
                with self._open(url, extra_headers) as resp:
                    raw = resp.read()
                return json.loads(raw.decode("utf-8", "replace"))
            except urllib.error.HTTPError as exc:
                last = exc
                if exc.code in (403, 412) and attempt == 0:
                    self.bootstrap()
                    continue
                if exc.code < 500 and exc.code not in (412, 429):
                    raise NetworkError(f"HTTP {exc.code}：{url}") from exc
            except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
                last = exc
            time.sleep(min(2 ** attempt, 4))
        raise NetworkError(f"请求失败：{url}（{last}）")

    def get_text(self, url: str, retries: int = 2) -> str:
        last: Exception | None = None
        for attempt in range(retries):
            try:
                with self._open(url) as resp:
                    return resp.read(2 << 20).decode("utf-8", "replace")
            except (urllib.error.URLError, urllib.error.HTTPError, OSError, TimeoutError) as exc:
                last = exc
                time.sleep(min(2 ** attempt, 4))
        raise NetworkError(f"请求失败：{url}（{last}）")

    def download(
        self,
        url: str,
        dest: Path,
        progress: Callable[[int, int], None] | None = None,
        cancel: Callable[[], bool] | None = None,
        retries: int = 3,
        chunk: int = 1 << 18,
    ) -> Path:
        """下载到 dest，支持 .part 断点续传。"""
        dest.parent.mkdir(parents=True, exist_ok=True)
        part = dest.with_suffix(dest.suffix + ".part")
        total = 0
        for attempt in range(retries):
            offset = part.stat().st_size if part.exists() else 0
            headers = {"Range": f"bytes={offset}-"} if offset else {}
            try:
                with self._open(url, headers) as resp:
                    if offset and resp.status == 200:
                        offset = 0  # 服务器忽略 Range，重新开始
                    length = int(resp.headers.get("Content-Length") or 0)
                    total = offset + length if length else 0
                    mode = "ab" if offset else "wb"
                    with open(part, mode) as fh:
                        written = offset
                        while True:
                            if cancel and cancel():
                                raise CancelledError("已取消")
                            block = resp.read(chunk)
                            if not block:
                                break
                            fh.write(block)
                            written += len(block)
                            if progress:
                                progress(written, total)
                if total and part.stat().st_size < total and attempt < retries - 1:
                    raise NetworkError("下载不完整，继续重试")
                part.replace(dest)
                return dest
            except CancelledError:
                raise
            except (urllib.error.URLError, urllib.error.HTTPError, OSError, NetworkError) as exc:
                if isinstance(exc, urllib.error.HTTPError) and exc.code in (403, 404, 410):
                    raise NetworkError(f"下载被拒绝：HTTP {exc.code}") from exc
                if attempt == retries - 1:
                    raise NetworkError(f"下载失败：{exc}") from exc
                time.sleep(min(2 ** attempt, 5))
        raise NetworkError("下载失败")
