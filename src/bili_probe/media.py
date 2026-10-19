"""播放流选择、下载与 ffmpeg 合成。"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from .utils import ToolError, human_size

# 兼容性优先：AVC 在各类播放器/剪辑软件里最稳，HEVC 次之，AV1 最后
CODEC_ORDER = ("avc", "hev", "av01")


@dataclass
class Stream:
    kind: str  # "video" | "audio"
    id: int
    codec: str
    width: int
    height: int
    bandwidth: int
    url: str

    @property
    def label(self) -> str:
        if self.kind == "audio":
            return f"audio id={self.id} {self.bandwidth // 1000} kbps"
        return f"{self.codec} {self.width}x{self.height} id={self.id}"


def _stream(item: dict, kind: str) -> Stream:
    url = item.get("baseUrl") or item.get("base_url") or ""
    return Stream(
        kind=kind,
        id=int(item.get("id", 0)),
        codec=str(item.get("codecs") or ""),
        width=int(item.get("width", 0) or 0),
        height=int(item.get("height", 0) or 0),
        bandwidth=int(item.get("bandwidth", 0) or 0),
        url=url,
    )


def pick_streams(
    dash: dict, codec: str = "avc", max_height: int | None = None
) -> tuple[Stream, Stream]:
    """从 DASH 清单里挑一路视频和一路音频。

    codec 取 ``avc`` / ``hev`` / ``av01`` / ``any``；指定的编码不存在时自动回退，
    而不是直接报错。
    """
    videos = [_stream(item, "video") for item in (dash.get("video") or [])]
    videos = [item for item in videos if item.url]
    if not videos:
        raise ToolError("playurl 没有返回可用的视频流（可能需要登录或该视频受限）")

    if codec != "any":
        matching = [item for item in videos if item.codec.lower().startswith(codec)]
        if not matching:
            print(f"[warn] 没有 {codec} 编码，回退到全部可用流", file=sys.stderr)
        else:
            videos = matching

    if max_height:
        capped = [item for item in videos if item.height <= max_height]
        if capped:
            videos = capped

    videos.sort(key=lambda item: (item.height, item.bandwidth))
    best_video = videos[-1]

    audios = [_stream(item, "audio") for item in (dash.get("audio") or [])]
    audios = [item for item in audios if item.url]
    if not audios:
        raise ToolError("playurl 没有返回音频流")
    best_audio = max(audios, key=lambda item: item.bandwidth)
    return best_video, best_audio


def download(
    url: str,
    dest: str | os.PathLike[str],
    headers: dict[str, str],
    timeout: float = 120.0,
    progress: bool = True,
) -> int:
    """流式下载到文件，返回字节数。"""
    dest = Path(dest)
    request = urllib.request.Request(url, headers=headers)
    written = 0
    with urllib.request.urlopen(request, timeout=timeout) as resp, open(dest, "wb") as fh:
        total = int(resp.headers.get("Content-Length") or 0)
        while True:
            chunk = resp.read(1 << 20)
            if not chunk:
                break
            fh.write(chunk)
            written += len(chunk)
            if progress and total:
                done = written * 100 // total
                sys.stderr.write(f"\r  {dest.name}  {done:3d}%  {human_size(written)}")
                sys.stderr.flush()
    if progress and total:
        sys.stderr.write("\n")
    return written


def merge(ffmpeg: str, video: Path, audio: Path, out: str | os.PathLike[str]) -> Path:
    """把分离的音视频轨道无损合成为一个 mp4。"""
    out = Path(out)
    result = subprocess.run(
        [ffmpeg, "-y", "-loglevel", "error", "-i", str(video), "-i", str(audio),
         "-c", "copy", "-movflags", "+faststart", str(out)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise ToolError(f"ffmpeg 合成失败：{result.stderr.strip()[-400:]}")
    return out


_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)")
_STREAM_RE = re.compile(r"Stream #0:\d+.*?: (Video|Audio): (\w+).*?(\d{2,5}x\d{2,5})?")


def probe(ffmpeg: str, path: str | os.PathLike[str]) -> dict:
    """用 ffmpeg 自身解析媒体信息（不依赖 ffprobe）。"""
    result = subprocess.run(
        [ffmpeg, "-hide_banner", "-i", str(path)], capture_output=True, text=True
    )
    text = result.stderr
    info: dict = {"path": str(path), "raw": text}

    match = _DURATION_RE.search(text)
    if match:
        hours, minutes, seconds = match.groups()
        info["duration"] = int(hours) * 3600 + int(minutes) * 60 + float(seconds)

    for kind, codec, size in _STREAM_RE.findall(text):
        key = kind.lower()
        info.setdefault(key, {"codec": codec})
        if size:
            width, height = size.split("x")
            info[key].update({"width": int(width), "height": int(height)})
    return info
