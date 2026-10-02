"""文本数学公式格式化（纯函数，行为与原 clip2line_cut&$.py 完全一致）。

规则：
    \\[ ... \\]  →  $$ 块（内容两侧换行）
    \\( ... \\)  →  $ 行内
    清理 $ 两侧的空格/制表符（不动换行，避免破坏 $$ 块结构）
    $$ 块结束后若紧跟内容则补一个空行
"""

from __future__ import annotations

import re


def ensure_blank_line_after_block_math(text: str) -> str:
    """行间公式（$$ 块）结束后若紧跟着内容，则补一个空行。

    支持两种写法：
      1) 独占一行的 $$ ... $$
      2) 同一行写完的 $$...$$
    已经是空行、或已到文本末尾时不再重复补。
    """
    lines = text.split("\n")
    result = []
    in_block = False

    def need_blank(idx: int) -> bool:
        """下一行存在且非空时才需要补空行"""
        return idx + 1 < len(lines) and lines[idx + 1].strip() != ""

    for idx, line in enumerate(lines):
        stripped = line.strip()
        result.append(line)

        if in_block:
            # 块内：遇到独占一行的 $$ 即视为块结束
            if stripped == "$$":
                in_block = False
                if need_blank(idx):
                    result.append("")
            continue

        if stripped == "$$":
            in_block = True
        elif len(stripped) > 4 and stripped.startswith("$$") and stripped.endswith("$$"):
            # 单行写法 $$...$$
            if need_blank(idx):
                result.append("")

    return "\n".join(result)


def format_math_text(text: str) -> str:
    """将 \\( \\) 转为 $，\\[ \\] 转为 $$，清理多余空格，并给行间公式补尾随空行"""

    def replace_block(match):
        content = match.group(1).strip()
        return f"$$\n{content}\n$$"

    def replace_inline(match):
        content = match.group(1).strip()
        return f"${content}$"

    # 块级公式 \[ ... \]
    text = re.sub(r"\\\[(.*?)\\\]", replace_block, text, flags=re.DOTALL)
    # 行内公式 \( ... \)
    text = re.sub(r"\\\((.*?)\\\)", replace_inline, text, flags=re.DOTALL)
    # 清理 $ 两侧空格（只清理空格/制表符，避免吃掉换行而破坏 $$ 块结构）
    text = re.sub(r"[ \t]+\$", "$", text)
    text = re.sub(r"\$[ \t]+", "$", text)
    # 行间公式结束后补空行
    text = ensure_blank_line_after_block_math(text)

    return text.strip()


def has_formula_markers(text: str) -> bool:
    """检测文本中是否包含数学公式标记（LaTeX 定界符 或 $$ 行间公式）。

    只有返回 True 时才会去改写剪贴板，避免动到用户普通复制的文本。
    """
    return bool(re.search(r"\\[\(\)\[\]]", text)) or "$$" in text
