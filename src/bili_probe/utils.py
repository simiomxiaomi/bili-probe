"""通用工具：ffmpeg 定位、路径处理、视频 ID 解析。"""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path


class ToolError(RuntimeError):
    """依赖缺失、参数非法或外部程序调用失败。"""


_BV_RE = re.compile(r"(BV[0-9A-Za-z]{10})")


def parse_video_id(text: str) -> tuple[str, int]:
    """从 BV 号或视频链接中解析出 ``(bvid, 分P序号)``。

    >>> parse_video_id("BV1Jrp16HEAB")
    ('BV1Jrp16HEAB', 1)
    >>> parse_video_id("https://www.bilibili.com/video/BV1Jrp16HEAB/?p=3")
    ('BV1Jrp16HEAB', 3)
    """
    text = (text or "").strip()
    match = _BV_RE.search(text)
    if not match:
        raise ToolError(f"无法从 {text!r} 中识别出 BV 号")
    page = 1
    page_match = re.search(r"[?&]p=(\d+)", text)
    if page_match:
        page = max(1, int(page_match.group(1)))
    return match.group(1), page


def find_ffmpeg(explicit: str | None = None) -> str:
    """按优先级查找 ffmpeg：显式参数 > 环境变量 > PATH > imageio-ffmpeg。"""
    for candidate in (explicit, os.environ.get("BILI_PROBE_FFMPEG")):
        if candidate and Path(candidate).is_file():
            return str(Path(candidate).resolve())

    on_path = shutil.which("ffmpeg")
    if on_path:
        return on_path

    try:
        import imageio_ffmpeg
    except ImportError:
        pass
    else:
        return imageio_ffmpeg.get_ffmpeg_exe()

    raise ToolError(
        "未找到 ffmpeg。请安装它（winget install Gyan.FFmpeg / brew install ffmpeg），"
        "或执行 pip install imageio-ffmpeg 让本工具自带一份。"
    )


def ensure_dir(path: str | os.PathLike[str]) -> Path:
    """确保目录存在并返回 Path。"""
    directory = Path(path)
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def human_size(num_bytes: float) -> str:
    """把字节数格式化成易读字符串。"""
    for unit in ("B", "KB", "MB", "GB"):
        if abs(num_bytes) < 1024 or unit == "GB":
            return f"{num_bytes:.1f} {unit}" if unit != "B" else f"{int(num_bytes)} B"
        num_bytes /= 1024
    return f"{num_bytes:.1f} GB"


def safe_filename(name: str, limit: int = 80) -> str:
    """把标题清理成可用的文件名。"""
    cleaned = re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", name).strip(" ._")
    cleaned = re.sub(r"\s+", " ", cleaned)
    return (cleaned or "video")[:limit]
