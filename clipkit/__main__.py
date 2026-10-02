"""命令行入口。

    python -m clipkit                    启动托盘（默认图片功能关闭，不会动剪贴板）
    python -m clipkit stitch             启动并直接进入拼接模式
    python -m clipkit crop               启动并直接进入即时裁剪
    python -m clipkit simulate <目录>     离线：逐张裁剪后拼接，完全不碰剪贴板
    python -m clipkit doctor             环境自检

退出码：0 正常；1 失败；2 用法错误。
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from . import __version__
from .clipboard import Win32Clipboard
from .imaging import stitch_image_files
from .monitor import ClipboardMonitor, CropOptions, ImageMode

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".webp", ".tif", ".tiff", ".gif"}


def log(message: str) -> None:
    """立刻 flush：托盘通常没有控制台，输出会被 launcher 的 --log-dir 收走。"""
    print(message, flush=True)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="clipkit",
        description="剪贴板图片工具：拼接 / 裁剪 / 文本公式格式化",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例:\n"
            "  python -m clipkit stitch                 启动即进入拼接模式\n"
            "  python -m clipkit crop --text            即时裁剪 + 文本公式格式化\n"
            "  python -m clipkit simulate .\\shots -o all.png\n"
            "  python -m clipkit doctor\n"
        ),
    )
    p.add_argument("command", nargs="?", default="tray",
                   choices=["tray", "stitch", "crop", "simulate", "doctor"],
                   help="tray(默认) / stitch / crop / simulate / doctor")
    p.add_argument("paths", nargs="*", help="simulate：图片目录或单个图片")
    p.add_argument("-o", "--out", type=Path, default=None,
                   help="simulate：输出 PNG（默认 <目录>/_stitched.png）")
    p.add_argument("--no-crop", action="store_true", help="simulate：只拼接不裁剪")
    p.add_argument("--text", action="store_true", help="启动时开启文本公式格式化")
    p.add_argument("--interval", type=float, default=0.25, help="剪贴板轮询间隔秒（默认 0.25）")
    p.add_argument("--no-crop-before-stitch", action="store_true", help="拼接前不逐张裁剪")
    p.add_argument("--no-notify", action="store_true", help="关掉托盘气泡提示")
    p.add_argument("--version", action="version", version=f"clipkit {__version__}")
    return p


# ---------------------------------------------------------------- doctor


def cmd_doctor() -> int:
    log(f"clipkit {__version__} doctor")
    log(f"  python    : {sys.version.split()[0]}  ({sys.executable})")
    log(f"  platform  : {sys.platform} (os.name={os.name})")

    ok = True
    for mod in ("PIL", "numpy", "win32clipboard", "pystray"):
        try:
            m = __import__(mod)
            log(f"  依赖 {mod:<14}: OK {getattr(m, '__version__', '')}".rstrip())
        except Exception as e:  # noqa: BLE001
            ok = False
            log(f"  依赖 {mod:<14}: 缺失（{type(e).__name__}）")

    if os.name != "nt":
        log("  [!] 剪贴板与托盘功能只在 Windows 可用")
        return 1 if not ok else 0

    try:
        info = Win32Clipboard().probe()
        log(f"  剪贴板    : 序列号={info['sequence']}  当前类型={info['kind']}")
    except Exception as e:  # noqa: BLE001
        ok = False
        log(f"  剪贴板    : 读取失败 {type(e).__name__}: {e}")

    if ok:
        log("  结论      : 环境正常，可以 python -m clipkit stitch 试用")
    else:
        log("  结论      : 有缺失，pip install -r requirements.txt")
    return 0 if ok else 1


# ---------------------------------------------------------------- simulate


def cmd_simulate(paths, out: Path | None, crop: bool) -> int:
    if not paths:
        log("[!] 用法：python -m clipkit simulate <图片目录> [-o 输出.png]")
        return 2

    target = Path(paths[0])
    if target.is_dir():
        files = sorted(p for p in target.iterdir()
                       if p.is_file() and p.suffix.lower() in IMAGE_EXTS)
    elif target.is_file():
        files = [target]
    else:
        log(f"[!] 找不到：{target}")
        return 1

    if not files:
        log(f"[!] {target} 里没有可用图片")
        return 1

    log(f"离线拼接 {len(files)} 张（逐张裁剪={'开' if crop else '关'}），不接触剪贴板")
    for p in files:
        log(f"  - {p.name}")

    result = stitch_image_files(files, crop=crop)
    if result is None:
        log("[!] 拼接失败：没有可用图片")
        return 1

    if out is not None:
        out_path = out
    elif target.is_dir():
        out_path = target / "_stitched.png"
    else:
        out_path = target.with_name(target.stem + "_stitched.png")

    result.save(out_path)
    log(f"  → {result.width}x{result.height} 已保存：{out_path}")
    return 0


# ---------------------------------------------------------------- tray


def cmd_tray(mode: ImageMode, args) -> int:
    try:
        import pystray  # noqa: F401
    except ImportError:
        log("[!] 缺少 pystray：pip install -r requirements.txt")
        return 1

    from .tray import TrayApp

    monitor = ClipboardMonitor(
        image_mode=mode,
        text_enabled=bool(args.text),
        crop_options=CropOptions(),
        crop_before_stitch=not args.no_crop_before_stitch,
        log=log,
    )
    app = TrayApp(monitor, interval=args.interval, log=log, notify=not args.no_notify)
    return app.run()


# ---------------------------------------------------------------- main


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "doctor":
        return cmd_doctor()
    if args.command == "simulate":
        return cmd_simulate(args.paths, args.out, crop=not args.no_crop)

    mode = {"stitch": ImageMode.STITCH, "crop": ImageMode.CROP}.get(args.command, ImageMode.OFF)
    return cmd_tray(mode, args)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
