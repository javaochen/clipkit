"""托盘界面的冒烟测试：不启动真实托盘图标，只验证菜单/回调/图标构造可用。

真实托盘没法在无人值守环境里跑，但 pystray 的菜单项在构造时就会校验回调，
所以把菜单建出来、把每个动作调一遍，能挡住绝大部分"点一下就崩"的问题。
"""

from __future__ import annotations

import os
import unittest
from unittest import mock

try:
    import pystray  # noqa: F401
    HAS_PYSTRAY = True
except ImportError:  # pragma: no cover
    HAS_PYSTRAY = False

from clipkit.monitor import ClipboardMonitor, ImageMode
from tests import support
from tests.support import FakeClipboard, block, colored


@unittest.skipUnless(HAS_PYSTRAY, "未安装 pystray")
class TraySmokeTests(unittest.TestCase):
    def make_app(self, **monitor_kwargs):
        from clipkit.tray import TrayApp

        backend = FakeClipboard()
        monitor = ClipboardMonitor(backend, log=None, **monitor_kwargs)
        monitor.tick()
        return backend, monitor, TrayApp(monitor, log=None, notify=False)

    def test_menu_builds(self):
        _, _, app = self.make_app()
        menu = app._menu()
        self.assertIsNotNone(menu)
        self.assertIsInstance(app._primary_label(), str)

    def test_all_menu_actions_run(self):
        _, monitor, app = self.make_app()
        app._set_stitch()
        self.assertIs(monitor.image_mode, ImageMode.STITCH)

        on = monitor.text_enabled
        app._toggle_text()
        self.assertIs(monitor.text_enabled, not on)

        before = monitor.crop_before_stitch
        app._toggle_crop_before()
        self.assertIs(monitor.crop_before_stitch, not before)

        app._set_crop()
        self.assertIs(monitor.image_mode, ImageMode.CROP)
        app._set_off()
        self.assertIs(monitor.image_mode, ImageMode.OFF)

        app._finish()          # 空队列 → skipped，不应抛异常
        app._clear()
        app._primary()         # 关闭态 → 切到即时裁剪
        self.assertIs(monitor.image_mode, ImageMode.CROP)

    def test_left_click_finishes_stitch(self):
        backend, monitor, app = self.make_app()
        app._set_stitch()
        monitor.crop_before_stitch = False

        backend.user_copy_image(colored(10, 5, (255, 0, 0)))
        monitor.tick()
        backend.user_copy_image(colored(20, 6, (0, 255, 0)))
        monitor.tick()
        self.assertEqual(monitor.pending(), 2)

        app._primary()         # 拼接模式下左键 = 完成拼接
        self.assertEqual(monitor.pending(), 0)
        self.assertEqual(backend.writes, [("image", (20, 11))])

    def test_icon_and_title_reflect_state(self):
        from clipkit.tray import MODE_TEXT

        _, monitor, app = self.make_app()
        for mode in (ImageMode.OFF, ImageMode.CROP, ImageMode.STITCH):
            monitor.set_image_mode(mode)
            img = app._icon_image()
            self.assertEqual(img.size, (64, 64))
            title = app._title()
            self.assertIn("clipkit", title)
            self.assertIn(MODE_TEXT[mode], title)

    def test_title_shows_collected_count_only_when_nonzero(self):
        backend, monitor, app = self.make_app(image_mode=ImageMode.STITCH,
                                              crop_before_stitch=False)
        self.assertNotIn("已收集", app._title())

        backend.user_copy_image(colored(8, 4, (1, 2, 3)))
        monitor.tick()
        self.assertIn("已收集 1 张", app._title())

    def test_refresh_without_icon_is_noop(self):
        _, _, app = self.make_app()
        app.icon = None
        app._refresh(force=True)       # 不应抛异常

    def test_quit_sets_stop(self):
        _, _, app = self.make_app()
        app.icon = None
        app._quit()
        self.assertTrue(app._stop.is_set())

    def test_run_reports_tray_failure_instead_of_traceback(self):
        """托盘创建失败时要以退出码 1 + 人话结束，而不是抛 traceback 或悄悄活着。

        真实场景：受限环境（沙箱 / 低权限令牌）下 pystray 的
        ChangeWindowMessageFilterEx 会被拒绝访问——旧写法会让主线程带着
        traceback 退出，而监听守护线程还在跑，进程既不干净退出、调用方也
        看不出启动失败。
        """
        from unittest import mock

        import clipkit.tray as tray_mod

        _, _, app = self.make_app()
        logs = []
        app.log = logs.append
        app.interval = 0.05

        class BoomIcon:
            def __init__(self, *args, **kwargs):
                self.icon = None
                self.title = ""

            def run(self, *args, **kwargs):
                raise PermissionError("[WinError 5] 拒绝访问")

            def update_menu(self):
                pass

            def notify(self, *args, **kwargs):
                pass

            def stop(self):
                pass

        with mock.patch.object(tray_mod.pystray, "Icon", BoomIcon):
            rc = app.run()

        self.assertEqual(rc, 1)
        self.assertTrue(any("托盘启动失败" in m for m in logs), logs)
        self.assertTrue(app._stop.is_set(), "必须让监听线程停下来")


@unittest.skipUnless(HAS_PYSTRAY, "未安装 pystray")
class TrayFeatureTests(unittest.TestCase):
    """新增行为：点亮规则、开启时处理当前剪贴板、分开保存、参数持久化。"""

    def make_app(self, **monitor_kwargs):
        from clipkit.tray import TrayApp

        backend = FakeClipboard()
        monitor = ClipboardMonitor(backend, log=None, **monitor_kwargs)
        monitor.tick()
        return backend, monitor, TrayApp(monitor, log=None, notify=False)

    def test_icon_is_lit_when_only_text_formatting_is_on(self):
        from clipkit.tray import IDLE_COLOR, LIT_COLOR

        _, monitor, app = self.make_app()
        self.assertEqual(app._icon_image().getpixel((12, 12)), IDLE_COLOR)

        monitor.text_enabled = True          # 只开公式格式化也要亮
        self.assertEqual(app._icon_image().getpixel((12, 12)), LIT_COLOR)

    def test_icon_is_lit_for_image_modes(self):
        from clipkit.tray import LIT_COLOR

        for mode in (ImageMode.CROP, ImageMode.STITCH):
            _, _, app = self.make_app(image_mode=mode)
            self.assertEqual(app._icon_image().getpixel((12, 12)), LIT_COLOR)

    def test_enabling_crop_processes_current_clipboard_once(self):
        backend, _, app = self.make_app(process_on_enable=True)
        backend.user_copy_image(block(200, 150, (40, 30, 160, 120)))

        app._set_crop()
        self.assertEqual(backend.writes, [("image", (121, 91))])

    def test_enabling_crop_leaves_clipboard_alone_when_flag_off(self):
        backend, _, app = self.make_app(process_on_enable=False)
        backend.user_copy_image(block(200, 150, (40, 30, 160, 120)))

        app._set_crop()
        self.assertEqual(backend.writes, [])

    def test_enabling_text_formatting_processes_current_clipboard_once(self):
        backend, _, app = self.make_app(process_on_enable=True)
        backend.user_copy_text(r"\(x^2\)")

        app._toggle_text()
        self.assertEqual(backend.writes, [("text", "$x^2$")])

    def test_save_split_writes_files_to_app_folder(self):
        backend, monitor, app = self.make_app(image_mode=ImageMode.STITCH,
                                              crop_before_stitch=False)
        with support.temp_dir() as d:
            app.save_dir = d
            for img in (colored(10, 5, (255, 0, 0)), colored(20, 6, (0, 255, 0))):
                backend.user_copy_image(img)
                monitor.tick()

            app._save_split()
            self.assertEqual(len(list(d.glob("*.png"))), 2)
            self.assertEqual(monitor.pending(), 0)

    def test_toggle_process_on_enable_persists_to_config(self):
        from clipkit import config

        with support.temp_dir() as d, mock.patch.dict(
                os.environ, {"CLIPKIT_CONFIG": str(d / "cfg.json")}):
            _, monitor, app = self.make_app(process_on_enable=True)

            app._toggle_process_on_enable()
            self.assertFalse(monitor.process_on_enable)
            self.assertFalse(config.load()["process_on_enable"])

            app._toggle_process_on_enable()
            self.assertTrue(config.load()["process_on_enable"])

    def test_topmost_menu_patch_installs_on_real_icon(self):
        """右键菜单置顶补丁要能挂上（pystray 把消息处理绑在实例上）。"""
        import pystray

        from clipkit import win32_tweaks

        icon = pystray.Icon("clipkit-test", support.blank(16, 16), "test", pystray.Menu())
        self.assertTrue(win32_tweaks.install_topmost_menu(icon))
        # 第二次应识别为"已打过"，不重复包
        self.assertFalse(win32_tweaks.install_topmost_menu(icon))

    def test_menu_builds_with_new_items(self):
        with support.temp_dir() as d, mock.patch.dict(
                os.environ, {"CLIPKIT_CONFIG": str(d / "cfg.json")}):
            _, _, app = self.make_app()
            self.assertIsNotNone(app._menu())
            app._toggle_crop_before()      # 菜单里的其它回调仍可正常调用
            app._clear()
            app._save_split()              # 空队列 → skipped，不抛异常
            app._refresh(force=True)


if __name__ == "__main__":
    unittest.main()
