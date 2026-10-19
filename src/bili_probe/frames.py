"""按帧率抽帧。

用 ffmpeg 直接抽，不经过 Python 逐帧解码——快，而且不会把内存吃满。
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from .utils import ToolError, ensure_dir


def extract(
    ffmpeg: str,
    video: str | os.PathLike[str],
    outdir: str | os.PathLike[str],
    fps: float = 1.0,
    fmt: str = "jpg",
    width: int | None = None,
    start: float | None = None,
    duration: float | None = None,
) -> list[Path]:
    """按帧率抽帧，返回按序号排序的图片路径列表。

    参数
    ----
    fps: 每秒抽几帧。0.5 表示每 2 秒一帧。
    fmt: ``jpg`` 或 ``png``。
    width: 等比缩放到指定宽度（高度自动为偶数）。
    start / duration: 只处理某一段。
    """
    if fps <= 0:
        raise ToolError("fps 必须大于 0")

    directory = ensure_dir(outdir)
    for stale in directory.glob(f"*.{fmt}"):
        stale.unlink()

    filters = [f"fps={fps}"]
    if width:
        filters.append(f"scale={width}:-2")

    args = [ffmpeg, "-y", "-loglevel", "error"]
    if start:
        args += ["-ss", str(start)]
    if duration:
        args += ["-t", str(duration)]
    args += ["-i", str(video), "-vf", ",".join(filters), "-q:v", "2",
             str(directory / f"f%05d.{fmt}")]

    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode != 0:
        raise ToolError(f"ffmpeg 抽帧失败：{result.stderr.strip()[-400:]}")

    files = sorted(directory.glob(f"*.{fmt}"))
    if not files:
        raise ToolError("没有抽到任何帧，检查视频路径和 fps 参数")
    return files
