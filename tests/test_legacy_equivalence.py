"""与旧实现逐像素对拍：证明重写没有改变裁剪 / 拼接的结果。

旧文件就在上层目录（app/）。文件还在就跑，退役后自动跳过 —— 这样对拍
不会变成仓库的长期负担。

跑法：python -m unittest tests.test_legacy_equivalence -v
"""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

import numpy as np

from clipkit import imaging
from tests import support

APP_DIR = Path(__file__).resolve().parents[2]
LEGACY_CROP = APP_DIR / "clip2line_cut&$.py"
LEGACY_STITCH = APP_DIR / "copy_images.py"

# 旧行为 = 不裁纯黑（black_threshold=-1）+ 不保护 1 像素细线（min_blank_run=1）。
# 对拍时必须显式关掉新能力，才能保证"除了新增能力，原有裁剪逐像素不变"。
OLD_AXIS = {"black_threshold": -1, "min_blank_run": 1}
# 整条管线还要再多关一项：单趟（旧实现就是行→列各扫一遍）
OLD_PIPELINE = {**OLD_AXIS, "refine_passes": 1}


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载 {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _assert_same(test: unittest.TestCase, a, b, label: str) -> None:
    test.assertEqual(a.size, b.size, f"{label}: 尺寸不同 {a.size} vs {b.size}")
    test.assertEqual(a.mode, b.mode, f"{label}: 模式不同 {a.mode} vs {b.mode}")
    test.assertTrue(np.array_equal(np.asarray(a), np.asarray(b)), f"{label}: 像素不同")


@unittest.skipUnless(LEGACY_CROP.exists(), f"旧实现不在：{LEGACY_CROP}")
class CropEquivalenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.legacy = _load("legacy_clip2line", LEGACY_CROP)

    def test_every_case_matches_pixel_for_pixel(self):
        for name, maker in support.equivalence_cases().items():
            with self.subTest(case=name):
                a, b = maker(), maker()
                self.assertEqual(self.legacy.find_content_bbox(a),
                                 imaging.find_content_bbox(b), f"{name}: bbox 不同")
                _assert_same(self, self.legacy.crop_non_white(a),
                             imaging.crop_non_white(b), f"{name}/crop_non_white")
                _assert_same(self, self.legacy.remove_blank_rows(a),
                             imaging.remove_blank_rows(b, **OLD_AXIS),
                             f"{name}/remove_blank_rows")
                _assert_same(self, self.legacy.remove_blank_columns(a),
                             imaging.remove_blank_columns(b, **OLD_AXIS),
                             f"{name}/remove_blank_columns")
                _assert_same(self, self.legacy.process_page_image(a),
                             imaging.process_page_image(b, **OLD_PIPELINE),
                             f"{name}/process_page_image")

    def test_safe_crop_box_matches(self):
        for w, h in [(0, 0), (1, 1), (7, 7), (8, 8), (100, 50), (50, 100)]:
            for box in [None, (0, 0, 1, 1), (2, 2, 3, 3), (-5, -5, 4, 4), (90, 40, 200, 200)]:
                with self.subTest(w=w, h=h, box=box):
                    self.assertEqual(self.legacy.safe_crop_box(box, w, h, 8),
                                     imaging.safe_crop_box(box, w, h, 8))


@unittest.skipUnless(LEGACY_STITCH.exists(), f"旧实现不在：{LEGACY_STITCH}")
class StitchEquivalenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.legacy = _load("legacy_copy_images", LEGACY_STITCH)
        except Exception as e:  # noqa: BLE001 - 缺 pywin32 等环境问题就跳过
            raise unittest.SkipTest(f"无法加载 copy_images.py：{e}")

    def test_stitch_matches_legacy(self):
        imgs = [support.colored(10, 5, (255, 0, 0)),
                support.colored(20, 6, (0, 255, 0)),
                support.colored(30, 7, (0, 0, 255)),
                support.block(12, 4, (2, 1, 9, 3))]
        a = self.legacy.ClipboardStitchTrayWin32.stitch_vertical_centered(
            None, [i.copy() for i in imgs])
        b = imaging.stitch_vertical_centered([i.copy() for i in imgs])
        _assert_same(self, a, b, "stitch")

    def test_stitch_image_files_matches_in_memory_pipeline(self):
        cases = [support.blocks_1280x720, support.patterned, support.noisy]
        with support.temp_dir() as d:
            paths = []
            for i, maker in enumerate(cases):
                p = Path(d) / f"{i}.png"
                maker().save(p)
                paths.append(p)

            expected = imaging.stitch_vertical_centered(
                [imaging.process_page_image(m()) for m in cases])
            _assert_same(self, expected, imaging.stitch_image_files(paths), "stitch_image_files")


if __name__ == "__main__":
    unittest.main()
