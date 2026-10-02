"""剪贴板后端：按"格式位"判定内容类型，绝不误伤文本复制。

三条硬规则（对应需求"图片监听不能影响文本复制"）：

1. **轮询不打开剪贴板**。序列号走 user32.GetClipboardSequenceNumber()，这个调用
   不需要 OpenClipboard——每秒 4 次的空转完全不去锁剪贴板，不会干扰别的程序正在
   进行的复制/粘贴。只有序列号真的变了，才打开剪贴板读格式位。

2. **类型判定按安全优先级：文件 > 文本 > 图片**。浏览器 / Office / 资源管理器复制时
   经常同时放"文本 + HTML + DIB"（网页选区、Excel 区域）。这类富剪贴板一律按文本
   处理；若当成截图去裁剪改写，用户想粘贴的文字就没了。只有纯图片剪贴板
   （截图工具，通常只有 CF_DIB / PNG）才当图片。

3. **读路径绝不写**。只有确认是图片、且用户开了图片功能，才去读图；
   只有真的要写回结果时才 EmptyClipboard。
"""

from __future__ import annotations

import ctypes
import enum
import io
import os
import time

from PIL import Image, ImageGrab

try:  # 非 Windows 上导入本模块不应直接崩，便于跑纯函数测试
    import win32clipboard
    import win32con
except ImportError:  # pragma: no cover
    win32clipboard = None
    win32con = None


# Windows 剪贴板格式常量（手动补齐，避免依赖 win32con 的具体版本）
CF_TEXT = 1
CF_BITMAP = 2
CF_DIB = 8
CF_UNICODETEXT = 13
CF_HDROP = 15
CF_DIBV5 = 17

PNG_FORMAT_NAME = "PNG"

_user32 = ctypes.windll.user32 if os.name == "nt" else None
if _user32 is not None:
    try:
        # 序列号是 DWORD，别让 ctypes 当成有符号 int
        _user32.GetClipboardSequenceNumber.restype = ctypes.c_uint32
    except Exception:  # pragma: no cover
        _user32 = None


class ClipKind(enum.Enum):
    EMPTY = "empty"
    TEXT = "text"
    IMAGE = "image"
    FILELIST = "filelist"
    OTHER = "other"


def classify(formats) -> ClipKind:
    """把"剪贴板里有哪些格式"映射成内容类型。

    安全优先级：文件 > 文本 > 图片。这个顺序就是"不影响文本复制"的实现。
    """
    formats = set(formats)
    if not formats:
        return ClipKind.EMPTY
    if "filelist" in formats:
        return ClipKind.FILELIST
    if "text" in formats:
        return ClipKind.TEXT
    if "image" in formats:
        return ClipKind.IMAGE
    return ClipKind.OTHER


# ---------------------------------------------------------------- 编解码（纯函数）


def image_to_dib_bytes(image: Image.Image) -> bytes:
    """PIL 图 → CF_DIB 载荷（BMP 去掉 14 字节文件头）。"""
    buf = io.BytesIO()
    image.convert("RGB").save(buf, format="BMP")
    return buf.getvalue()[14:]


def image_to_png_bytes(image: Image.Image) -> bytes:
    """PIL 图 → PNG 字节（现代程序更认这个格式）。"""
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


# ---------------------------------------------------------------- 真实后端


class Win32Clipboard:
    """Windows 剪贴板后端。"""

    name = "win32"

    # ---- 序列号：不打开剪贴板 ----

    def sequence(self) -> int:
        if _user32 is not None:
            try:
                return int(_user32.GetClipboardSequenceNumber())
            except Exception:
                pass
        return self._sequence_by_opening()

    def _sequence_by_opening(self) -> int:
        if win32clipboard is None or not self._open():
            return 0
        try:
            return int(win32clipboard.GetClipboardSequenceNumber())
        except Exception:
            return 0
        finally:
            self._close()

    # ---- 打开/关闭（会被别的进程短暂占用，重试） ----

    @staticmethod
    def _open(retries: int = 4, delay: float = 0.03) -> bool:
        if win32clipboard is None:
            return False
        for _ in range(retries):
            try:
                win32clipboard.OpenClipboard()
                return True
            except Exception:
                time.sleep(delay)
        return False

    @staticmethod
    def _close() -> None:
        try:
            win32clipboard.CloseClipboard()
        except Exception:
            pass

    @staticmethod
    def _has(fmt) -> bool:
        try:
            return bool(win32clipboard.IsClipboardFormatAvailable(fmt))
        except Exception:
            return False

    @staticmethod
    def _has_png() -> bool:
        try:
            return bool(win32clipboard.IsClipboardFormatAvailable(
                win32clipboard.RegisterClipboardFormat(PNG_FORMAT_NAME)))
        except Exception:
            return False

    # ---- 类型 ----

    def kind(self) -> ClipKind:
        if win32clipboard is None or not self._open():
            return ClipKind.OTHER
        try:
            formats = set()
            if self._has(CF_UNICODETEXT) or self._has(CF_TEXT):
                formats.add("text")
            if self._has(CF_HDROP):
                formats.add("filelist")
            if self._has(CF_DIB) or self._has(CF_DIBV5) or self._has(CF_BITMAP) or self._has_png():
                formats.add("image")
            return classify(formats)
        finally:
            self._close()

    # ---- 读 ----

    def read_text(self):
        if win32clipboard is None or not self._open():
            return None
        try:
            if self._has(CF_UNICODETEXT):
                data = win32clipboard.GetClipboardData(CF_UNICODETEXT)
                return data or None
            if self._has(CF_TEXT):
                data = win32clipboard.GetClipboardData(CF_TEXT)
                return data or None
        except Exception:
            return None
        finally:
            self._close()
        return None

    def read_image(self):
        """只在 kind() 判定为图片后调用。

        grabclipboard() 在"复制文件"时返回的是**文件路径列表**而不是图片，
        这里必须只认 PIL.Image，别的一律当没有图片——否则下游会对 list
        做 .convert() 之类操作直接抛异常（旧版就是这么把监听线程搞死的）。
        """
        try:
            data = ImageGrab.grabclipboard()
        except Exception:
            return None
        if isinstance(data, Image.Image):
            return data.copy()
        return None

    # ---- 写 ----

    def write_text(self, text: str) -> bool:
        if win32clipboard is None or not text:
            return False
        for _ in range(3):
            if not self._open():
                continue
            try:
                win32clipboard.EmptyClipboard()
                win32clipboard.SetClipboardData(CF_UNICODETEXT, text)
                return True
            except Exception:
                return False
            finally:
                self._close()
        return False

    def write_image(self, image: Image.Image) -> bool:
        if win32clipboard is None or image is None:
            return False
        try:
            dib = image_to_dib_bytes(image)
            png = image_to_png_bytes(image)
        except Exception:
            return False

        for _ in range(3):
            if not self._open():
                continue
            try:
                win32clipboard.EmptyClipboard()
                win32clipboard.SetClipboardData(CF_DIB, dib)
                try:  # 顺手放一份 PNG，浏览器/微信粘贴兼容性更好
                    win32clipboard.SetClipboardData(
                        win32clipboard.RegisterClipboardFormat(PNG_FORMAT_NAME), png)
                except Exception:
                    pass
                return True
            except Exception:
                return False
            finally:
                self._close()
        return False

    # ---- 自检 ----

    def probe(self) -> dict:
        """只读探测，供 doctor 用。"""
        return {"sequence": self.sequence(), "kind": self.kind().value,
                "text": bool(self._has_text_readable())}

    def _has_text_readable(self) -> bool:
        if win32clipboard is None or not self._open():
            return False
        try:
            return self._has(CF_UNICODETEXT)
        finally:
            self._close()
