"""裁剪与拼接的结构性测试，以及从旧实现实测出来的冻结基准值。"""

from __future__ import annotations

import unittest
from pathlib import Path

from PIL import ImageDraw

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


class BlackBlankTests(unittest.TestCase):
    """空白 = 整条偏白 或 整条偏黑；1 像素宽的空白线（黑白同理）留作分隔线。"""

    @staticmethod
    def _hbar(band_color=(0, 0, 0), band_rows=22, width=100, content=20):
        """上内容块 + 满宽色带 + 下内容块（不留白边，便于算准尺寸）。"""
        height = content + band_rows + content
        img = support.blank(width, height, (255, 255, 255))
        d = ImageDraw.Draw(img)
        for y0 in (0, content + band_rows):
            d.rectangle((10, y0, width - 11, y0 + content - 1), fill=(20, 20, 20))
        d.rectangle((0, content, width - 1, content + band_rows - 1), fill=band_color)
        return img

    @staticmethod
    def _vbar(band_color=(0, 0, 0), band_cols=22, height=100, content=20):
        """左内容块 + 满高色带 + 右内容块（不留白边）。"""
        width = content + band_cols + content
        img = support.blank(width, height, (255, 255, 255))
        d = ImageDraw.Draw(img)
        for x0 in (0, content + band_cols):
            d.rectangle((x0, 0, x0 + content - 1, height - 1), fill=(20, 20, 20))
        d.rectangle((content, 0, content + band_cols - 1, height - 1), fill=band_color)
        return img

    def test_pure_black_band_is_cropped(self):
        out = imaging.process_page_image(self._hbar((0, 0, 0), band_rows=22))
        self.assertEqual(out.size, (80, 40))      # 22 行纯黑带被删

    def test_pure_black_vertical_band_is_cropped(self):
        out = imaging.process_page_image(self._vbar((0, 0, 0), band_cols=22))
        self.assertEqual(out.size, (40, 100))     # 22 列纯黑带被删

    def test_black_band_kept_when_black_removal_disabled(self):
        out = imaging.process_page_image(self._hbar((0, 0, 0), band_rows=22),
                                         black_threshold=-1, min_blank_run=1)
        self.assertEqual(out.size, (100, 62))     # 旧行为：黑带保留

    def test_dark_theme_background_is_not_treated_as_black(self):
        # #1E1E1E 灰度 30 > 8，不该被当纯黑删掉（深色主题截图的命门）
        out = imaging.process_page_image(self._hbar((30, 30, 30), band_rows=22))
        self.assertEqual(out.size, (100, 62))

    def test_one_pixel_black_line_survives(self):
        out = imaging.process_page_image(self._hbar((0, 0, 0), band_rows=1))
        self.assertEqual(out.size, (100, 41))     # 1 像素黑线留作分隔

    def test_one_pixel_white_line_survives(self):
        out = imaging.process_page_image(self._hbar((255, 255, 255), band_rows=1))
        self.assertEqual(out.size, (80, 41))      # 白线同理

    def test_thin_line_removed_when_min_blank_run_is_one(self):
        out = imaging.process_page_image(self._hbar((0, 0, 0), band_rows=1),
                                         min_blank_run=1)
        self.assertEqual(out.size, (80, 40))      # 不保护细线时照旧删掉

    def test_band_with_a_light_pixel_is_content(self):
        img = self._hbar((0, 0, 0), band_rows=22)
        img.putpixel((50, 30), (255, 255, 255))   # 黑带里出现一个白点
        out = imaging.process_page_image(img)
        self.assertEqual(out.size, (100, 41))     # 整条不再"纯黑" → 只有这一行留下

    def test_all_black_falls_back_like_all_white(self):
        black = support.blank(60, 40, (0, 0, 0))
        white = support.blank(60, 40, (255, 255, 255))
        self.assertEqual(imaging.process_page_image(black).size,
                         imaging.process_page_image(white).size)

    def test_extra_pass_cleans_margins_left_by_full_height_border(self):
        """贯通全高的右边会让单趟漏掉上下白边；多扫一趟（默认）就收干净。"""
        img = support.blank(80, 70, (255, 255, 255))
        d = ImageDraw.Draw(img)
        for y0 in (10, 40):
            d.rectangle((5, y0, 69, y0 + 19), fill=(20, 20, 20))
        d.rectangle((70, 0, 79, 69), fill=(0, 0, 0))     # 贯通全高的右边

        self.assertEqual(imaging.process_page_image(img, refine_passes=1).size, (65, 70))
        self.assertEqual(imaging.process_page_image(img, refine_passes=2).size, (65, 40))


if __name__ == "__main__":
    unittest.main()
