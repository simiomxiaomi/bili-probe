"""弹幕抓取与解析。

B 站的弹幕 XML 通常是 **raw deflate**（不是 gzip，也不是带 zlib 头的 deflate），
所以解压必须用 ``zlib.decompress(data, -15)``。这里对几种情况都做了兼容。
"""

from __future__ import annotations

import gzip
import json
import zlib
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import asdict, dataclass, field

from .utils import ToolError

DANMAKU_URL = "https://comment.bilibili.com/{cid}.xml"


@dataclass
class Danmaku:
    """一条弹幕。"""

    t: float          # 出现时间（秒）
    text: str
    mode: int = 1     # 1/2/3 滚动，4 底部，5 顶部
    size: int = 25
    color: int = 16777215

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class DanmakuReport:
    total: int
    top: list[tuple[str, int]] = field(default_factory=list)
    density: dict[int, int] = field(default_factory=dict)


def decompress(data: bytes) -> bytes:
    """把弹幕接口返回的内容解成 XML 字节。"""
    if data[:2] == b"\x1f\x8b":
        return gzip.decompress(data)
    for wbits in (-15, 15, 47):
        try:
            return zlib.decompress(data, wbits)
        except zlib.error:
            continue
    if data.lstrip()[:1] == b"<":
        return data
    raise ToolError("弹幕内容无法解压：不是 gzip / deflate，也不是明文 XML")


def parse_xml(xml_bytes: bytes) -> list[Danmaku]:
    """解析弹幕 XML。``<d p="时间,模式,字号,颜色,...">文本</d>``。"""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        raise ToolError(f"弹幕 XML 解析失败：{exc}") from exc

    items: list[Danmaku] = []
    for node in root.findall("d"):
        parts = (node.get("p") or "").split(",")
        try:
            timestamp = float(parts[0])
        except (IndexError, ValueError):
            continue
        text = (node.text or "").strip()
        if not text:
            continue
        items.append(
            Danmaku(
                t=timestamp,
                text=text,
                mode=_as_int(parts, 1, 1),
                size=_as_int(parts, 2, 25),
                color=_as_int(parts, 3, 16777215),
            )
        )
    items.sort(key=lambda item: item.t)
    return items


def _as_int(parts: list[str], index: int, default: int) -> int:
    try:
        return int(parts[index])
    except (IndexError, ValueError):
        return default


def summarize(items: list[Danmaku], top: int = 25, bucket: int = 10) -> DanmakuReport:
    """统计高频弹幕与时间密度。"""
    counter = Counter(item.text for item in items)
    density: Counter[int] = Counter()
    for item in items:
        density[int(item.t // bucket) * bucket] += 1
    return DanmakuReport(
        total=len(items),
        top=counter.most_common(top),
        density=dict(sorted(density.items())),
    )


def write_jsonl(items: list[Danmaku], path: str) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        for item in items:
            fh.write(json.dumps(item.as_dict(), ensure_ascii=False) + "\n")


def format_report(report: DanmakuReport, title: str = "") -> str:
    """把统计结果排成可直接打印/存盘的文本。"""
    lines = []
    if title:
        lines.append(title)
    lines.append(f"弹幕总数：{report.total}")
    lines.append("")
    lines.append("== 高频弹幕 ==")
    for text, count in report.top:
        lines.append(f"{count:>4}  {text}")
    lines.append("")
    lines.append("== 密度（每 10 秒）==")
    lines.append("  ".join(f"{sec}s:{cnt}" for sec, cnt in report.density.items()))
    return "\n".join(lines)
