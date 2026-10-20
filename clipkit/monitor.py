"""剪贴板处理状态机：拼接收集 / 即时裁剪 / 文本公式格式化。

四条需求的落点：

* **开启拼接模式-裁剪多个-完成拼接**
  拼接模式下每张进来的图先裁剪、再入队（连拍摄时刻一起记），**期间一次都不写剪贴板**；
  "完成拼接"时纵向拼合写回，"分开保存"时按日期-时分秒逐张存到指定文件夹。

* **图片监听不能影响文本复制**
  剪贴板里是文本时（哪怕图片功能开着），只做公式判断，不改写就绝不 EmptyClipboard；
  是文件列表时直接跳过。类型判定由 clipboard.classify 的安全优先级保证。

* **process_on_enable（默认 True）**
  开启"即时裁剪"或"公式格式化"（包括启动时就带着这些开关）时，把当前剪贴板内容
  **恰好处理一次**。False 则不干扰：只建立基线，启动前就在剪贴板里的内容一律不动。

* **一轮异常不能弄死监听**
  旧版 monitor_loop 没有 try/except：复制文件时 grabclipboard 返回路径列表，
  下游 AttributeError 直接把监听线程打死，而托盘图标还显示"监听中"。
  现在统一走 safe_tick()，异常被记下来、循环继续。

设计上 monitor 不依赖 pystray / 不依赖真实剪贴板：后端可注入，
tick() 是单次纯轮询，测试直接调它，不用起托盘、不用等 sleep。
"""

from __future__ import annotations

import enum
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from . import exporter
from .clipboard import ClipKind, Win32Clipboard
from .exporter import Shot
from .formula import format_math_text, has_formula_markers
from .imaging import (BLACK_THRESHOLD, MIN_BLANK_RUN, process_page_image,
                      stitch_vertical_centered)


class ImageMode(enum.Enum):
    OFF = "off"        # 不处理图片
    CROP = "crop"      # 即时裁剪：每张立刻裁剪并写回
    STITCH = "stitch"  # 拼接：逐张裁剪入队，"完成拼接"时一次写回


@dataclass
class CropOptions:
    white_threshold: int = 245
    depth_threshold: int = 20
    min_size: int = 8
    black_threshold: int = BLACK_THRESHOLD   # 整条灰度 ≤ 它算"纯黑空白"；-1 = 不删黑（旧行为）
    min_blank_run: int = MIN_BLANK_RUN       # 连续空白 ≥ 这么多像素才删；1 = 不保护细线


@dataclass
class Round:
    """一次 tick / 动作的结果（便于测试断言与日志）。"""

    action: str          # idle/skipped/collected/cropped/formatted/stitched/saved/error
    message: str = ""
    wrote_clipboard: bool = False


class ClipboardMonitor:
    def __init__(self, backend=None, *, image_mode: ImageMode = ImageMode.OFF,
                 text_enabled: bool = False, crop_options: CropOptions | None = None,
                 crop_before_stitch: bool = True, background=(255, 255, 255, 0),
                 self_write_grace: float = 0.9, process_on_enable: bool = True, log=None):
        self.backend = backend if backend is not None else Win32Clipboard()
        self.image_mode = image_mode
        self.text_enabled = text_enabled
        self.crop_options = crop_options or CropOptions()
        self.crop_before_stitch = crop_before_stitch
        self.background = background
        self.self_write_grace = self_write_grace
        self.process_on_enable = process_on_enable
        self.log = log

        self.queue: list[Shot] = []   # 已收集（已裁剪）的截图，等待拼接或分开保存
        self.last_message = "就绪"
        self.last_error = ""

        self._lock = threading.RLock()
        self._last_seq = None         # None = 还没建立基线
        self._ignore_until = 0.0
        self._last_logged = ""

    # ------------------------------------------------------------ 对外状态

    def pending(self) -> int:
        with self._lock:
            return len(self.queue)

    def shots(self) -> list[Shot]:
        with self._lock:
            return list(self.queue)

    def any_enabled(self) -> bool:
        """是否有任意功能开启（托盘据此点亮，不区分是哪一个）。"""
        return self.text_enabled or self.image_mode is not ImageMode.OFF

    def set_image_mode(self, mode: ImageMode) -> None:
        with self._lock:
            if mode is not self.image_mode:
                self.image_mode = mode
                self.last_message = f"图片模式：{mode.value}"

    def toggle_text(self) -> bool:
        with self._lock:
            self.text_enabled = not self.text_enabled
            return self.text_enabled

    def clear_queue(self) -> int:
        with self._lock:
            n = len(self.queue)
            self.queue.clear()
            return n

    # ------------------------------------------------------------ 单次轮询

    def tick(self, now: float | None = None, force: bool = False) -> Round:
        """轮询一次。

        force=True 时忽略"序列号没变"和"首次基线"，直接按当前开关处理剪贴板里现有的
        内容 —— 这就是"开启功能时恰好处理当前剪贴板一次"的实现。
        """
        now = time.time() if now is None else now

        seq = self.backend.sequence()
        if seq <= 0:
            return Round("idle", "暂时读不到剪贴板序列号")

        if not force:
            if self._last_seq is None:
                # 首次只建立基线：启动瞬间剪贴板里已有的内容不动它
                self._last_seq = seq
                return Round("idle", "已就绪（当前剪贴板内容不处理）")
            if seq == self._last_seq:
                return Round("idle", "")

        self._last_seq = seq

        if now < self._ignore_until:
            return Round("idle", "忽略（本程序刚写进去的内容）")

        kind = self.backend.kind()

        if kind is ClipKind.FILELIST:
            return Round("skipped", "剪贴板里是文件，未改动")
        if kind is ClipKind.TEXT:
            return self._handle_text()
        if kind is ClipKind.IMAGE:
            return self._handle_image(now)
        return Round("skipped", f"未处理的内容类型：{kind.value}")

    def safe_tick(self, now: float | None = None, force: bool = False) -> Round:
        """tick 的异常隔离版本：任何异常都不允许弄死监听循环。"""
        try:
            r = self.tick(now, force=force)
        except Exception as e:  # noqa: BLE001 - 这里就是要兜住一切
            self.last_error = f"{type(e).__name__}: {e}"
            return Round("error", f"本轮处理异常，已跳过（监听继续）：{self.last_error}")
        if r.message:
            self.last_message = r.message
        return r

    def startup_tick(self) -> Round:
        """启动后的第一次处理。

        process_on_enable=True（默认）：把启动时就已在剪贴板里的内容按当前开关处理一次；
        False：只建立基线，不干扰它。
        """
        if self.process_on_enable:
            return self.safe_tick(force=True)
        return self.safe_tick()

    def process_current_once(self) -> Round:
        """立刻处理一次当前剪贴板内容（不管序列号有没有变）。"""
        return self.safe_tick(force=True)

    def run_loop(self, stop_event: threading.Event, interval: float = 0.25) -> None:
        while not stop_event.is_set():
            time.sleep(interval)
            self.emit(self.safe_tick())

    def emit(self, r: Round) -> Round:
        """统一日志出口（重复消息只打一次，避免刷屏）。"""
        if self.log and r.message and r.message != self._last_logged:
            self._last_logged = r.message
            self.log(f"[{r.action}] {r.message}")
        return r

    # ------------------------------------------------------------ 文本

    def _handle_text(self) -> Round:
        if not self.text_enabled:
            return Round("skipped", "是文本（公式格式化未开启），未改动剪贴板")

        text = self.backend.read_text()
        if not text:
            return Round("skipped", "剪贴板文本为空，未改动")

        if not has_formula_markers(text):
            return Round("skipped", "文本没有公式标记，未改动剪贴板")

        formatted = format_math_text(text)
        if formatted == text:
            return Round("skipped", "文本无需格式化，未改动剪贴板")

        ok = self.backend.write_text(formatted)
        if ok:
            self._mark_self_write()
            return Round("formatted", f"公式已格式化（{len(text)} → {len(formatted)} 字符）", True)
        return Round("error", "公式格式化失败：写回剪贴板被占用")

    # ------------------------------------------------------------ 图片

    def _handle_image(self, now: float) -> Round:
        if self.image_mode is ImageMode.OFF:
            return Round("skipped", "是图片（图片功能已关闭），未改动剪贴板")

        original = self.backend.read_image()
        if original is None:
            return Round("skipped", "拿不到图片数据，未改动剪贴板")

        processed = original
        if self.image_mode is ImageMode.CROP or self.crop_before_stitch:
            processed = process_page_image(
                original,
                white_threshold=self.crop_options.white_threshold,
                depth_threshold=self.crop_options.depth_threshold,
                min_size=self.crop_options.min_size,
                black_threshold=self.crop_options.black_threshold,
                min_blank_run=self.crop_options.min_blank_run,
            )

        if self.image_mode is ImageMode.CROP:
            ok = self.backend.write_image(processed)
            if ok:
                self._mark_self_write()
                return Round("cropped",
                             f"已裁剪 {original.width}x{original.height} → "
                             f"{processed.width}x{processed.height}", True)
            return Round("error", "裁剪完成，但写回剪贴板失败")

        # 拼接模式：只入队，绝不写剪贴板（这样文本复制完全不受影响）
        with self._lock:
            self.queue.append(Shot(processed, now))
            n = len(self.queue)
        return Round("collected",
                     f"已收集第 {n} 张（裁到 {processed.width}x{processed.height}），未改动剪贴板")

    # ------------------------------------------------------------ 完成拼接 / 分开保存

    def finish_stitch(self) -> Round:
        with self._lock:
            if not self.queue:
                return Round("skipped", "还没有收集到图片")
            shots = list(self.queue)
            self.queue.clear()

        images = [s.image for s in shots]
        result = stitch_vertical_centered(images, background=self.background)
        if result is None:
            self._restore(shots)
            return Round("error", "拼接失败：没有可用图片")

        ok = self.backend.write_image(result)
        if ok:
            self._mark_self_write()
            return Round("stitched",
                         f"已拼接 {len(images)} 张 → {result.width}x{result.height} 并写回剪贴板", True)

        self._restore(shots)  # 写回失败，图放回队列以便重试
        return Round("error", f"拼接完成（{result.width}x{result.height}）但写回失败，图片已留在队列里")

    def save_shots(self, folder) -> Round:
        """把收集到的截图按"日期-时分秒"分开保存到 folder（不碰剪贴板）。"""
        with self._lock:
            if not self.queue:
                return Round("skipped", "还没有收集到图片")
            shots = list(self.queue)
            self.queue.clear()

        try:
            paths = exporter.save_shots(shots, Path(folder))
        except Exception as e:  # noqa: BLE001 - 保存失败要把图还给用户
            self._restore(shots)
            return Round("error", f"保存失败（图片已留在队列里）：{type(e).__name__}: {e}")

        names = ", ".join(p.name for p in paths[:3])
        if len(paths) > 3:
            names += f" … 共 {len(paths)} 个"
        return Round("saved", f"已分开保存 {len(paths)} 张到 {folder}（{names}）")

    def _restore(self, shots) -> None:
        with self._lock:
            self.queue[:0] = shots

    # ------------------------------------------------------------ 内部

    def _mark_self_write(self) -> None:
        """本程序写回剪贴板之后：更新时间窗 + 立刻吃掉新序列号，避免自己触发自己。"""
        self._ignore_until = time.time() + self.self_write_grace
        try:
            seq = self.backend.sequence()
            if seq > 0:
                self._last_seq = seq
        except Exception:
            pass
