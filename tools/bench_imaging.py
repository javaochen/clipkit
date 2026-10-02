"""裁剪管线的耗时 / 峰值内存基准。

跑法（仓库根目录）：
    python tools/bench_imaging.py
    python tools/bench_imaging.py --compare "..\\clip2line_cut&$.py"   # 与旧实现 A/B

峰值内存用采样线程读进程 RSS：numpy 的数组不走 Python 分配器，tracemalloc 看不到它。
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
import threading
import time
from pathlib import Path

# 让 tools/ 下也能 import clipkit
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402

from clipkit import imaging  # noqa: E402

try:
    import psutil
except ImportError:  # pragma: no cover
    psutil = None


def make_image(w: int, h: int) -> Image.Image:
    """与 tests.support.blocks_1280x720 同构：四块内容 + 全宽空白横带 + 全高空白竖带。"""
    img = Image.new("RGB", (w, h), (255, 255, 255))
    d = ImageDraw.Draw(img)
    sx, sy = w / 1280.0, h / 720.0
    for (x0, y0, x1, y1) in [(60, 50, 500, 300), (60, 380, 500, 640),
                             (640, 50, 1200, 300), (640, 380, 1200, 640)]:
        d.rectangle((x0 * sx, y0 * sy, x1 * sx, y1 * sy), fill=(20, 20, 20))
    return img


def measure(fn, repeats: int = 3):
    """返回 (最佳耗时秒, 峰值增量MB 或 None)。"""
    if psutil is None:
        best = min(_timeit(fn) for _ in range(repeats))
        return best, None

    proc = psutil.Process()
    base = proc.memory_info().rss
    peak = [base]
    stop = threading.Event()

    def sample():
        while not stop.is_set():
            peak[0] = max(peak[0], proc.memory_info().rss)
            time.sleep(0.002)

    th = threading.Thread(target=sample, daemon=True)
    th.start()
    try:
        best = min(_timeit(fn) for _ in range(repeats))
    finally:
        stop.set()
        th.join(timeout=1)
    return best, (peak[0] - base) / 1048576.0


def _timeit(fn) -> float:
    t0 = time.perf_counter()
    fn()
    return time.perf_counter() - t0


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载 {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    ap = argparse.ArgumentParser(description="clipkit 裁剪管线基准")
    ap.add_argument("--compare", type=Path, default=None, help="旧实现文件，做 A/B 对比")
    ap.add_argument("--repeats", type=int, default=3)
    args = ap.parse_args()

    legacy = _load(args.compare, "legacy_clip") if args.compare else None
    print(f"psutil: {'有（报告峰值内存）' if psutil else '无（只报耗时）'}")

    for (w, h) in [(1280, 720), (1920, 1080), (2560, 1440)]:
        img = make_image(w, h)
        size_mb = w * h * 3 / 1048576.0
        print(f"\n=== {w}x{h}  (原图 RGB ≈ {size_mb:.1f}MB) ===")

        t_new, m_new = measure(lambda: imaging.process_page_image(img), args.repeats)
        out_new = imaging.process_page_image(img)
        print(f"  clipkit  : {t_new * 1000:7.0f}ms  "
              f"peak+{m_new:5.1f}MB  -> {out_new.size}" if m_new is not None else
              f"  clipkit  : {t_new * 1000:7.0f}ms  -> {out_new.size}")

        if legacy is not None:
            t_old, m_old = measure(lambda: legacy.process_page_image(img), args.repeats)
            out_old = legacy.process_page_image(img)
            same = (out_old.size == out_new.size and
                    np.array_equal(np.asarray(out_old), np.asarray(out_new)))
            speed = f"{t_old / t_new:.2f}x" if t_new else "n/a"
            print(f"  旧实现   : {t_old * 1000:7.0f}ms  "
                  f"peak+{m_old:5.1f}MB  -> {out_old.size}" if m_old is not None else
                  f"  旧实现   : {t_old * 1000:7.0f}ms  -> {out_old.size}")
            print(f"  提速 {speed}   逐像素一致: {same}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
