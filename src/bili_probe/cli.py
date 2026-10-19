"""命令行入口：``bili-probe <命令> <BV 号或链接>``。"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

from . import __version__
from .client import Client
from .comments import fetch as fetch_comments, format_comments
from .danmaku import (
    DANMAKU_URL,
    decompress,
    format_report,
    parse_xml,
    summarize,
    write_jsonl,
)
from .frames import extract
from .media import download as download_file, merge, pick_streams, probe
from .scenes import contact_sheets, detect, pick_nearest
from .utils import ToolError, ensure_dir, human_size, parse_video_id

QUALITY = {
    "8k": 127,
    "4k": 120,
    "1080p60": 116,
    "1080": 80,
    "720": 64,
    "480": 32,
    "360": 16,
}


# ---------------------------------------------------------------- 公共部分


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "target",
        metavar="VIDEO",
        help=(
            "必填。BV 号或视频链接，例如 BV1Jrp16HEAB "
            "或 https://www.bilibili.com/video/BV1Jrp16HEAB（带 ?p=2 会识别分P）"
        ),
    )
    parser.add_argument("-o", "--out", default="output", help="输出目录（默认 output）")
    parser.add_argument("--page", type=int, default=None, help="分P 序号，默认取链接里的 ?p=")
    parser.add_argument("--sessdata", default=os.environ.get("BILI_SESSDATA"),
                        help="登录 Cookie 里的 SESSDATA，需要高清晰度时才用")
    parser.add_argument("--ffmpeg", default=None, help="ffmpeg 可执行文件路径")
    parser.add_argument("-q", "--quiet", action="store_true", help="少打印中间信息")


def _paths(out: Path, bvid: str, page: int) -> dict[str, Path]:
    stem = f"{bvid}_p{page}"
    return {
        "root": out,
        "video": out / f"{stem}.mp4",
        "info": out / f"{stem}.info.json",
        "danmaku_jsonl": out / f"{stem}.danmaku.jsonl",
        "danmaku_xml": out / f"{stem}.danmaku.xml",
        "danmaku_report": out / f"{stem}.danmaku.txt",
        "comments": out / f"{stem}.comments.json",
        "frames": out / "frames",
        "scene_frames": out / "scenes" / "frames",
        "scenes": out / "scenes" / "scenes.json",
        "sheets": out / "sheets",
        "keyframes": out / "keyframes",
    }


def _resolve(args) -> tuple[Client, object, object, dict[str, Path]]:
    bvid, page_from_url = parse_video_id(args.target)
    page_index = args.page or page_from_url
    client = Client(sessdata=args.sessdata)
    info = client.view(bvid)
    page = info.page(page_index)
    out = ensure_dir(args.out)
    return client, info, page, _paths(out, info.bvid, page.index)


def _ensure_video(args, client, info, page, paths) -> Path:
    """返回本地视频路径，没有就下载。"""
    if args.video and Path(args.video).exists():
        return Path(args.video)
    if paths["video"].exists() and not getattr(args, "force", False):
        return paths["video"]
    return _download(args, client, info, page, paths)


def _download(args, client, info, page, paths) -> Path:
    ffmpeg = _ffmpeg(args)
    qn = QUALITY.get(str(getattr(args, "quality", "1080")).lower(), 80)
    data = client.playurl(info.bvid, page.cid, qn=qn)
    video, audio = pick_streams(data.get("dash") or {}, codec=getattr(args, "codec", "avc"))
    print(f"选取：{video.label} / {audio.label}", file=sys.stderr)

    tmp_dir = ensure_dir(paths["root"] / ".parts")
    video_part = tmp_dir / f"{info.bvid}_p{page.index}.video.m4s"
    audio_part = tmp_dir / f"{info.bvid}_p{page.index}.audio.m4s"

    print("下载视频流…", file=sys.stderr)
    download_file(video.url, video_part, client.headers, progress=not args.quiet)
    print("下载音频流…", file=sys.stderr)
    download_file(audio.url, audio_part, client.headers, progress=not args.quiet)

    print("合成 mp4…", file=sys.stderr)
    merge(ffmpeg, video_part, audio_part, paths["video"])
    if not getattr(args, "keep_parts", False):
        shutil.rmtree(tmp_dir, ignore_errors=True)

    meta = probe(ffmpeg, paths["video"])
    paths["info"].write_text(
        json.dumps(
            {
                "bvid": info.bvid,
                "aid": info.aid,
                "title": info.title,
                "owner": info.owner,
                "page": page.index,
                "part": page.part,
                "duration": info.duration,
                "stream": {"codec": video.codec, "width": video.width,
                           "height": video.height, "kbps": video.bandwidth // 1000},
                "probe": {k: v for k, v in meta.items() if k != "raw"},
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"完成：{paths['video']}  {human_size(paths['video'].stat().st_size)}", file=sys.stderr)
    return paths["video"]


def _ffmpeg(args) -> str:
    from .utils import find_ffmpeg

    return find_ffmpeg(getattr(args, "ffmpeg", None))


# ---------------------------------------------------------------- 各命令


def cmd_info(args) -> int:
    client, info, page, _ = _resolve(args)
    print(f"标题   {info.title}")
    print(f"UP     {info.owner}")
    print(f"BV     {info.bvid}   aid {info.aid}")
    print(f"时长   {info.duration // 60} 分 {info.duration % 60} 秒")
    print(f"分P    {len(info.pages)} 个")
    for item in info.pages:
        mark = "→" if item.index == page.index else " "
        print(f"  {mark} P{item.index}  {item.part}  cid={item.cid}")

    data = client.playurl(info.bvid, page.cid)
    dash = data.get("dash") or {}
    heights = sorted({int(v.get("height", 0)) for v in dash.get("video") or []}, reverse=True)
    if heights:
        print(f"可用清晰度   {', '.join(str(h) + 'P' for h in heights)}")
        codecs = sorted({str(v.get('codecs')) for v in dash.get("video") or []})
        print(f"可用编码     {', '.join(codecs)}")
    return 0


def cmd_download(args) -> int:
    client, info, page, paths = _resolve(args)
    _download(args, client, info, page, paths)
    return 0


def cmd_danmaku(args) -> int:
    client, info, page, paths = _resolve(args)
    raw = client.get(DANMAKU_URL.format(cid=page.cid), raw=True)
    xml = decompress(raw)

    items = parse_xml(xml)
    write_jsonl(items, str(paths["danmaku_jsonl"]))
    paths["danmaku_xml"].write_bytes(xml)

    report = summarize(items, top=args.top)
    text = format_report(report, title=f"{info.title} · P{page.index}")
    paths["danmaku_report"].write_text(text, encoding="utf-8")
    print(text)
    print(f"\n已保存：{paths['danmaku_jsonl']}")
    return 0


def cmd_comments(args) -> int:
    client, info, page, paths = _resolve(args)
    items = fetch_comments(client, info.aid, pages=args.pages, with_replies=args.with_replies)
    paths["comments"].write_text(
        json.dumps([item.as_dict() for item in items], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(format_comments(items, limit=args.top))
    print(f"\n已保存：{paths['comments']}")
    return 0


def cmd_frames(args) -> int:
    client, info, page, paths = _resolve(args)
    video = _ensure_video(args, client, info, page, paths)
    files = extract(_ffmpeg(args), video, paths["frames"], fps=args.fps,
                    fmt=args.format, width=args.width)
    print(f"抽出 {len(files)} 帧 → {paths['frames']}")
    return 0


def _detect_scenes(args, paths, video: Path):
    ffmpeg = _ffmpeg(args)
    frames = sorted(paths["scene_frames"].glob("*.jpg"))
    if not frames or getattr(args, "force", False):
        print(f"按 {args.fps} fps 抽帧用于分析…", file=sys.stderr)
        frames = extract(ffmpeg, video, paths["scene_frames"], fps=args.fps)
    scenes = detect(frames, fps=args.fps, threshold=args.threshold, min_len=args.min_len)
    paths["scenes"].parent.mkdir(parents=True, exist_ok=True)
    paths["scenes"].write_text(
        json.dumps(
            [{"start": round(s.start, 2), "end": round(s.end, 2), "file": s.file.name}
             for s in scenes],
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return scenes


def cmd_scenes(args) -> int:
    client, info, page, paths = _resolve(args)
    video = _ensure_video(args, client, info, page, paths)
    scenes = _detect_scenes(args, paths, video)
    print(f"检测到 {len(scenes)} 个场景")
    for scene in scenes:
        print(f"  {scene.start:7.2f}s  {scene.file.name}")
    print(f"\n已保存：{paths['scenes']}")
    return 0


def cmd_sheets(args) -> int:
    client, info, page, paths = _resolve(args)
    video = _ensure_video(args, client, info, page, paths)
    scenes = _detect_scenes(args, paths, video)
    if not scenes:
        print("没有检测到有效场景", file=sys.stderr)
        return 1
    sheets = contact_sheets(scenes, paths["sheets"], cols=args.cols, rows=args.rows)
    print(f"生成 {len(sheets)} 张接触表 → {paths['sheets']}")
    for sheet in sheets:
        print(f"  {sheet.name}")
    return 0


def cmd_keyframes(args) -> int:
    client, info, page, paths = _resolve(args)
    video = _ensure_video(args, client, info, page, paths)
    scenes = _detect_scenes(args, paths, video)
    times = [float(item) for item in str(args.times).split(",") if item.strip()]
    picked = pick_nearest(scenes, times)
    ensure_dir(paths["keyframes"])
    for scene in picked:
        target = paths["keyframes"] / f"k{scene.start:07.1f}.jpg"
        shutil.copy2(scene.file, target)
    print(f"挑出 {len(picked)} 张关键帧 → {paths['keyframes']}")
    return 0


def cmd_all(args) -> int:
    client, info, page, paths = _resolve(args)
    print("== 下载 ==", file=sys.stderr)
    video = _ensure_video(args, client, info, page, paths)

    print("== 弹幕 ==", file=sys.stderr)
    from .danmaku import decompress

    raw = client.get(DANMAKU_URL.format(cid=page.cid), raw=True)
    items = parse_xml(decompress(raw))
    write_jsonl(items, str(paths["danmaku_jsonl"]))
    paths["danmaku_report"].write_text(
        format_report(summarize(items), title=info.title), encoding="utf-8"
    )

    print("== 评论 ==", file=sys.stderr)
    try:
        comments = fetch_comments(client, info.aid, pages=args.pages)
        paths["comments"].write_text(
            json.dumps([item.as_dict() for item in comments], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except ToolError as exc:
        print(f"评论跳过：{exc}", file=sys.stderr)

    print("== 抽帧 / 场景 / 接触表 ==", file=sys.stderr)
    extract(_ffmpeg(args), video, paths["frames"], fps=args.fps, format="jpg")
    scenes = _detect_scenes(args, paths, video)
    if scenes:
        contact_sheets(scenes, paths["sheets"], cols=args.cols, rows=args.rows)

    print(f"\n全部完成 → {paths['root']}")
    print(f"  视频   {paths['video'].name}")
    print(f"  弹幕   {paths['danmaku_jsonl'].name}")
    print(f"  评论   {paths['comments'].name}")
    print(f"  场景   {len(scenes)} 个")
    return 0


# ---------------------------------------------------------------- 入口


EXAMPLES = """\
示例：
  bili-probe info BV1Jrp16HEAB
  bili-probe info https://www.bilibili.com/video/BV1Jrp16HEAB
  bili-probe download BV1Jrp16HEAB --quality 1080
  bili-probe all "https://www.bilibili.com/video/BV1Jrp16HEAB/?p=2"

注意：目标视频没有默认值，每条命令都必须手动指定 BV 号或完整链接。
"""


class _Parser(argparse.ArgumentParser):
    """缺少 VIDEO 参数时，给出比 argparse 默认更明确的提示。"""

    def error(self, message: str) -> None:  # type: ignore[override]
        if "VIDEO" in message or "target" in message:
            self.print_usage(sys.stderr)
            print("\n错误：必须指定要处理的视频。", file=sys.stderr)
            print("目标视频没有默认值，请手动输入 BV 号或完整链接，例如：", file=sys.stderr)
            print("  bili-probe info BV1Jrp16HEAB", file=sys.stderr)
            print(
                "  bili-probe info https://www.bilibili.com/video/BV1Jrp16HEAB",
                file=sys.stderr,
            )
            raise SystemExit(2)
        super().error(message)


def build_parser() -> argparse.ArgumentParser:
    parser = _Parser(
        prog="bili-probe",
        description="B 站视频探查：下载、弹幕、评论、抽帧、场景检测、接触表。",
        epilog=EXAMPLES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"bili-probe {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("info", help="查看视频信息与可用清晰度")
    _add_common(p)
    p.set_defaults(func=cmd_info)

    p = sub.add_parser("download", help="下载并合成 mp4")
    _add_common(p)
    p.add_argument("--quality", default="1080", choices=list(QUALITY), help="目标清晰度")
    p.add_argument("--codec", default="avc", choices=["avc", "hev", "av01", "any"])
    p.add_argument("--video", default=None, help="已存在的视频文件，跳过下载")
    p.add_argument("--force", action="store_true", help="覆盖已有文件")
    p.add_argument("--keep-parts", action="store_true", help="保留下载的音视频分片")
    p.set_defaults(func=cmd_download)

    p = sub.add_parser("danmaku", help="抓取弹幕并统计")
    _add_common(p)
    p.add_argument("--top", type=int, default=25, help="高频弹幕取前几条")
    p.set_defaults(func=cmd_danmaku)

    p = sub.add_parser("comments", help="抓取评论")
    _add_common(p)
    p.add_argument("--pages", type=int, default=1, help="抓几页（每页 20 条）")
    p.add_argument("--top", type=int, default=15, help="打印前几条")
    p.add_argument("--with-replies", action="store_true", help="包含楼中楼")
    p.set_defaults(func=cmd_comments)

    p = sub.add_parser("frames", help="按帧率抽帧")
    _add_common(p)
    p.add_argument("--fps", type=float, default=1.0, help="每秒抽几帧")
    p.add_argument("--format", default="jpg", choices=["jpg", "png"])
    p.add_argument("--width", type=int, default=None, help="缩放到指定宽度")
    p.add_argument("--video", default=None)
    p.set_defaults(func=cmd_frames)

    for name, handler, extra in (
        ("scenes", cmd_scenes, "detect"),
        ("sheets", cmd_sheets, "sheets"),
        ("keyframes", cmd_keyframes, "keyframes"),
    ):
        p = sub.add_parser(name, help={
            "detect": "检测镜头切换",
            "sheets": "生成接触表",
            "keyframes": "按时间点挑关键帧",
        }[extra])
        _add_common(p)
        p.add_argument("--fps", type=float, default=2.0, help="分析用抽帧帧率")
        p.add_argument("--threshold", type=float, default=3.0, help="场景切换阈值，越大越不敏感")
        p.add_argument("--min-len", type=int, default=2, help="最短场景帧数")
        p.add_argument("--video", default=None)
        p.add_argument("--force", action="store_true", help="强制重新抽帧")
        if extra == "sheets":
            p.add_argument("--cols", type=int, default=2)
            p.add_argument("--rows", type=int, default=3)
        if extra == "keyframes":
            p.add_argument("--times", default="0.5,10,30,60",
                           help="逗号分隔的时间点，取最近场景")
        p.set_defaults(func=handler)

    p = sub.add_parser("all", help="一条龙：下载 + 弹幕 + 评论 + 抽帧 + 场景 + 接触表")
    _add_common(p)
    p.add_argument("--quality", default="1080", choices=list(QUALITY))
    p.add_argument("--codec", default="avc", choices=["avc", "hev", "av01", "any"])
    p.add_argument("--fps", type=float, default=2.0)
    p.add_argument("--threshold", type=float, default=3.0)
    p.add_argument("--min-len", type=int, default=2)
    p.add_argument("--cols", type=int, default=2)
    p.add_argument("--rows", type=int, default=3)
    p.add_argument("--pages", type=int, default=1)
    p.add_argument("--video", default=None)
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_all)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not hasattr(args, "video"):
        args.video = None
    try:
        return int(args.func(args) or 0)
    except ToolError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\n已中断", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
