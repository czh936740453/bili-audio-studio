"""B 站链接解析与纯音频流获取。

设计约束：只读取 playurl 返回的 dash.audio（以及 flac / dolby 音频轨），
任何情况下都不会请求 dash.video 或 durl（视频流）。
"""

from __future__ import annotations

import re
import urllib.parse
from dataclasses import dataclass, field

from .net import NetworkError, resolve_redirect

API = "https://api.bilibili.com"

QUALITY_LABEL = {
    30216: "64k",
    30232: "132k",
    30280: "192k",
    0: "FLAC",
}


class BiliError(RuntimeError):
    pass


@dataclass
class Ref:
    kind: str                     # video / bangumi / short / unknown
    bvid: str = ""
    aid: int = 0
    ep_id: int = 0
    page: int = 1
    raw: str = ""


@dataclass
class TrackMeta:
    bvid: str
    aid: int
    cid: int
    title: str
    part: str
    duration: float               # 秒
    page: int = 1
    owner: str = ""
    source_url: str = ""
    audio_id: int = 0
    codecs: str = ""
    bandwidth: int = 0
    audio_url: str = ""
    quality_label: str = ""
    backup_urls: list[str] = field(default_factory=list)

    @property
    def display_title(self) -> str:
        if self.part and self.part != self.title:
            return f"{self.title} - {self.part}"
        return self.title


_BV_RE = re.compile(r"BV([0-9A-Za-z]{10})")
_AV_RE = re.compile(r"av(\d+)", re.IGNORECASE)
_EP_RE = re.compile(r"ep(\d+)", re.IGNORECASE)
_PAGE_RE = re.compile(r"[?&]p=(\d+)")


def parse_input(text: str) -> Ref:
    """把一行输入解析成 Ref。支持 BV 号、av 号、完整链接、短链、番剧 ep。"""
    raw = (text or "").strip()
    if not raw:
        return Ref(kind="unknown", raw=raw)

    if "b23.tv" in raw or "bili2233.cn" in raw:
        return Ref(kind="short", raw=raw)

    page_match = _PAGE_RE.search(raw)
    page = int(page_match.group(1)) if page_match else 1

    ep_match = _EP_RE.search(raw)
    if ep_match and ("bangumi" in raw or raw.lower().startswith("ep")):
        return Ref(kind="bangumi", ep_id=int(ep_match.group(1)), page=1, raw=raw)

    bv_match = _BV_RE.search(raw)
    if bv_match:
        return Ref(kind="video", bvid="BV" + bv_match.group(1), page=page, raw=raw)

    av_match = _AV_RE.search(raw)
    if av_match and ("video" in raw or raw.lower().startswith("av")):
        return Ref(kind="video", aid=int(av_match.group(1)), page=page, raw=raw)

    return Ref(kind="unknown", raw=raw)


def _check(data: dict, what: str) -> dict:
    code = data.get("code")
    if code not in (0, None):
        message = data.get("message") or "未知错误"
        if code == -404:
            message = "内容不存在或已被删除"
        elif code in (-403, -10403):
            message = "无权访问（可能是地区限制或付费内容）"
        elif code == 62002:
            message = "稿件不可见"
        raise BiliError(f"{what}失败：{message}（code {code}）")
    return data.get("data") or {}


def resolve(client, ref: Ref) -> list[TrackMeta]:
    """把一个输入解析成 1..N 条曲目元数据（多 P 视频会展开）。"""
    if ref.kind == "short":
        final = resolve_redirect(ref.raw)
        if final and final != ref.raw and "b23.tv" not in final:
            return resolve(client, parse_input(final))
        raise BiliError("短链解析失败，请粘贴展开后的完整链接")

    if ref.kind == "bangumi":
        return _resolve_bangumi(client, ref)

    if ref.kind != "video":
        raise BiliError("无法识别的链接，请粘贴 B 站视频链接或 BV 号")

    query = {"bvid": ref.bvid} if ref.bvid else {"aid": ref.aid}
    url = f"{API}/x/web-interface/view?{urllib.parse.urlencode(query)}"
    data = _check(client.get_json(url), "获取视频信息")
    return _expand_view(client, data, ref)


def _expand_view(client, data: dict, ref: Ref) -> list[TrackMeta]:
    bvid = data.get("bvid") or ref.bvid
    aid = int(data.get("aid") or ref.aid or 0)
    title = data.get("title") or "未命名"
    owner = ((data.get("owner") or {}).get("name")) or ""
    pages = data.get("pages") or []
    out: list[TrackMeta] = []
    if not pages:
        pages = [{"cid": data.get("cid"), "page": 1, "part": title, "duration": data.get("duration", 0)}]
    for item in pages:
        cid = int(item.get("cid") or 0)
        if not cid:
            continue
        page_no = int(item.get("page") or 1)
        if ref.page and ref.page != page_no and len(pages) > 1:
            continue
        out.append(
            TrackMeta(
                bvid=bvid,
                aid=aid,
                cid=cid,
                title=title,
                part=item.get("part") or title,
                duration=float(item.get("duration") or 0),
                page=page_no,
                owner=owner,
                source_url=f"https://www.bilibili.com/video/{bvid}?p={page_no}",
            )
        )
    if not out:
        raise BiliError("该视频没有可用的分 P 信息")
    return out


def _resolve_bangumi(client, ref: Ref) -> list[TrackMeta]:
    url = f"{API}/pgc/view/web/season?{urllib.parse.urlencode({'ep_id': ref.ep_id})}"
    data = _check(client.get_json(url), "获取番剧信息")
    episodes = data.get("episodes") or []
    target = next((e for e in episodes if int(e.get("id") or 0) == ref.ep_id), None)
    if target is None:
        raise BiliError("番剧剧集不存在")
    bvid = target.get("bvid") or ""
    cid = int(target.get("cid") or 0)
    if not bvid or not cid:
        raise BiliError("该剧集没有可用音轨")
    duration_ms = int(target.get("duration") or 0)
    return [
        TrackMeta(
            bvid=bvid,
            aid=int(target.get("aid") or 0),
            cid=cid,
            title=data.get("season_title") or data.get("title") or "番剧",
            part=target.get("long_title") or target.get("title") or "",
            duration=duration_ms / 1000.0,
            page=1,
            owner="",
            source_url=f"https://www.bilibili.com/bangumi/play/ep{ref.ep_id}",
        )
    ]


def attach_audio(client, meta: TrackMeta, settings: dict) -> TrackMeta:
    """查询 playurl，挑选最高可用音质，把地址写回 meta。"""
    params = {
        "bvid": meta.bvid,
        "cid": meta.cid,
        "fnval": "4048",
        "fnver": "0",
        "fourk": "1",
    }
    url = f"{API}/x/player/playurl?{urllib.parse.urlencode(params)}"
    data = _check(client.get_json(url), "获取音频流")
    dash = data.get("dash") or {}

    candidates: list[dict] = []
    for item in dash.get("audio") or []:
        candidates.append(
            {
                "id": int(item.get("id") or 0),
                "url": item.get("baseUrl") or item.get("base_url") or "",
                "backup": [u for u in (item.get("backupUrl") or item.get("backup_url") or []) if u],
                "codecs": item.get("codecs") or "",
                "bandwidth": int(item.get("bandwidth") or 0),
                "label": QUALITY_LABEL.get(int(item.get("id") or 0), str(item.get("id"))),
            }
        )
    # FLAC / 杜比需要大会员 Cookie，可用时优先
    for section, label in (("flac", "FLAC"), ("dolby", "杜比")):
        audio = ((dash.get(section) or {}).get("audio")) or None
        if isinstance(audio, dict):
            candidates.append(
                {
                    "id": 0,
                    "url": audio.get("baseUrl") or audio.get("base_url") or "",
                    "backup": [u for u in (audio.get("backupUrl") or audio.get("backup_url") or []) if u],
                    "codecs": audio.get("codecs") or "",
                    "bandwidth": int(audio.get("bandwidth") or 0),
                    "label": label,
                }
            )

    candidates = [c for c in candidates if c["url"]]
    if not candidates:
        raise BiliError("该视频没有可用音轨（无 dash.audio）")

    want = int(settings.get("audio_id") or 0)
    picked = None
    if want:
        picked = next((c for c in candidates if c["id"] == want), None)
    if picked is None:
        picked = max(candidates, key=lambda c: (c["bandwidth"], c["id"]))

    meta.audio_id = picked["id"]
    meta.codecs = picked["codecs"]
    meta.bandwidth = picked["bandwidth"]
    meta.audio_url = picked["url"]
    meta.quality_label = picked["label"]
    meta.backup_urls = picked["backup"][:2]
    if not meta.duration:
        meta.duration = float(data.get("timelength") or 0) / 1000.0
    return meta


def cache_name(meta: TrackMeta) -> str:
    suffix = "flac" if meta.audio_id == 0 and "flac" in (meta.codecs or "").lower() else str(meta.audio_id)
    return f"{meta.bvid}_{meta.cid}_{suffix}.m4s"
