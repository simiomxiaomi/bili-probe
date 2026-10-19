"""场景检测与接触表的测试（需要 Pillow + numpy，缺失时自动跳过）。"""

from __future__ import annotations

import pytest

np = pytest.importorskip("numpy")
Image = pytest.importorskip("PIL.Image")

from bili_probe.scenes import Scene, contact_sheets, detect, pick_nearest


def _frames(tmp_path, groups):
    """每组生成若干张完全相同的随机纹理，模拟一个稳定的镜头。"""
    rng = np.random.default_rng(42)
    paths = []
    index = 0
    for base in groups:
        pattern = np.clip(rng.normal(base, 40, (120, 213)), 0, 255).astype("uint8")
        for _ in range(6):
            path = tmp_path / f"f{index:05d}.png"
            Image.fromarray(pattern).save(path)
            paths.append(path)
            index += 1
    return paths


def test_detect_splits_on_hard_cut(tmp_path):
    frames = _frames(tmp_path, [50, 200])
    scenes = detect(frames, fps=1.0, threshold=3.0)
    assert len(scenes) == 2
    assert scenes[0].start == 0.0
    assert scenes[1].start == pytest.approx(6.0)


def test_detect_keeps_single_static_shot(tmp_path):
    frames = _frames(tmp_path, [128])
    scenes = detect(frames, fps=1.0)
    assert len(scenes) == 1


def test_detect_requires_two_frames(tmp_path):
    frames = _frames(tmp_path, [128])[:1]
    with pytest.raises(Exception):
        detect(frames)


def test_pick_nearest_returns_closest_scene(tmp_path):
    frames = _frames(tmp_path, [50, 200])
    scenes = detect(frames, fps=1.0)
    picked = pick_nearest(scenes, [0.4, 5.6])
    assert [scene.start for scene in picked] == [0.0, 6.0]


def test_contact_sheets_writes_files(tmp_path):
    frames = _frames(tmp_path, [50, 200])
    scenes = detect(frames, fps=1.0)
    sheets = contact_sheets(scenes, tmp_path / "sheets", cell=(320, 180))
    assert sheets and all(path.exists() for path in sheets)
