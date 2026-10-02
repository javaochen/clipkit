"""pystray 托盘界面：一个图标管三种图片行为 + 文本格式化开关。

鼠标动线（对应需求"开启拼接模式-裁剪多个-完成拼接"）：
    右键菜单选「拼接模式」 → 连续截图（每张自动裁剪入队，秒级气泡提示） →
    **左键点图标** = 完成拼接并复制  /  或右键菜单「完成拼接并复制（N 张）」

图标配色：灰=图片关闭，绿=即时裁剪，蓝=拼接模式（角标是已收集张数）。
"""

from __future__ import annotations

import threading
import time

import pystray
from PIL import Image, ImageDraw
from pystray import Menu, MenuItem as Item

from .monitor import ClipboardMonitor, ImageMode

MODE_COLORS = {
    ImageMode.OFF: (127, 140, 141, 255),     # 灰
    ImageMode.CROP: (46, 204, 113, 255),     # 绿
    ImageMode.STITCH: (52, 152, 219, 255),   # 蓝
}

MODE_TEXT = {
    ImageMode.OFF: "图片关闭",
    ImageMode.CROP: "即时裁剪",
    ImageMode.STITCH: "拼接模式",
}


def _relax_pystray_message_filter() -> bool:
    """把 pystray 的 ChangeWindowMessageFilterEx 换成"失败也不致命"的版本。

    这次调用（pystray/_win32.py 的 _create_window）只是为了让托盘图标在
    **资源管理器重启后**还能收回来。在受限环境（低完整性令牌 / 沙箱 / 受限令牌）下
    它会返回 WinError 5 拒绝访问，而 pystray 没有兜底，于是整个托盘起不来、
    只留一串 traceback。这里包一层：真实调用照做，只在被拒时降级——
    图标照常可用，代价仅是 Explorer 重启后可能不再自动回来。
    """
    try:
        from pystray._util import win32 as pwin32
    except Exception:
        return False

    original = getattr(pwin32, "ChangeWindowMessageFilterEx", None)
    if original is None or getattr(original, "_clipkit_tolerant", False):
        return False

    def tolerant(*args, **kwargs):
        try:
            return original(*args, **kwargs)
        except OSError:      # PermissionError 是 OSError 子类
            return None

    tolerant._clipkit_tolerant = True
    pwin32.ChangeWindowMessageFilterEx = tolerant
    return True


class TrayApp:
    def __init__(self, monitor: ClipboardMonitor, *, interval: float = 0.25,
                 log=None, notify: bool = True):
        self.monitor = monitor
        self.interval = interval
        self.log = log
        self.notify = notify
        self.icon = None
        self._stop = threading.Event()
        self._thread = None
        self._sig = None

    # ------------------------------------------------------------ 图标

    def _icon_image(self) -> Image.Image:
        mode = self.monitor.image_mode
        img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.rounded_rectangle((6, 6, 58, 58), radius=12, fill=MODE_COLORS[mode])
        # 图形语言：一个裁剪框 + 一条中线
        d.rectangle((16, 16, 48, 40), outline=(255, 255, 255, 235), width=3)
        d.line((10, 28, 54, 28), fill=(255, 255, 255, 235), width=3)
        n = self.monitor.pending()
        if mode is ImageMode.STITCH and n:
            d.text((25, 41), str(n), fill=(255, 255, 255, 255))
        return img

    def _title(self) -> str:
        m = self.monitor
        parts = [f"clipkit — {MODE_TEXT[m.image_mode]}"]
        if m.image_mode is ImageMode.STITCH:
            parts.append(f"已收集 {m.pending()} 张")
        parts.append("公式格式化开" if m.text_enabled else "公式格式化关")
        return " / ".join(parts)

    # ------------------------------------------------------------ 主操作（左键）

    def _primary_label(self) -> str:
        m = self.monitor
        if m.image_mode is ImageMode.STITCH:
            n = m.pending()
            return f"完成拼接并复制（{n} 张）" if n else "拼接模式：等截图（右键可切模式）"
        if m.image_mode is ImageMode.CROP:
            return "即时裁剪运行中（点此暂停）"
        return "图片功能已关闭（点此开始即时裁剪）"

    def _primary(self, icon=None, item=None):
        m = self.monitor
        if m.image_mode is ImageMode.STITCH:
            if m.pending():
                self._finish()
            else:
                self._toast("拼接模式已开启：连续截图，然后左键点图标完成拼接")
        elif m.image_mode is ImageMode.CROP:
            self._set_off()
            self._toast("已暂停图片处理")
        else:
            self._set_crop()
            self._toast("已开启即时裁剪")

    # ------------------------------------------------------------ 菜单

    def _menu(self):
        m = self.monitor
        return Menu(
            Item(lambda item: self._primary_label(), self._primary, default=True),
            Menu.SEPARATOR,
            Item("拼接模式：连续截图 → 完成拼接", self._set_stitch,
                 checked=lambda item: m.image_mode is ImageMode.STITCH, radio=True),
            Item("即时裁剪：每张立刻改写剪贴板", self._set_crop,
                 checked=lambda item: m.image_mode is ImageMode.CROP, radio=True),
            Item("关闭图片功能", self._set_off,
                 checked=lambda item: m.image_mode is ImageMode.OFF, radio=True),
            Menu.SEPARATOR,
            Item(lambda item: f"拼接前逐张裁剪（{'开' if m.crop_before_stitch else '关'}）",
                 self._toggle_crop_before, checked=lambda item: m.crop_before_stitch),
            Item(lambda item: f"文本公式格式化（{'开' if m.text_enabled else '关'}）",
                 self._toggle_text, checked=lambda item: m.text_enabled),
            Menu.SEPARATOR,
            Item(lambda item: f"完成拼接并复制（{m.pending()} 张）", self._finish,
                 enabled=lambda item: m.pending() > 0),
            Item("清空已收集", self._clear, enabled=lambda item: m.pending() > 0),
            Menu.SEPARATOR,
            Item("退出", self._quit),
        )

    # ------------------------------------------------------------ 菜单动作

    def _set_stitch(self, icon=None, item=None):
        self.monitor.set_image_mode(ImageMode.STITCH)
        self._log("已切到拼接模式：连续截图自动收集，左键点图标完成拼接")
        self._toast("拼接模式已开启：连续截图后左键点图标完成拼接")
        self._refresh(force=True)

    def _set_crop(self, icon=None, item=None):
        self.monitor.set_image_mode(ImageMode.CROP)
        self._log("已切到即时裁剪模式")
        self._refresh(force=True)

    def _set_off(self, icon=None, item=None):
        self.monitor.set_image_mode(ImageMode.OFF)
        self._log("已关闭图片功能")
        self._refresh(force=True)

    def _toggle_text(self, icon=None, item=None):
        on = self.monitor.toggle_text()
        self._log(f"文本公式格式化：{'开' if on else '关'}")
        self._refresh(force=True)

    def _toggle_crop_before(self, icon=None, item=None):
        self.monitor.crop_before_stitch = not self.monitor.crop_before_stitch
        self._log(f"拼接前逐张裁剪：{'开' if self.monitor.crop_before_stitch else '关'}")
        self._refresh(force=True)

    def _finish(self, icon=None, item=None):
        r = self.monitor.emit(self.monitor.finish_stitch())
        if r.wrote_clipboard:
            self._toast(r.message)
        self._refresh(force=True)

    def _clear(self, icon=None, item=None):
        n = self.monitor.clear_queue()
        self._log(f"已清空收集（{n} 张）")
        self._refresh(force=True)

    def _quit(self, icon=None, item=None):
        self._stop.set()
        self._log("退出中…")
        if self.icon is not None:
            self.icon.stop()

    # ------------------------------------------------------------ 运行

    def run(self) -> int:
        _relax_pystray_message_filter()
        self.icon = pystray.Icon("clipkit", self._icon_image(), self._title(), self._menu())
        self._sig = None
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="clipkit-monitor", daemon=True)
        self._thread.start()
        self._log("clipkit 已启动：左键=主操作，右键=菜单")
        self._log(f"  图片模式={MODE_TEXT[self.monitor.image_mode]}，"
                  f"公式格式化={'开' if self.monitor.text_enabled else '关'}")

        try:
            self.icon.run()   # 阻塞，直到 icon.stop()
        except Exception as e:  # noqa: BLE001 - 托盘起不来要给人话，而不是一串 traceback
            self._stop.set()    # 关键：停掉监听线程，否则"主线程死了、守护线程还活着"
            if self._thread is not None:
                self._thread.join(timeout=1.0)
            self._log(f"[!] 托盘启动失败：{type(e).__name__}: {e}")
            self._log("    图标没能创建，程序以退出码 1 结束（调用方据此判定启动失败）。"
                      "受限环境（沙箱 / 低权限令牌）下系统会拒绝托盘相关调用，"
                      "请改用普通控制台运行；离线处理可用 python -m clipkit simulate <目录>。")
            return 1

        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
        return 0

    def _loop(self) -> None:
        while not self._stop.is_set():
            time.sleep(self.interval)
            try:
                r = self.monitor.emit(self.monitor.safe_tick())
                if r.action == "collected":
                    self._toast(r.message)
                self._refresh()
            except Exception as e:  # noqa: BLE001 - 循环本身也不许死
                self._log(f"[tray] 轮询异常，已跳过：{type(e).__name__}: {e}")

    # ------------------------------------------------------------ 小工具

    def _refresh(self, force: bool = False) -> None:
        if self.icon is None:
            return
        m = self.monitor
        sig = (m.image_mode, m.pending(), m.text_enabled, m.crop_before_stitch)
        if not force and sig == self._sig:
            return
        self._sig = sig
        try:
            self.icon.icon = self._icon_image()
            self.icon.title = self._title()
            self.icon.update_menu()
        except Exception as e:  # noqa: BLE001
            self._log(f"[tray] 刷新图标失败：{type(e).__name__}: {e}")

    def _toast(self, message: str) -> None:
        if not self.notify or self.icon is None:
            return
        try:
            self.icon.notify(message, "clipkit")
        except Exception:
            pass

    def _log(self, message: str) -> None:
        if self.log:
            self.log(message)
