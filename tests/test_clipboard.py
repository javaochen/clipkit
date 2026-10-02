"""剪贴板编解码与类型判定（纯函数部分，不需要真实剪贴板）。"""

from __future__ import annotations

import struct
import unittest

from PIL import Image

from clipkit.clipboard import (ClipKind, classify, image_to_dib_bytes,
                               image_to_png_bytes)


class ClassifyTests(unittest.TestCase):
    def test_empty(self):
        self.assertIs(classify(set()), ClipKind.EMPTY)

    def test_simple_kinds(self):
        self.assertIs(classify({"text"}), ClipKind.TEXT)
        self.assertIs(classify({"image"}), ClipKind.IMAGE)
        self.assertIs(classify({"filelist"}), ClipKind.FILELIST)

    def test_text_wins_over_image_for_rich_clipboard(self):
        """浏览器/Office 复制时同时放文本和 DIB —— 必须按文本处理。

        这是"图片监听不能影响文本复制"的核心规则：否则用户想粘贴的文字会被
        当成截图裁掉改写。
        """
        self.assertIs(classify({"text", "image"}), ClipKind.TEXT)
        self.assertIs(classify({"text", "image", "html"}), ClipKind.TEXT)

    def test_filelist_wins_over_everything(self):
        self.assertIs(classify({"filelist", "text", "image"}), ClipKind.FILELIST)

    def test_unknown_formats(self):
        self.assertIs(classify({"html"}), ClipKind.OTHER)


class EncodeTests(unittest.TestCase):
    def setUp(self):
        self.img = Image.new("RGB", (12, 8), (10, 20, 30))

    def test_dib_is_bmp_without_file_header(self):
        import io
        buf = io.BytesIO()
        self.img.save(buf, format="BMP")
        bmp = buf.getvalue()

        dib = image_to_dib_bytes(self.img)
        self.assertEqual(len(dib), len(bmp) - 14)
        # CF_DIB 载荷以 BITMAPINFOHEADER 开头，其第一个字段是结构体大小 40
        self.assertEqual(struct.unpack("<I", dib[:4])[0], 40)

    def test_png_header(self):
        png = image_to_png_bytes(self.img)
        self.assertTrue(png.startswith(b"\x89PNG\r\n\x1a\n"))


if __name__ == "__main__":
    unittest.main()
