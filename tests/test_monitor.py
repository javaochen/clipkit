"""状态机测试：三条需求逐条落成断言。

* 拼接模式：收集期间一次都不写剪贴板，完成时才写一次
* 图片监听不影响文本复制：文本/文件列表路径绝不改剪贴板、也不去读图
* 异常/自写回环不会把监听弄死或陷入死循环

全部用内存假剪贴板，不碰真实剪贴板、不起托盘。
"""

from __future__ import annotations

import unittest

from clipkit.clipboard import ClipKind
from clipkit.monitor import ClipboardMonitor, ImageMode
from tests import support
from tests.support import FakeClipboard, block, blocks_1280x720, colored


class MonitorTestBase(unittest.TestCase):
    def make(self, **kwargs):
        """建一个已建立基线的 monitor（启动时不处理剪贴板里已有的内容）。"""
        backend = FakeClipboard()
        monitor = ClipboardMonitor(backend, log=None, **kwargs)
        monitor.tick()
        return backend, monitor


# ---------------------------------------------------------------- 启动


class BaselineTests(unittest.TestCase):
    def test_first_tick_only_baselines(self):
        backend = FakeClipboard()
        backend.user_copy_image(blocks_1280x720())      # 启动前剪贴板里就有的图
        monitor = ClipboardMonitor(backend, image_mode=ImageMode.STITCH, log=None)

        r = monitor.tick()
        self.assertEqual(r.action, "idle")
        self.assertEqual(monitor.pending(), 0)
        self.assertEqual(backend.writes, [])

    def test_unreadable_sequence_is_reported(self):
        backend = FakeClipboard()
        backend._seq = 0
        monitor = ClipboardMonitor(backend, log=None)
        self.assertIn("序列号", monitor.tick().message)


# ---------------------------------------------------------------- 文本安全


class TextSafetyTests(MonitorTestBase):
    def test_text_copy_untouched_when_image_mode_on(self):
        backend, monitor = self.make(image_mode=ImageMode.STITCH)
        backend.user_copy_text("这是一段普通文字，不是截图")

        r = monitor.tick()
        self.assertEqual(backend.writes, [])
        self.assertNotIn("image", backend.reads)     # 根本不去抓图
        self.assertNotIn("text", backend.reads)      # 也没读文本
        self.assertFalse(r.wrote_clipboard)

    def test_text_copy_untouched_in_crop_mode(self):
        backend, monitor = self.make(image_mode=ImageMode.CROP)
        backend.user_copy_text("普通文字")
        monitor.tick()
        self.assertEqual(backend.writes, [])
        self.assertNotIn("image", backend.reads)

    def test_filelist_is_ignored(self):
        backend, monitor = self.make(image_mode=ImageMode.STITCH)
        backend.user_copy_files()

        r = monitor.tick()
        self.assertEqual(r.action, "skipped")
        self.assertEqual(backend.writes, [])
        self.assertNotIn("image", backend.reads)

    def test_text_without_markers_is_not_rewritten(self):
        backend, monitor = self.make(image_mode=ImageMode.OFF, text_enabled=True)
        backend.user_copy_text("普通文字，没有公式")
        monitor.tick()
        self.assertEqual(backend.writes, [])

    def test_formula_text_is_formatted_exactly_once(self):
        backend, monitor = self.make(image_mode=ImageMode.STITCH, text_enabled=True)
        backend.user_copy_text(r"\(x^2\)")

        r = monitor.tick()
        self.assertTrue(r.wrote_clipboard)
        self.assertEqual(backend.writes, [("text", "$x^2$")])
        self.assertEqual(monitor.pending(), 0)       # 文本不会混进拼接队列

    def test_text_copy_with_formatting_off_leaves_clipboard_alone(self):
        backend, monitor = self.make(image_mode=ImageMode.OFF, text_enabled=False)
        backend.user_copy_text(r"\[a+b\]")
        monitor.tick()
        self.assertEqual(backend.writes, [])


# ---------------------------------------------------------------- 图片


class ImageModeTests(MonitorTestBase):
    def test_off_mode_never_reads_image(self):
        backend, monitor = self.make(image_mode=ImageMode.OFF)
        backend.user_copy_image(blocks_1280x720())

        r = monitor.tick()
        self.assertEqual(r.action, "skipped")
        self.assertEqual(backend.writes, [])
        self.assertNotIn("image", backend.reads)

    def test_crop_mode_writes_back_once(self):
        backend, monitor = self.make(image_mode=ImageMode.CROP)
        backend.user_copy_image(block(200, 150, (40, 30, 160, 120)))

        r = monitor.tick()
        self.assertTrue(r.wrote_clipboard)
        self.assertEqual(backend.writes, [("image", (121, 91))])

    def test_crop_mode_does_not_reprocess_its_own_result(self):
        backend, monitor = self.make(image_mode=ImageMode.CROP)
        backend.user_copy_image(block(200, 150, (40, 30, 160, 120)))
        monitor.tick()

        r = monitor.tick()
        self.assertEqual(len(backend.writes), 1)
        self.assertEqual(r.action, "idle")

    def test_stitch_collects_without_writing_during_session(self):
        backend, monitor = self.make(image_mode=ImageMode.STITCH, crop_before_stitch=False)
        for img in (colored(10, 5, (255, 0, 0)),
                    colored(20, 6, (0, 255, 0)),
                    colored(30, 7, (0, 0, 255))):
            backend.user_copy_image(img)
            self.assertEqual(monitor.tick().action, "collected")

        self.assertEqual(backend.writes, [])         # 收集期间一次都没写剪贴板
        self.assertEqual(monitor.pending(), 3)

    def test_finish_stitch_writes_once_and_clears(self):
        backend, monitor = self.make(image_mode=ImageMode.STITCH, crop_before_stitch=False)
        for img in (colored(10, 5, (255, 0, 0)),
                    colored(20, 6, (0, 255, 0)),
                    colored(30, 7, (0, 0, 255))):
            backend.user_copy_image(img)
            monitor.tick()

        r = monitor.finish_stitch()
        self.assertTrue(r.wrote_clipboard)
        self.assertEqual(backend.writes, [("image", (30, 18))])
        self.assertEqual(monitor.pending(), 0)

    def test_stitch_crops_each_image_before_collecting(self):
        backend, monitor = self.make(image_mode=ImageMode.STITCH)   # 默认开逐张裁剪
        backend.user_copy_image(block(200, 150, (40, 30, 160, 120)))
        monitor.tick()
        monitor.finish_stitch()
        self.assertEqual(backend.writes, [("image", (121, 91))])

    def test_finish_with_empty_queue_is_skipped(self):
        backend, monitor = self.make(image_mode=ImageMode.STITCH)
        r = monitor.finish_stitch()
        self.assertEqual(r.action, "skipped")
        self.assertEqual(backend.writes, [])

    def test_queue_kept_when_write_fails(self):
        backend, monitor = self.make(image_mode=ImageMode.STITCH, crop_before_stitch=False)
        backend.user_copy_image(colored(10, 5, (255, 0, 0)))
        monitor.tick()
        backend.user_copy_image(colored(20, 6, (0, 255, 0)))
        monitor.tick()

        backend.fail_write = True
        r = monitor.finish_stitch()
        self.assertFalse(r.wrote_clipboard)
        self.assertEqual(monitor.pending(), 2)       # 图没丢，可以重试

    def test_clear_queue(self):
        backend, monitor = self.make(image_mode=ImageMode.STITCH, crop_before_stitch=False)
        backend.user_copy_image(colored(10, 5, (255, 0, 0)))
        monitor.tick()
        self.assertEqual(monitor.clear_queue(), 1)
        self.assertEqual(monitor.pending(), 0)

    def test_image_kind_without_data_is_skipped(self):
        backend, monitor = self.make(image_mode=ImageMode.CROP)
        backend._seq += 1
        backend._kind = ClipKind.IMAGE
        backend._image = None

        r = monitor.safe_tick()
        self.assertEqual(r.action, "skipped")
        self.assertEqual(backend.writes, [])


# ---------------------------------------------------------------- 健壮性


class RobustnessTests(MonitorTestBase):
    def test_exception_does_not_kill_the_loop(self):
        """旧版 bug：复制文件时读图抛异常，监听线程直接死掉。"""
        backend, monitor = self.make(image_mode=ImageMode.CROP)
        backend.raise_on_read_image = True
        backend.user_copy_image(block(80, 60, (10, 10, 70, 50)))

        r = monitor.safe_tick()
        self.assertEqual(r.action, "error")
        self.assertIn("boom", monitor.last_error)

        # 循环还活着：下一张正常处理
        backend.raise_on_read_image = False
        backend.user_copy_image(block(80, 60, (10, 10, 70, 50)))
        self.assertTrue(monitor.safe_tick().wrote_clipboard)

    def test_grace_window_suppresses_immediate_echo(self):
        backend, monitor = self.make(image_mode=ImageMode.CROP, self_write_grace=100)
        backend.user_copy_image(block(200, 150, (40, 30, 160, 120)))
        monitor.tick()
        self.assertEqual(len(backend.writes), 1)

        backend.user_copy_image(block(200, 150, (40, 30, 160, 120)))
        r = monitor.tick()
        self.assertEqual(r.action, "idle")
        self.assertEqual(len(backend.writes), 1)

    def test_self_write_not_recollected_without_grace(self):
        """自写回环的结构性保护：写回后立刻吃掉新序列号。"""
        backend, monitor = self.make(image_mode=ImageMode.STITCH,
                                     crop_before_stitch=False, self_write_grace=0)
        backend.user_copy_image(colored(10, 5, (255, 0, 0)))
        monitor.tick()
        monitor.finish_stitch()

        backend.user_copy_image(colored(10, 5, (255, 0, 0)))
        # 注意：这是"用户真的又复制了一张"，应当被收集（不是回环）
        self.assertEqual(monitor.tick().action, "collected")
        self.assertEqual(len(backend.writes), 1)


# ---------------------------------------------------------------- 开启功能时处理当前剪贴板


class ProcessOnEnableTests(unittest.TestCase):
    """参数 process_on_enable：True（默认）时开启功能会恰好处理当前剪贴板一次。"""

    def test_process_current_once_crops_what_is_already_there(self):
        backend = FakeClipboard()
        backend.user_copy_image(block(200, 150, (40, 30, 160, 120)))
        monitor = ClipboardMonitor(backend, image_mode=ImageMode.CROP, log=None)

        r = monitor.process_current_once()
        self.assertTrue(r.wrote_clipboard)
        self.assertEqual(backend.writes, [("image", (121, 91))])

    def test_process_current_once_formats_text_already_there(self):
        backend = FakeClipboard()
        backend.user_copy_text(r"\(x^2\)")
        monitor = ClipboardMonitor(backend, text_enabled=True, log=None)

        self.assertTrue(monitor.process_current_once().wrote_clipboard)
        self.assertEqual(backend.writes, [("text", "$x^2$")])

    def test_process_current_once_does_nothing_when_all_features_off(self):
        backend = FakeClipboard()
        backend.user_copy_text(r"\(x^2\)")
        monitor = ClipboardMonitor(backend, log=None)

        r = monitor.process_current_once()
        self.assertEqual(r.action, "skipped")
        self.assertEqual(backend.writes, [])

    def test_startup_tick_processes_when_flag_on(self):
        backend = FakeClipboard()
        backend.user_copy_image(block(200, 150, (40, 30, 160, 120)))
        monitor = ClipboardMonitor(backend, image_mode=ImageMode.CROP,
                                   process_on_enable=True, log=None)

        self.assertTrue(monitor.startup_tick().wrote_clipboard)

    def test_startup_tick_keeps_baseline_when_flag_off(self):
        backend = FakeClipboard()
        backend.user_copy_image(block(200, 150, (40, 30, 160, 120)))
        monitor = ClipboardMonitor(backend, image_mode=ImageMode.CROP,
                                   process_on_enable=False, log=None)

        r = monitor.startup_tick()
        self.assertEqual(r.action, "idle")
        self.assertEqual(backend.writes, [])

        # 但基线已建立：之后"又复制了内容"（序列号变化）仍会正常处理
        backend.user_copy_image(block(200, 150, (40, 30, 160, 120)))
        self.assertTrue(monitor.safe_tick().wrote_clipboard)

    def test_forced_pass_still_respects_self_write_grace(self):
        backend = FakeClipboard()
        monitor = ClipboardMonitor(backend, image_mode=ImageMode.CROP,
                                   self_write_grace=100, log=None)
        monitor.tick()                       # 先建立基线
        backend.user_copy_image(block(200, 150, (40, 30, 160, 120)))
        monitor.tick()                       # 正常处理并写回

        r = monitor.process_current_once()   # 窗口内再强制一次 → 被自写保护挡住
        self.assertEqual(r.action, "idle")
        self.assertEqual(len(backend.writes), 1)


class SaveSplitTests(unittest.TestCase):
    """分开保存：逐张写文件、不碰剪贴板、失败不丢图。"""

    def test_saves_collected_shots_and_clears_queue(self):
        backend = FakeClipboard()
        monitor = ClipboardMonitor(backend, image_mode=ImageMode.STITCH,
                                   crop_before_stitch=False, log=None)
        monitor.tick()
        for img in (colored(10, 5, (255, 0, 0)), colored(20, 6, (0, 255, 0))):
            backend.user_copy_image(img)
            monitor.tick()

        with support.temp_dir() as d:
            r = monitor.save_shots(d)
            self.assertEqual(r.action, "saved")
            self.assertEqual(len(list(d.glob("*.png"))), 2)
            self.assertEqual(monitor.pending(), 0)

        self.assertEqual(backend.writes, [])      # 保存过程完全不碰剪贴板

    def test_queue_kept_when_folder_cannot_be_created(self):
        backend = FakeClipboard()
        monitor = ClipboardMonitor(backend, image_mode=ImageMode.STITCH, log=None)
        monitor.tick()
        backend.user_copy_image(colored(10, 5, (255, 0, 0)))
        monitor.tick()

        with support.temp_dir() as d:
            blocker = d / "not_a_dir"
            blocker.write_text("x", encoding="utf-8")   # 父路径是文件 → mkdir 会失败
            r = monitor.save_shots(blocker / "shots")
            self.assertEqual(r.action, "error")
            self.assertEqual(monitor.pending(), 1)      # 图还在队列里

    def test_save_with_empty_queue_is_skipped(self):
        backend = FakeClipboard()
        monitor = ClipboardMonitor(backend, log=None)
        with support.temp_dir() as d:
            self.assertEqual(monitor.save_shots(d).action, "skipped")

    def test_any_enabled_reflects_text_only(self):
        backend = FakeClipboard()
        monitor = ClipboardMonitor(backend, log=None)
        self.assertFalse(monitor.any_enabled())
        monitor.text_enabled = True
        self.assertTrue(monitor.any_enabled())

    def test_any_enabled_reflects_image_modes(self):
        backend = FakeClipboard()
        for mode in (ImageMode.CROP, ImageMode.STITCH):
            monitor = ClipboardMonitor(backend, image_mode=mode, log=None)
            self.assertTrue(monitor.any_enabled())


# ---------------------------------------------------------------- 默认全关


class DefaultOffTests(unittest.TestCase):
    """启动时默认不开启任何功能：不裁剪、不格式化、也不动剪贴板里已有的内容。"""

    def test_default_monitor_has_no_feature_enabled(self):
        backend = FakeClipboard()
        monitor = ClipboardMonitor(backend, log=None)
        self.assertIs(monitor.image_mode, ImageMode.OFF)
        self.assertFalse(monitor.text_enabled)
        self.assertFalse(monitor.any_enabled())

    def test_default_startup_leaves_existing_image_alone(self):
        backend = FakeClipboard()
        backend.user_copy_image(block(200, 150, (40, 30, 160, 120)))
        monitor = ClipboardMonitor(backend, log=None)   # 默认全关（含 process_on_enable=True）

        r = monitor.startup_tick()
        self.assertEqual(backend.writes, [])
        self.assertEqual(monitor.pending(), 0)
        self.assertEqual(r.action, "skipped")

    def test_default_startup_leaves_existing_text_alone(self):
        backend = FakeClipboard()
        backend.user_copy_text(r"\(x^2\)")
        monitor = ClipboardMonitor(backend, log=None)

        monitor.startup_tick()
        self.assertEqual(backend.writes, [])


if __name__ == "__main__":
    unittest.main()
