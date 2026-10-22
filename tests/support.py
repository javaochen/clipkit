"""测试辅助：内存假剪贴板 + 合成测试图。

全程不碰真实剪贴板、不依赖托盘，所以在没有桌面会话的地方也能跑。
"""

from __future__ import annotations

import contextlib
import itertools
import os
import shutil
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from clipkit.clipboard import ClipKind

# 临时目录放在仓库内：受限沙箱（workspace-write）下系统临时目录可能不可写
TMP_ROOT = Path(__file__).resolve().parent / "_tmp"
_counter = itertools.count()


@contextlib.contextmanager
def temp_dir():
    """工作区内的临时目录，退出时删掉。

    注意：这里刻意**不用 tempfile.mkdtemp** —— 它建出来的目录在受限沙箱下
    不可写（实测 PermissionError），普通 os.mkdir 的目录才正常。
    """
    TMP_ROOT.mkdir(parents=True, exist_ok=True)
    path = TMP_ROOT / f"run{os.getpid()}_{next(_counter)}"
    path.mkdir()
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


# ---------------------------------------------------------------- 合成图


def blank(w: int, h: int, color=(255, 255, 255)) -> Image.Image:
    return Image.new("RGB", (w, h), color)


def block(w: int, h: int, box, color=(20, 20, 20)) -> Image.Image:
    img = blank(w, h, (255, 255, 255))
    ImageDraw.Draw(img).rectangle(box, fill=color)
    return img


def colored(w: int, h: int, color) -> Image.Image:
    """左上角一小块指定颜色的白底图，用来验证拼接时的水平居中。"""
    img = blank(w, h)
    ImageDraw.Draw(img).rectangle((0, 0, min(w, 2) - 1, min(h, 2) - 1), fill=color)
    return img


def blocks_1280x720() -> Image.Image:
    """白底 + 四块内容，中间留"全宽空白横带"和"全高空白竖带"。

    几何（用于固定期望值）：
      内容包围盒 (60,50)-(1201,641)  → 1141x591
      去掉中间空白带后              → 1002x512
    这两个数字是从**旧实现**实测出来的，作为行为冻结的基准。
    """
    img = blank(1280, 720)
    d = ImageDraw.Draw(img)
    for box in [(60, 50, 500, 300), (60, 380, 500, 640),
                (640, 50, 1200, 300), (640, 380, 1200, 640)]:
        d.rectangle(box, fill=(20, 20, 20))
    return img


def noisy(w: int = 200, h: int = 150, seed: int = 7) -> Image.Image:
    """随机噪点 + 四周白边：把阈值判定推到边界上。"""
    rng = np.random.default_rng(seed)
    arr = (rng.random((h, w, 3)) * 255).astype(np.uint8)
    arr[:5, :, :] = 255
    arr[-5:, :, :] = 255
    arr[:, :5, :] = 255
    arr[:, -5:, :] = 255
    return Image.fromarray(arr)


def patterned(w: int = 240, h: int = 180) -> Image.Image:
    """浅灰细线 + 黑框：灰线可能被判为空白行，考验两段式裁剪。"""
    img = blank(w, h, (250, 250, 250))
    d = ImageDraw.Draw(img)
    for y in range(20, h - 20, 7):
        d.line((20, y, w - 20, y), fill=(240, 240, 240))
    d.rectangle((40, 30, w - 40, h - 30), outline=(0, 0, 0), width=2)
    return img


def equivalence_cases() -> dict:
    """对拍用例：每个 key 返回一张全新的图。"""
    return {
        "all_white_60x40": lambda: blank(60, 40),
        "one_block": lambda: block(120, 80, (20, 15, 100, 65)),
        "content_at_edges": lambda: block(50, 50, (0, 0, 50, 50)),
        "two_blocks_with_gaps": blocks_1280x720,
        "noise": noisy,
        "patterned": patterned,
        "tiny_1x1": lambda: block(1, 1, (0, 0, 1, 1)),
        "tiny_3x3": lambda: block(3, 3, (0, 0, 3, 3)),
        "wide_short": lambda: block(300, 9, (10, 2, 290, 7)),
        "tall_narrow": lambda: block(9, 300, (2, 10, 7, 290)),
        "light_gray_content": lambda: block(80, 60, (10, 10, 70, 50), color=(250, 250, 250)),
    }


# ---------------------------------------------------------------- 假剪贴板


class FakeClipboard:
    """内存剪贴板，实现 monitor 需要的后端协议。

    关键：write_* 会像真实剪贴板一样把序列号 +1（"自写回环"就是靠这个序列号
    变化来模拟的）。初始序列号是 1，因为真实 Windows 上 0 表示读取失败。
    """

    def __init__(self):
        self._seq = 1
        self._kind = ClipKind.EMPTY
        self._text = None
        self._image = None
        self.reads = []
        self.writes = []
        self.fail_write = False
        self.raise_on_read_image = False

    # ---- 后端协议 ----

    def sequence(self) -> int:
        return self._seq

    def kind(self) -> ClipKind:
        self.reads.append("kind")
        return self._kind

    def read_text(self):
        self.reads.append("text")
        return self._text

    def read_image(self):
        self.reads.append("image")
        if self.raise_on_read_image:
            raise RuntimeError("boom: 读图失败")
        return self._image

    def write_text(self, text: str) -> bool:
        if self.fail_write:
            return False
        self.writes.append(("text", text))
        self._seq += 1
        self._kind = ClipKind.TEXT
        self._text = text
        self._image = None
        return True

    def write_image(self, image) -> bool:
        if self.fail_write:
            return False
        self.writes.append(("image", image.size))
        self._seq += 1
        self._kind = ClipKind.IMAGE
        self._image = image.copy()
        self._text = None
        return True

    # ---- 模拟用户复制 ----

    def user_copy_text(self, text: str) -> None:
        self._seq += 1
        self._kind = ClipKind.TEXT
        self._text = text
        self._image = None

    def user_copy_image(self, image) -> None:
        self._seq += 1
        self._kind = ClipKind.IMAGE
        self._image = image.copy()
        self._text = None

    def user_copy_files(self) -> None:
        self._seq += 1
        self._kind = ClipKind.FILELIST
        self._text = None
        self._image = None
