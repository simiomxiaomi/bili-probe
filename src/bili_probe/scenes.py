"""场景检测与接触表。

思路（和手工做的一模一样，只是固化成流程）：

1. 相邻帧缩成小图、转灰度，算差的均值；
2. 差值超过阈值 → 视为镜头切换；
3. 每个镜头取中间帧，纯色/黑场丢掉；
4. 用均值哈希去重，避免同一画面反复出现；
5. 拼成 2×3 的接触表，带时间戳。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .utils import ToolError, ensure_dir


@dataclass
class Scene:
    index: int      # 在帧序列中的序号
    start: float    # 秒
    end: float
    file: Path


def _load_modules():
    try:
        import numpy as np
        from PIL import Image
    except ImportError as exc:  # pragma: no cover - 取决于环境
        raise ToolError(
            '场景检测需要 Pillow 和 numpy：pip install "bili-probe[sheets]"'
        ) from exc
    return np, Image


def detect(
    frames: list[Path],
    fps: float = 1.0,
    threshold: float = 3.0,
    min_len: int = 2,
    blank_std: float = 4.0,
    sample: tuple[int, int] = (213, 120),
    dedupe: float = 0.06,
) -> list[Scene]:
    """检测镜头切换，返回每个镜头的代表场景。

    参数
    ----
    threshold: 相邻帧灰度差的均值阈值。静止视频调低，快剪视频调高。
    min_len: 短于这个帧数的片段会被丢掉（避免把闪白当成场景）。
    blank_std: 灰度标准差低于它就当作黑场/纯色丢弃。
    dedupe: 哈希差异小于它的视为重复画面。
    """
    np, Image = _load_modules()
    if len(frames) < 2:
        raise ToolError("至少需要 2 帧才能做场景检测")

    thumbs = [
        np.asarray(Image.open(path).convert("L").resize(sample), dtype=np.float32)
        for path in frames
    ]
    diffs = [float(np.abs(thumbs[i] - thumbs[i - 1]).mean()) for i in range(1, len(thumbs))]

    cuts = [0] + [i for i, value in enumerate(diffs, start=1) if value > threshold] + [len(frames)]
    candidates: list[Scene] = []
    for begin, end in zip(cuts, cuts[1:]):
        if end - begin < min_len:
            continue
        middle = (begin + end) // 2
        if float(thumbs[middle].std()) < blank_std:
            continue
        candidates.append(
            Scene(index=middle, start=begin / fps, end=end / fps, file=frames[middle])
        )

    if not candidates:
        return []

    def ahash(array) -> "object":
        image = Image.fromarray(array.astype("uint8")).resize((16, 9))
        values = np.asarray(image, dtype=np.float32)
        return (values > values.mean()).flatten()

    kept: list[Scene] = []
    signatures: list = []
    for scene in candidates:
        signature = ahash(thumbs[scene.index])
        if any(float(np.mean(signature != old)) < dedupe for old in signatures[-4:]):
            continue
        kept.append(scene)
        signatures.append(signature)
    return kept


def contact_sheets(
    scenes: list[Scene],
    outdir: str | os.PathLike[str],
    cols: int = 2,
    rows: int = 3,
    cell: tuple[int, int] = (852, 480),
    quality: int = 88,
) -> list[Path]:
    """把场景拼成带时间戳的接触表，每张 ``cols × rows`` 格。"""
    _np, Image = _load_modules()
    from PIL import ImageDraw, ImageFont

    directory = ensure_dir(outdir)
    for stale in directory.glob("sheet*.jpg"):
        stale.unlink()

    per_sheet = cols * rows
    font = _load_font(28)
    paths: list[Path] = []

    for start in range(0, len(scenes), per_sheet):
        chunk = scenes[start:start + per_sheet]
        canvas = Image.new("RGB", (cell[0] * cols, cell[1] * rows), "white")
        draw = ImageDraw.Draw(canvas)
        for position, scene in enumerate(chunk):
            image = Image.open(scene.file).convert("RGB").resize(cell)
            x, y = (position % cols) * cell[0], (position // cols) * cell[1]
            canvas.paste(image, (x, y))
            draw.rectangle([x, y, x + 160, y + 40], fill="black")
            draw.text((x + 10, y + 6), f"{scene.start:05.1f}s", fill="white", font=font)
        path = directory / f"sheet{start // per_sheet + 1:02d}.jpg"
        canvas.save(path, quality=quality)
        paths.append(path)
    return paths


def _load_font(size: int):
    from PIL import ImageFont

    for name in ("arial.ttf", "segoeui.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow < 10
        return ImageFont.load_default()


def pick_nearest(scenes: list[Scene], times: list[float]) -> list[Scene]:
    """按时间点取最接近的场景，用于挑关键帧。"""
    picked: list[Scene] = []
    for when in times:
        if not scenes:
            break
        best = min(scenes, key=lambda scene: abs(scene.start - when))
        if best not in picked:
            picked.append(best)
    return picked
