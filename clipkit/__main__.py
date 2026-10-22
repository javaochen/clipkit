"""命令行入口。

    python -m clipkit                    启动托盘（默认图片功能关闭，不会动剪贴板）
    python -m clipkit stitch             启动并直接进入拼接模式
    python -m clipkit crop               启动并直接进入即时裁剪
    python -m clipkit simulate <目录>     离线：逐张裁剪后拼接，完全不碰剪贴板
    python -m clipkit simulate <目录> --split   离线：逐张裁剪后**分开保存**（文件名用图片的修改时间）
    python -m clipkit doctor             环境自检

常用选项：
    --save-dir DIR              拼接模式下"分开保存"的目标文件夹（本次运行有效；
                                托盘菜单里「选择保存文件夹…」选的会被记住）
    --process-on-enable         True（默认，可从配置改）：开启裁剪 / 公式格式化时，
                                把当前剪贴板内容恰好处理一次
    --no-process-on-enable      False：不干扰之前的剪贴板内容，只处理之后的新内容

退出码：0 正常；1 失败；2 用法错误。
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from PIL import Image

from . import __version__, config, exporter
from .clipboard import Win32Clipboard
from .exporter import Shot
from .imaging import (BLACK_THRESHOLD, MIN_BLANK_RUN, REFINE_PASSES,
                      process_page_image, stitch_image_files)
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
    p.add_argument("--split", action="store_true",
                   help="simulate：逐张分开保存（此时 -o 视为输出文件夹）")
    p.add_argument("--black-threshold", type=int, default=BLACK_THRESHOLD, metavar="N",
                   help=f"simulate/托盘：整条灰度 ≤ N 视为纯黑空白裁掉（默认 {BLACK_THRESHOLD}；-1 = 不裁黑）")
    p.add_argument("--min-blank-run", type=int, default=MIN_BLANK_RUN, metavar="N",
                   help=f"simulate/托盘：连续空白 ≥ N 像素才裁，更细的留作分隔线（默认 {MIN_BLANK_RUN}）")
    p.add_argument("--refine-passes", type=int, default=REFINE_PASSES, metavar="N",
                   help=f"simulate/托盘：行/列过滤最多扫几趟（默认 {REFINE_PASSES}；1 = 旧实现的单趟）")
    p.add_argument("--text", action="store_true", help="启动时开启文本公式格式化")
    p.add_argument("--interval", type=float, default=0.25, help="剪贴板轮询间隔秒（默认 0.25）")
    p.add_argument("--no-crop-before-stitch", action="store_true", help="拼接前不逐张裁剪")
    p.add_argument("--no-notify", action="store_true", help="关掉托盘气泡提示")
    p.add_argument("--save-dir", type=Path, default=None,
                   help="分开保存的目标文件夹（本次运行有效；菜单里选文件夹会记住）")
    p.add_argument("--process-on-enable", dest="process_on_enable", action="store_true", default=None,
                   help="开启功能时先处理一次当前剪贴板（默认读配置，配置默认开）")
    p.add_argument("--no-process-on-enable", dest="process_on_enable", action="store_false",
                   help="不干扰剪贴板里已有的内容，只处理之后的新内容")
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

    settings = config.load()
    log(f"  配置文件  : {config.config_path()}")
    log(f"  保存文件夹: {config.save_dir()}")
    log(f"  开启时先处理当前剪贴板: {'开' if settings['process_on_enable'] else '关'}")
    if os.name == "nt":
        try:
            from . import win32_tweaks

            patches = win32_tweaks.install()
            log(f"  Windows 兼容补丁: {patches}")
        except Exception as e:  # noqa: BLE001
            log(f"  Windows 兼容补丁: 不可用（{type(e).__name__}）")

    if ok:
        log("  结论      : 环境正常，可以 python -m clipkit stitch 试用")
    else:
        log("  结论      : 有缺失，pip install -r requirements.txt")
    return 0 if ok else 1


# ---------------------------------------------------------------- simulate


def cmd_simulate(paths, out: Path | None, crop: bool, split: bool = False,
                 crop_kwargs: dict | None = None) -> int:
    if not paths:
        log("[!] 用法：python -m clipkit simulate <图片目录> [-o 输出.png] [--split]")
        return 2

    crop_kwargs = dict(crop_kwargs or {})

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

    if split:
        return _simulate_split(files, target, out, crop, crop_kwargs)

    log(f"离线拼接 {len(files)} 张（逐张裁剪={'开' if crop else '关'}），不接触剪贴板")
    for p in files:
        log(f"  - {p.name}")

    result = stitch_image_files(files, crop=crop, **crop_kwargs)
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


def _simulate_split(files, target: Path, out: Path | None, crop: bool,
                    crop_kwargs: dict | None = None) -> int:
    """离线"分开保存"：文件名用每张图自己的修改时间（离线文件没有"拍摄时刻"）。"""
    folder = out if out is not None else (target if target.is_dir() else target.parent) / "_split"
    log(f"离线分开保存 {len(files)} 张（逐张裁剪={'开' if crop else '关'}）→ {folder}")

    shots = []
    for path in files:
        try:
            with Image.open(path) as im:
                image = im.convert("RGB")
        except Exception as e:  # noqa: BLE001
            log(f"  [!] 跳过 {path.name}：{type(e).__name__}: {e}")
            continue
        if crop:
            image = process_page_image(image, **(crop_kwargs or {}))
        shots.append(Shot(image, path.stat().st_mtime))

    if not shots:
        log("[!] 没有可保存的图片")
        return 1

    try:
        written = exporter.save_shots(shots, folder)
    except Exception as e:  # noqa: BLE001
        log(f"[!] 保存失败：{type(e).__name__}: {e}")
        return 1

    for path in written:
        log(f"  - {path.name}")
    log(f"  → 已分开保存 {len(written)} 张到 {folder}")
    return 0

def cmd_tray(mode: ImageMode, args) -> int:
    try:
        import pystray  # noqa: F401
    except ImportError:
        log("[!] 缺少 pystray：pip install -r requirements.txt")
        return 1

    from .tray import TrayApp

    settings = config.load()
    process_on_enable = (bool(settings["process_on_enable"]) if args.process_on_enable is None
                         else bool(args.process_on_enable))

    monitor = ClipboardMonitor(
        image_mode=mode,
        text_enabled=bool(args.text),
        crop_options=CropOptions(black_threshold=args.black_threshold,
                                 min_blank_run=args.min_blank_run,
                                 refine_passes=args.refine_passes),
        crop_before_stitch=not args.no_crop_before_stitch,
        process_on_enable=process_on_enable,
        log=log,
    )
    app = TrayApp(monitor, interval=args.interval, log=log, notify=not args.no_notify)
    if args.save_dir:
        app.save_dir = Path(args.save_dir)
    return app.run()


# ---------------------------------------------------------------- main


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "doctor":
        return cmd_doctor()
    if args.command == "simulate":
        crop_kwargs = {"black_threshold": args.black_threshold,
                       "min_blank_run": args.min_blank_run,
                       "refine_passes": args.refine_passes}
        return cmd_simulate(args.paths, args.out, crop=not args.no_crop,
                            split=args.split, crop_kwargs=crop_kwargs)

    mode = {"stitch": ImageMode.STITCH, "crop": ImageMode.CROP}.get(args.command, ImageMode.OFF)
    return cmd_tray(mode, args)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
