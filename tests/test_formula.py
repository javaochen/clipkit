"""文本公式格式化：行为冻结测试（对着旧 clip2line_cut&$.py 的行为写死）。"""

from __future__ import annotations

import unittest

from clipkit.formula import (ensure_blank_line_after_block_math, format_math_text,
                             has_formula_markers)


class FormatMathTextTests(unittest.TestCase):
    def test_inline(self):
        self.assertEqual(format_math_text(r"\(x^2\)"), "$x^2$")

    def test_block_on_own_lines(self):
        self.assertEqual(format_math_text("\\[a+b\\]"), "$$\na+b\n$$")

    def test_space_cleanup_around_dollar(self):
        self.assertEqual(format_math_text("$ x $"), "$x$")

    def test_strip_outer_whitespace(self):
        self.assertEqual(format_math_text("  \\(a\\)  "), "$a$")

    def test_block_then_text_gets_blank_line(self):
        self.assertEqual(
            ensure_blank_line_after_block_math("$$a$$\n文字"),
            "$$a$$\n\n文字",
        )

    def test_no_extra_blank_at_end(self):
        self.assertEqual(ensure_blank_line_after_block_math("$$\na\n$$"), "$$\na\n$$")

    def test_no_extra_blank_when_already_blank(self):
        self.assertEqual(ensure_blank_line_after_block_math("$$a$$\n\n文字"),
                         "$$a$$\n\n文字")

    def test_plain_text_untouched(self):
        text = "这是一段普通文字，没有公式。"
        self.assertEqual(format_math_text(text), text)


class HasFormulaMarkersTests(unittest.TestCase):
    def test_detects_inline_and_block_delimiters(self):
        self.assertTrue(has_formula_markers(r"\(x\)"))
        self.assertTrue(has_formula_markers(r"\[y\]"))
        self.assertTrue(has_formula_markers("$$z$$"))

    def test_plain_text_and_single_dollar_are_not_markers(self):
        self.assertFalse(has_formula_markers("普通文字"))
        self.assertFalse(has_formula_markers("$x$"))     # 已是目标格式，不必再动
        self.assertFalse(has_formula_markers("价格 $5 元"))


if __name__ == "__main__":
    unittest.main()
