"""pystray 托盘界面：一个图标管三种图片行为 + 文本格式化开关。

鼠标动线（对应需求"开启拼接模式-裁剪多个-完成拼接"）：
    右键菜单选「拼接模式」 → 连续截图（每张自动裁剪入队，气泡提示） →
    然后二选一：
        **左键点图标** = 完成拼接并复制
        右键菜单「分开保存到文件夹（N 张）」 = 按日期-时分秒逐张存到指定文件夹

图标只分"亮 / 灭"两态（需求：任意功能开启就亮，不区分是哪一个）：
    亮 = 绿（图片处理或文本公式格式化，任意一个开着）
    灭 = 灰（全部关闭）
角标数字是已收集张数。

两处 Windows 兼容补丁在 win32_tweaks.py：
    * 受限环境下 pystray 起步失败 → 降级容错
    * 右键菜单被开始菜单这类置顶窗口压住 → 菜单出现时强制置顶
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path

import pystray
from PIL import Image, ImageDraw
from pystray import Menu, MenuItem as Item

from . import config, win32_tweaks
from .monitor import ClipboardMonitor, ImageMode

LIT_COLOR = (46, 204, 113, 255)      # 亮：任意功能开启
IDLE_COLOR = (127, 140, 141, 255)    # 灭：全部关闭

MODE_TEXT = {
    ImageMode.OFF: "图片关闭",
    ImageMode.CROP: "即时裁剪",
    ImageMode.STITCH: "拼接模式",
}


class TrayApp:
    def __init__(self, monitor: ClipboardMonitor, *, interval: float = 0.25,
                 log=None, notify: bool = True):
        self.monitor = monitor
        self.interval = interval
        self.log = log
        self.notify = notify
        self.icon = None
        self.save_dir = config.save_dir()
        self._stop = threading.Event()
        self._thread = None
        self._sig = None

    # ------------------------------------------------------------ 图标

    def _icon_image(self) -> Image.Image:
        lit = self.monitor.any_enabled()
        img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.rounded_rectangle((6, 6, 58, 58), radius=12, fill=LIT_COLOR if lit else IDLE_COLOR)
        # 图形语言：一个裁剪框 + 一条中线
        d.rectangle((16, 16, 48, 40), outline=(255, 255, 255, 235), width=3)
        d.line((10, 28, 54, 28), fill=(255, 255, 255, 235), width=3)
        n = self.monitor.pending()
        if n:
            d.text((24, 40), str(n), fill=(255, 255, 255, 255))
        return img

    def _title(self) -> str:
        m = self.monitor
        parts = [f"clipkit — {'监听中' if m.any_enabled() else '已暂停'}",
                 MODE_TEXT[m.image_mode]]
        if m.pending():
            parts.append(f"已收集 {m.pending()} 张")
        if m.text_enabled:
            parts.append("公式格式化开")
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
            Item("拼接模式：连续截图 → 完成拼接 / 分开保存", self._set_stitch,
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
            Item(lambda item: f"开启时先处理当前剪贴板一次（{'开' if m.process_on_enable else '关'}）",
                 self._toggle_process_on_enable, checked=lambda item: m.process_on_enable),
            Menu.SEPARATOR,
            Item(lambda item: f"完成拼接并复制（{m.pending()} 张）", self._finish,
                 enabled=lambda item: m.pending() > 0),
            Item(lambda item: f"分开保存到文件夹（{m.pending()} 张）", self._save_split,
                 enabled=lambda item: m.pending() > 0),
            Item("清空已收集", self._clear, enabled=lambda item: m.pending() > 0),
            Menu.SEPARATOR,
            Item(lambda item: f"选择保存文件夹…（当前：{_short_dir(self.save_dir)}）",
                 self._choose_folder),
            Item("打开保存文件夹", self._open_folder),
            Menu.SEPARATOR,
            Item("退出", self._quit),
        )

    # ------------------------------------------------------------ 菜单动作

    def _after_feature_on(self) -> None:
        """开启功能之后：按 process_on_enable 决定要不要先处理当前剪贴板一次。"""
        if not self.monitor.process_on_enable:
            return
        r = self.monitor.emit(self.monitor.process_current_once())
        if r.wrote_clipboard or r.action in ("collected", "cropped", "formatted"):
            self._toast(r.message)

    def _set_stitch(self, icon=None, item=None):
        self.monitor.set_image_mode(ImageMode.STITCH)
        self._log("已切到拼接模式：连续截图自动收集；左键=完成拼接，菜单里可分开保存")
        self._refresh(force=True)
        self._after_feature_on()

    def _set_crop(self, icon=None, item=None):
        self.monitor.set_image_mode(ImageMode.CROP)
        self._log("已切到即时裁剪模式")
        self._refresh(force=True)
        self._after_feature_on()

    def _set_off(self, icon=None, item=None):
        self.monitor.set_image_mode(ImageMode.OFF)
        self._log("已关闭图片功能")
        self._refresh(force=True)

    def _toggle_text(self, icon=None, item=None):
        on = self.monitor.toggle_text()
        self._log(f"文本公式格式化：{'开' if on else '关'}")
        self._refresh(force=True)
        if on:
            self._after_feature_on()

    def _toggle_crop_before(self, icon=None, item=None):
        self.monitor.crop_before_stitch = not self.monitor.crop_before_stitch
        self._log(f"拼接前逐张裁剪：{'开' if self.monitor.crop_before_stitch else '关'}")
        self._refresh(force=True)

    def _toggle_process_on_enable(self, icon=None, item=None):
        on = not self.monitor.process_on_enable
        self.monitor.process_on_enable = on
        config.update(process_on_enable=on)
        self._log(f"开启时先处理当前剪贴板一次：{'开' if on else '关'}")
        self._refresh(force=True)

    def _finish(self, icon=None, item=None):
        r = self.monitor.emit(self.monitor.finish_stitch())
        if r.wrote_clipboard:
            self._toast(r.message)
        self._refresh(force=True)

    def _save_split(self, icon=None, item=None):
        r = self.monitor.emit(self.monitor.save_shots(self.save_dir))
        if r.action == "saved":
            self._toast(r.message)
        self._refresh(force=True)

    def _choose_folder(self, icon=None, item=None):
        picked = _ask_directory(self.save_dir)
        if not picked:
            return
        self.save_dir = Path(picked)
        saved = config.update(save_dir=str(self.save_dir))
        self._log(f"保存文件夹：{self.save_dir}" + ("" if saved else "（配置写不进去，仅本次生效）"))
        self._toast(f"保存文件夹已设为 {self.save_dir}")
        self._refresh(force=True)

    def _open_folder(self, icon=None, item=None):
        try:
            self.save_dir.mkdir(parents=True, exist_ok=True)
            os.startfile(str(self.save_dir))       # noqa: S606 - Windows 专用
        except Exception as e:  # noqa: BLE001
            self._log(f"[tray] 打开保存文件夹失败：{type(e).__name__}: {e}")

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
        win32_tweaks.relax_message_filter()
        self.icon = pystray.Icon("clipkit", self._icon_image(), self._title(), self._menu())
        # 右键菜单置顶补丁要打在实例的消息处理表上（pystray 在 __init__ 里绑的）
        win32_tweaks.install_topmost_menu(self.icon)
        self._sig = None
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="clipkit-monitor", daemon=True)
        self._thread.start()

        m = self.monitor
        self._log("clipkit 已启动：左键=主操作，右键=菜单")
        self._log(f"  图片模式={MODE_TEXT[m.image_mode]}，"
                  f"公式格式化={'开' if m.text_enabled else '关'}，"
                  f"开启时处理当前剪贴板={'开' if m.process_on_enable else '关'}")
        self._log(f"  分开保存的文件夹：{self.save_dir}")

        try:
            self.icon.run()   # 阻塞，直到 icon.stop()
        except Exception as e:  # noqa: BLE001 - 托盘起不来要给人话，而不是一串 traceback
            self._stop.set()  # 关键：停掉监听线程，否则"主线程死了、守护线程还活着"
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
        first = True
        while not self._stop.is_set():
            time.sleep(self.interval)
            try:
                if first:
                    first = False
                    # 启动的第一次：按 process_on_enable 决定"处理当前一次"还是"只建立基线"
                    r = self.monitor.emit(self.monitor.startup_tick())
                else:
                    r = self.monitor.emit(self.monitor.safe_tick())
                if r.action in ("collected", "cropped", "formatted"):
                    self._toast(r.message)
                self._refresh()
            except Exception as e:  # noqa: BLE001 - 循环本身也不许死
                self._log(f"[tray] 轮询异常，已跳过：{type(e).__name__}: {e}")

    # ------------------------------------------------------------ 小工具

    def _refresh(self, force: bool = False) -> None:
        if self.icon is None:
            return
        m = self.monitor
        sig = (m.image_mode, m.pending(), m.text_enabled, m.crop_before_stitch,
               m.process_on_enable, str(self.save_dir))
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


# ---------------------------------------------------------------- 模块级小工具


def _short_dir(path) -> str:
    """菜单里显示文件夹名，太长就退化成完整路径。"""
    name = getattr(path, "name", "") or str(path)
    return name if 0 < len(name) <= 18 else str(path)


def _ask_directory(initial) -> str | None:
    """弹系统"选择文件夹"对话框；tkinter 不可用就返回 None（调用方可提示改用配置）。"""
    try:
        import tkinter
        from tkinter import filedialog
    except Exception:
        return None

    root = None
    try:
        root = tkinter.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        picked = filedialog.askdirectory(initialdir=str(initial), title="选择截图保存文件夹")
        return picked or None
    except Exception:
        return None
    finally:
        if root is not None:
            try:
                root.destroy()
            except Exception:
                pass
