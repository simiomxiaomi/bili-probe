"""基础解析逻辑的单元测试（不需要网络，也不依赖第三方库）。"""

from __future__ import annotations

import zlib

import pytest

from bili_probe.danmaku import Danmaku, decompress, parse_xml, summarize
from bili_probe.utils import ToolError, human_size, parse_video_id, safe_filename


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("BV1Jrp16HEAB", ("BV1Jrp16HEAB", 1)),
        ("  bv1jrp16heab  ".upper(), ("BV1JRP16HEAB", 1)),
        ("https://www.bilibili.com/video/BV1Jrp16HEAB/", ("BV1Jrp16HEAB", 1)),
        ("https://www.bilibili.com/video/BV1Jrp16HEAB/?p=3&spm=1", ("BV1Jrp16HEAB", 3)),
        ("https://www.bilibili.com/video/BV1Jrp16HEAB?p=12", ("BV1Jrp16HEAB", 12)),
    ],
)
def test_parse_video_id(raw, expected):
    assert parse_video_id(raw) == expected


def test_parse_video_id_rejects_garbage():
    with pytest.raises(ToolError):
        parse_video_id("https://example.com/not-a-video")


def test_human_size():
    assert human_size(512) == "512 B"
    assert human_size(2048) == "2.0 KB"
    assert human_size(5 * 1024 ** 3) == "5.0 GB"


def test_safe_filename():
    assert safe_filename('a/b:c*?"<>|d') == "a_b_c_d"
    assert safe_filename("   ") == "video"


SAMPLE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<i>
  <d p="3.0,5,25,255,0,0,0,0">第二条</d>
  <d p="1.5,1,25,16777215,0,0,0,0">第一条</d>
  <d p="bad,1,25,0">坏数据</d>
  <d p="5.0,1,25,0"></d>
</i>"""


def _raw_deflate(data: bytes) -> bytes:
    compressor = zlib.compressobj(9, zlib.DEFLATED, -15)
    return compressor.compress(data) + compressor.flush()


def test_decompress_raw_deflate():
    payload = _raw_deflate(SAMPLE_XML.encode("utf-8"))
    assert decompress(payload) == SAMPLE_XML.encode("utf-8")


def test_decompress_plain_xml_passthrough():
    data = SAMPLE_XML.encode("utf-8")
    assert decompress(data) == data


def test_parse_xml_skips_bad_rows_and_sorts():
    items = parse_xml(SAMPLE_XML.encode("utf-8"))
    assert [item.t for item in items] == [1.5, 3.0]
    assert items[0].text == "第一条"
    assert items[1].mode == 5
    assert items[1].color == 255


def test_summarize_density_and_top():
    items = [
        Danmaku(t=1.0, text="啊"),
        Danmaku(t=2.0, text="啊"),
        Danmaku(t=11.0, text="好"),
        Danmaku(t=12.5, text="好"),
        Danmaku(t=13.0, text="好"),
    ]
    report = summarize(items, top=2, bucket=10)
    assert report.total == 5
    assert report.top[0] == ("好", 3)
    assert report.density == {0: 2, 10: 3}
