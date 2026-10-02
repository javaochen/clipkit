"""裁剪与拼接的结构性测试，以及从旧实现实测出来的冻结基准值。"""

from __future__ import annotations

import unittest
from pathlib import Path

from clipkit import imaging
from tests import support


class CropTests(unittest.TestCase):
    def test_frozen_sizes_on_two_block_image(self):
        """1141x591 / 1002x512 是从旧实现实测出来的基准，重写后必须一模一样。"""
        img = support.blocks_1280x720()
        self.assertEqual(imaging.crop_non_white(img).size, (1141, 591))
        self.assertEqual(imaging.process_page_image(img).size, (1002, 512))

    def test_interior_blank_band_is_removed(self):
        img = support.blocks_1280x720()
        border = imaging.crop_non_white(img)
        full = imaging.process_page_image(img)
        self.assertLess(full.width, border.width)     # 全高空白竖带被删
        self.assertLess(full.height, border.height)   # 全宽空白横带被删

    def test_remove_blank_rows_only_affects_height(self):
        img = support.blocks_1280x720()
        border = imaging.crop_non_white(img)
        out = imaging.remove_blank_rows(border)
        self.assertEqual(out.width, border.width)
        self.assertLess(out.height, border.height)

    def test_all_white_falls_back_to_min_size(self):
        img = support.blank(100, 100)
        self.assertIsNone(imaging.find_content_bbox(img))
        self.assertEqual(imaging.crop_non_white(img).size, (8, 8))
        self.assertEqual(imaging.process_page_image(img).size, (8, 8))

    def test_single_block_keeps_its_size(self):
        img = support.block(100, 100, (20, 20, 80, 80))
        self.assertEqual(imaging.find_content_bbox(img), (20, 20, 81, 81))
        self.assertEqual(imaging.crop_non_white(img).size, (61, 61))
        self.assertEqual(imaging.process_page_image(img).size, (61, 61))

    def test_content_touching_edges(self):
        img = support.block(50, 50, (0, 0, 50, 50))
        self.assertEqual(imaging.crop_non_white(img).size, (50, 50))
        self.assertEqual(imaging.process_page_image(img).size, (50, 50))

    def test_tiny_images_do_not_crash(self):
        self.assertEqual(imaging.crop_non_white(support.block(1, 1, (0, 0, 1, 1))).size, (1, 1))
        self.assertEqual(imaging.process_page_image(support.block(3, 3, (0, 0, 3, 3))).size, (3, 3))

    def test_gray_and_rgba_inputs_are_accepted(self):
        self.assertEqual(imaging.process_page_image(support.blank(40, 30).convert("L")).mode, "RGB")
        self.assertEqual(imaging.process_page_image(support.blank(40, 30).convert("RGBA")).mode, "RGB")


class StitchTests(unittest.TestCase):
    def test_geometry_and_centering(self):
        imgs = [support.colored(10, 5, (255, 0, 0)),
                support.colored(20, 6, (0, 255, 0)),
                support.colored(30, 7, (0, 0, 255))]
        out = imaging.stitch_vertical_centered(imgs)
        self.assertEqual(out.size, (30, 18))
        self.assertEqual(out.mode, "RGBA")

        px = out.load()
        self.assertEqual(px[0, 0][3], 0)                    # 左侧留白是透明的
        self.assertEqual(px[10, 0][:3], (255, 0, 0))        # 10 宽 → x 偏移 (30-10)//2 = 10
        self.assertEqual(px[5, 5][:3], (0, 255, 0))         # 20 宽 → x 偏移 5，y 起点 5
        self.assertEqual(px[0, 11][:3], (0, 0, 255))        # 30 宽 → x 偏移 0，y 起点 11

    def test_empty_returns_none(self):
        self.assertIsNone(imaging.stitch_vertical_centered([]))
        self.assertIsNone(imaging.stitch_vertical_centered([None]))

    def test_align_left_and_right(self):
        imgs = [support.colored(10, 4, (255, 0, 0)), support.colored(30, 4, (0, 0, 255))]
        self.assertEqual(imaging.stitch_vertical(imgs, align="left").load()[0, 0][:3], (255, 0, 0))
        self.assertEqual(imaging.stitch_vertical(imgs, align="right").load()[20, 0][:3], (255, 0, 0))

    def test_stitch_image_files_two_pass(self):
        with support.temp_dir() as d:
            p1 = Path(d) / "a.png"
            p2 = Path(d) / "b.png"
            support.block(120, 80, (20, 15, 100, 65)).save(p1)   # 裁后 81x51
            support.block(60, 40, (5, 5, 55, 35)).save(p2)       # 裁后 51x31

            out = imaging.stitch_image_files([p1, p2])
            self.assertEqual(out.size, (81, 82))

    def test_stitch_image_files_respects_no_crop(self):
        with support.temp_dir() as d:
            p1 = Path(d) / "a.png"
            support.block(120, 80, (20, 15, 100, 65)).save(p1)
            self.assertEqual(imaging.stitch_image_files([p1], crop=False).size, (120, 80))


if __name__ == "__main__":
    unittest.main()
