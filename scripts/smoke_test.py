#!/usr/bin/env python
"""真实联网冒烟测试：解析 → 下载 → 转码 → 合并 → 校验。

    python scripts\\smoke_test.py            # 用热门短视频自动挑选 3 条
    python scripts\\smoke_test.py --keep     # 保留中间文件便于查看
    python scripts\\smoke_test.py BV1xx... BV1yy...   # 指定 BVID

仅用于开发自检，会真实访问 B 站接口。
"""

from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from biliaudio import audio, bilibili, config, jobs, pipeline, project as project_mod  # noqa: E402
from biliaudio.net import Client  # noqa: E402

SANDBOX = ROOT / "workspace" / ".smoke"
FAILED: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  [{'✓' if ok else '✗'}] {name}{(' — ' + detail) if detail else ''}")
    if not ok:
        FAILED.append(name)


def pick_videos(client: Client, count: int = 3) -> list[str]:
    """从热门列表里挑时长合适的短视频。"""
    try:
        data = client.get_json("https://api.bilibili.com/x/web-interface/popular?ps=50&pn=1")
        items = ((data or {}).get("data") or {}).get("list") or []
        picked = [it["bvid"] for it in items if 20 <= int(it.get("duration") or 0) <= 110][:count]
        if picked:
            return picked
    except Exception as exc:  # noqa: BLE001
        print(f"  热门列表获取失败（{exc}），改用固定样本")
    return ["BV1GJ411x7h7"] * count


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    keep = "--keep" in sys.argv

    if "--fresh" in sys.argv and SANDBOX.exists():
        shutil.rmtree(SANDBOX, ignore_errors=True)
    for sub in ("cache", "cache/norm", "output", "projects"):
        (SANDBOX / sub).mkdir(parents=True, exist_ok=True)
    # 清掉上一轮的项目与产物，保留下载缓存以便快速重跑
    for pattern in ("*.json",):
        for path in (SANDBOX / "projects").glob(pattern):
            path.unlink(missing_ok=True)
    for path in (SANDBOX / "output").glob("*"):
        path.unlink(missing_ok=True)

    # 沙箱化：不污染真实 workspace
    pipeline.CACHE_DIR = SANDBOX / "cache"
    pipeline.NORM_DIR = SANDBOX / "cache" / "norm"
    pipeline.OUTPUT_DIR = SANDBOX / "output"
    project_mod.PROJECT_DIR = SANDBOX / "projects"
    project_mod.CURRENT_FILE = SANDBOX / "projects" / "current.json"
    config.TMP_DIR = SANDBOX / "tmp"
    config.TMP_DIR.mkdir(parents=True, exist_ok=True)
    audio.TMP_DIR = config.TMP_DIR

    settings = dict(config.DEFAULT_SETTINGS)
    settings.update({"bitrate_kbps": 192, "parallel": 3})
    client = Client("")
    client.bootstrap()

    print("\n[1] ffmpeg")
    status = audio.ffmpeg_status()
    check("ffmpeg 可用", status["ok"], status.get("version") or status.get("path"))
    if not status["ok"]:
        print("\n请先运行： python scripts\\fetch_ffmpeg.py")
        return 2
    print(f"      MP3 编码器：{'有' if status['mp3'] else '无（将只输出 M4A）'}")

    print("\n[2] 解析")
    bvids = args or pick_videos(client, 3)
    print(f"      样本：{', '.join(bvids)}")
    metas = []
    for bvid in bvids:
        ref = bilibili.parse_input(bvid)
        metas.extend(bilibili.resolve(client, ref))
    check("解析出曲目", len(metas) > 0, f"{len(metas)} 条")
    for meta in metas:
        print(f"      · {meta.display_title}（{meta.duration:.0f}s）")

    print("\n[3] 下载纯音频")
    project = project_mod.new_project("冒烟测试")
    payload = [
        {
            "bvid": m.bvid, "aid": m.aid, "cid": m.cid, "page": m.page,
            "title": m.title, "part": m.part, "owner": m.owner,
            "duration": m.duration, "source_url": m.source_url,
        }
        for m in metas[:3]
    ]
    project_mod.add_tracks(project, payload)
    job = jobs.Job("smoke", "冒烟测试")
    t0 = time.time()
    pipeline.run_download(job, project["id"], settings)
    project = project_mod.load_project(project["id"])
    done = [t for t in project["tracks"] if t.get("status") == "done"]
    check("全部下载完成", len(done) == len(project["tracks"]), f"{len(done)}/{len(project['tracks'])}")
    for track in project["tracks"]:
        size = Path(track["cache_file"]).stat().st_size if track.get("cache_file") else 0
        head = Path(track["cache_file"]).read_bytes()[:16] if size else b""
        print(f"      · {track['part'][:30]:30s} {size/1024/1024:5.1f}MB {track['quality_label']} "
              f"head={head[:8].decode('latin1')}")
        check(f"文件是 fMP4（{track['part'][:12]}）", b"ftyp" in head)

    print("\n[4] 合并 + 定位符")
    build_job = jobs.Job("smoke-build", "冒烟合并")
    result = pipeline.run_build(build_job, project["id"], settings)
    out = Path(result["path"])
    markers = result["markers"]
    check("输出文件存在", out.is_file(), f"{out.stat().st_size/1024/1024:.1f}MB")
    check("定位符数量", len(markers) == len(project["tracks"]), f"{len(markers)} 个")
    for marker in markers:
        print(f"      · {audio._hms(marker['start']):>7s}  {marker['title'][:40]}")

    # 定位符偏移必须等于前面各曲实际时长之和（采样级对齐）
    offsets_ok = all(
        abs(markers[i]["start"] - sum(m["duration"] for m in markers[:i])) < 0.05
        for i in range(len(markers))
    )
    check("定位符偏移 = 各曲实际时长累加", offsets_ok)
    tail = markers[-1]["start"] + markers[-1]["duration"]
    check("总时长 = 末曲结束点（<0.1s）", abs(result["duration"] - tail) < 0.1,
          f"文件 {result['duration']:.3f}s / 定位符尾 {tail:.3f}s")
    api_total = sum(float(t["duration"]) for t in project["tracks"])
    print(f"      参考：接口给的视频时长合计 {api_total:.1f}s，"
          f"实际音频 {result['duration']:.1f}s（音频流通常比视频短 1-2s，属正常）")

    print("\n[5] 章节 / 侧车文件")
    code, text, err = audio._run([audio.find_ffmpeg(), "-hide_banner", "-i", str(out)])
    chapter_lines = [line for line in (err or "").splitlines() if "Chapter #" in line]
    check("容器内写入章节", len(chapter_lines) == len(markers), f"{len(chapter_lines)} 个章节")
    cue = out.with_suffix(".cue")
    index = out.with_suffix(".index.json")
    check("cue 文件", cue.is_file())
    check("index.json 文件", index.is_file())
    if index.is_file():
        data = json.loads(index.read_text(encoding="utf-8"))
        check("index.json 结构", len(data["markers"]) == len(markers))

    print("\n[6] 重排后重建（应复用缓存）")
    tracks = project["tracks"]
    reordered = list(reversed([t["id"] for t in tracks]))
    project_mod.reorder(project, reordered)
    t1 = time.time()
    rebuild = jobs.Job("smoke-rebuild", "重排重建")
    result2 = pipeline.run_build(rebuild, project["id"], settings)
    elapsed = time.time() - t1
    check("重排后重建成功", Path(result2["path"]).is_file(), f"{elapsed:.1f}s")
    check("重建未重新下载",
          all("下载" not in line["text"] for line in rebuild.logs
              if "开始" not in line["text"] and "输出：" not in line["text"]),
          f"{len(rebuild.logs)} 条日志")
    starts = [m["start"] for m in result2["markers"]]
    check("定位符随顺序更新", starts == sorted(starts) and len(set(starts)) == len(starts))

    print("\n[7] MP3 导出")
    if audio.has_encoder("libmp3lame"):
        mp3_settings = dict(settings, format="mp3")
        r3 = pipeline.run_build(jobs.Job("smoke-mp3", "MP3 导出"), project["id"], mp3_settings)
        mp3_path = Path(r3["path"])
        check("mp3 产物", mp3_path.suffix == ".mp3" and mp3_path.is_file(),
              f"{mp3_path.stat().st_size/1024/1024:.1f}MB")
        check("mp3 时长一致", abs(r3["duration"] - result2["duration"]) < 0.2,
              f"{r3['duration']:.3f}s")
        code3, _t3, err3 = audio._run([audio.find_ffmpeg(), "-hide_banner", "-i", str(mp3_path)])
        chap3 = [line for line in (err3 or "").splitlines() if "Chapter #" in line]
        print(f"      MP3 容器章节：{len(chap3)} 个"
              f"（{'已写入' if chap3 else '未写入 ID3，界面定位符不受影响'}）")
    else:
        print("      跳过：当前 ffmpeg 无 libmp3lame")

    total_time = time.time() - t0
    print(f"\n总耗时 {total_time:.1f}s，输出目录：{SANDBOX / 'output'}")
    if FAILED:
        print(f"\n失败项（{len(FAILED)}）：" + "、".join(FAILED))
        return 1
    print("\n全部冒烟检查通过 ✓")
    if not keep:
        shutil.rmtree(SANDBOX / "cache", ignore_errors=True)
        shutil.rmtree(SANDBOX / "tmp", ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
