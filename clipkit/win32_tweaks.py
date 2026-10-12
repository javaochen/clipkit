"""pystray 在 Windows 上的两处兼容补丁（只修环境差异，不改功能语义）。

两处都做了"失败就退回原行为"的兜底，任何异常都不会让托盘起不来。

1. relax_message_filter()
   pystray 创建托盘窗口时会调 ChangeWindowMessageFilterEx（只为"资源管理器重启后
   还能收回图标"）。受限环境（沙箱 / 低权限令牌）返回拒绝访问，而 pystray 没有兜底，
   于是整个托盘启动失败。包一层：真实调用照做，只在被拒时降级。

2. install_topmost_menu(icon)
   右键菜单是窗口类 #32768 的弹出菜单。pystray 只调了 SetForegroundWindow 就
   TrackPopupMenuEx，菜单本身不是置顶窗口，因此会被开始菜单（Win 键）这类置顶
   窗口压住。这里把该图标实例的 WM_NOTIFY 处理包一层：右键时起一个短命看门狗线程，
   在菜单出现的一瞬间把**属于本进程**的 #32768 置顶，并在菜单结束后补一条 WM_NULL
   （MSDN 对 TrackPopupMenu 的配套要求）。

   注意 pystray 把消息处理绑在**实例**的 _message_handlers 字典里（__init__ 时捕获
   绑定方法），所以补丁必须打在实例上、而且在图标创建之后调用。
"""

from __future__ import annotations

import ctypes
import os
import threading
import time
from ctypes import wintypes

WM_NULL = 0x0000
WM_RBUTTONUP = 0x0205
MENU_WINDOW_CLASS = "#32768"        # Windows 弹出菜单的固定窗口类

HWND_TOPMOST = -1
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010

_user32_cache = None


def _user32():
    """自己的 user32 句柄：不污染 ctypes.windll.user32 上别人设的 argtypes。"""
    global _user32_cache
    if _user32_cache is None:
        handle = ctypes.WinDLL("user32", use_last_error=True)
        handle.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
        handle.FindWindowW.restype = wintypes.HWND
        handle.FindWindowExW.argtypes = [wintypes.HWND, wintypes.HWND,
                                         wintypes.LPCWSTR, wintypes.LPCWSTR]
        handle.FindWindowExW.restype = wintypes.HWND
        handle.GetWindowThreadProcessId.argtypes = [wintypes.HWND,
                                                    ctypes.POINTER(wintypes.DWORD)]
        handle.GetWindowThreadProcessId.restype = wintypes.DWORD
        handle.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int,
                                        ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                        ctypes.c_uint]
        handle.SetWindowPos.restype = wintypes.BOOL
        handle.PostMessageW.argtypes = [wintypes.HWND, ctypes.c_uint,
                                        wintypes.WPARAM, wintypes.LPARAM]
        handle.PostMessageW.restype = wintypes.BOOL
        _user32_cache = handle
    return _user32_cache


# ---------------------------------------------------------------- 补丁 1


def relax_message_filter() -> bool:
    """让 pystray 的 ChangeWindowMessageFilterEx 变成"失败也不致命"。"""
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


# ---------------------------------------------------------------- 补丁 2


def _find_our_menu(user32, pid: int):
    """找到属于本进程的弹出菜单窗口（可能同时存在别的程序的菜单）。"""
    hwnd = user32.FindWindowW(MENU_WINDOW_CLASS, None)
    while hwnd:
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid:
            return hwnd
        hwnd = user32.FindWindowExW(None, hwnd, MENU_WINDOW_CLASS, None)
    return None


def _raise_menu_when_shown(timeout: float = 2.0, interval: float = 0.02) -> bool:
    """短命看门狗：菜单一出现就置顶，然后退出。"""
    user32 = _user32()
    pid = os.getpid()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        hwnd = _find_our_menu(user32, pid)
        if hwnd:
            user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0,
                                SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
            return True
        time.sleep(interval)
    return False


def install_topmost_menu(icon) -> bool:
    """把该 icon 实例的右键菜单处理换成"弹出来就置顶"的版本。"""
    handlers = getattr(icon, "_message_handlers", None)
    if not isinstance(handlers, dict):
        return False

    key = None
    original = None
    for candidate_key, handler in handlers.items():
        func = getattr(handler, "__func__", None)
        if func is not None and func.__name__ == "_on_notify":
            key, original = candidate_key, handler
            break
    if original is None or key is None:
        return False

    def patched(wparam, lparam):
        if lparam == WM_RBUTTONUP:
            threading.Thread(target=_raise_menu_when_shown, daemon=True).start()
        try:
            return original(wparam, lparam)
        finally:
            if lparam == WM_RBUTTONUP:
                hwnd = getattr(icon, "_hwnd", None)
                if hwnd:
                    try:
                        _user32().PostMessageW(hwnd, WM_NULL, 0, 0)
                    except Exception:
                        pass

    handlers[key] = patched
    return True


def install(icon=None) -> dict:
    """按需安装两个补丁，返回各自的安装结果（便于 doctor/日志）。"""
    result = {"message_filter": relax_message_filter(), "topmost_menu": False}
    if icon is not None:
        result["topmost_menu"] = install_topmost_menu(icon)
    return result
