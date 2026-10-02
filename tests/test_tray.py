"""托盘界面的冒烟测试：不启动真实托盘图标，只验证菜单/回调/图标构造可用。

真实托盘没法在无人值守环境里跑，但 pystray 的菜单项在构造时就会校验回调，
所以把菜单建出来、把每个动作调一遍，能挡住绝大部分"点一下就崩"的问题。
"""

from __future__ import annotations

import unittest

try:
    import pystray  # noqa: F401
    HAS_PYSTRAY = True
except ImportError:  # pragma: no cover
    HAS_PYSTRAY = False

from clipkit.monitor import ClipboardMonitor, ImageMode
from tests.support import FakeClipboard, colored


@unittest.skipUnless(HAS_PYSTRAY, "未安装 pystray")
class TraySmokeTests(unittest.TestCase):
    def make_app(self):
        from clipkit.tray import TrayApp

        backend = FakeClipboard()
        monitor = ClipboardMonitor(backend, image_mode=ImageMode.OFF, log=None)
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
        _, monitor, app = self.make_app()
        for mode in (ImageMode.OFF, ImageMode.CROP, ImageMode.STITCH):
            monitor.set_image_mode(mode)
            img = app._icon_image()
            self.assertEqual(img.size, (64, 64))
            title = app._title()
            self.assertIn("clipkit", title)
            if mode is ImageMode.STITCH:      # 只有拼接模式才在标题里报收集张数
                self.assertIn("已收集", title)
                self.assertIn(str(monitor.pending()), title)

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


if __name__ == "__main__":
    unittest.main()
