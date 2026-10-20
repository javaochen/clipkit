"""图像处理：去空白裁剪 + 纵向拼接（纯函数，不碰剪贴板、不碰托盘）。

**"空白"的定义**（对一条行/列，g 是它所有像素的灰度）：

    整条偏白：min(g) > 255 − depth_threshold      （没有比近白更暗的像素）
    整条偏黑：max(g) ≤ black_threshold            （每个像素都黑）

两者都算空白，都裁掉 —— 纯黑的行/列不是内容，和纯白一样该去掉。

**极细空白保留**：夹在内容之间、只有 1 像素宽的空白线（黑白同理）会留下当分隔线，
否则整条删掉会让上下两段内容粘在一起。只有连续 ≥ min_blank_run(=2) 像素的空白才删。

与旧实现（clip2line_cut&$.py / line_image.py）的关系：两段式裁剪结构、灰度权重、
运算顺序、阈值判定全部照旧，所以
    remove_blank_rows(..., black_threshold=-1, min_blank_run=1)
能逐像素复现旧行为（tests/test_legacy_equivalence.py 就是这么对拍的）。

新写法另外把两处 O(H+W) 的对象爆炸搬掉了——旧写法会为每一行/每一列各建一个 numpy
数组再 vstack / concatenate 整块复制（1920x1080 就是 1080 个行数组 + 1920 个列数组），
这是内存峰值和卡顿的主要来源；新写法用布尔掩码一次取完。
"""

from __future__ import annotations

import numpy as np
from PIL import Image

WHITE_THRESHOLD = 245
DEPTH_THRESHOLD = 20
MIN_SIZE = 8
BLACK_THRESHOLD = 8     # 灰度 ≤ 这个值算"黑"；只认接近 0 的纯黑，不动深色主题(#1E1E1E=30)
MIN_BLANK_RUN = 2       # 连续空白 ≥ 这么多像素才删；1 像素宽的空白线留作分隔


def _as_rgb(img: Image.Image) -> Image.Image:
    if img.mode != "RGB":
        return img.convert("RGB")
    return img


def gray_map(img: Image.Image) -> np.ndarray:
    """0.299R + 0.587G + 0.114B（float32）。

    刻意逐通道 astype(float32) 再累加：这样每个乘加都在 float32 里完成，与旧实现
    `np.array(img).astype(np.float32)` 后整体算的结果**逐位一致**（numpy 的弱标量
    规则下，uint8 数组乘 python float 会升到 float64，那就会引入 1 ULP 差异、
    可能在阈值边界翻转一整行）。同时峰值内存只有单通道 float32，而不是三通道。
    """
    arr = np.asarray(_as_rgb(img), dtype=np.uint8)
    f32 = np.float32
    gray = arr[:, :, 0].astype(f32)
    gray *= f32(0.299)
    gray += arr[:, :, 1].astype(f32) * f32(0.587)
    gray += arr[:, :, 2].astype(f32) * f32(0.114)
    return gray


def depth_map(img: Image.Image) -> np.ndarray:
    """内容"深度" = 255 - 灰度，越黑越深（等价旧 rgb_to_depth）。"""
    return 255.0 - gray_map(img)


def max_depth_per_row(img: Image.Image) -> np.ndarray:
    return depth_map(img).max(axis=1)


def max_depth_per_column(img: Image.Image) -> np.ndarray:
    return depth_map(img).max(axis=0)


def blank_mask(gray: np.ndarray, axis: int, depth_threshold: int = DEPTH_THRESHOLD,
               black_threshold: int = BLACK_THRESHOLD) -> np.ndarray:
    """True = 该行/列是空白：整条偏白 或 整条偏黑。

    axis=1 → 逐行（对每行求最暗/最亮），axis=0 → 逐列。
    """
    darkest = gray.min(axis=axis)        # 这一条里最暗的像素
    brightest = gray.max(axis=axis)      # 这一条里最亮的像素
    too_bright = darkest > (255 - depth_threshold)   # 没有比"近白"更暗的像素
    too_dark = brightest <= black_threshold          # 每个像素都黑
    return too_bright | too_dark


def _blank_runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """返回 mask 里连续 True 的区间 [(start, end), ...]（end 不含）。"""
    runs: list[tuple[int, int]] = []
    start = None
    for index, value in enumerate(mask):
        if value and start is None:
            start = index
        elif not value and start is not None:
            runs.append((start, index))
            start = None
    if start is not None:
        runs.append((start, len(mask)))
    return runs


def keep_mask(gray: np.ndarray, axis: int, depth_threshold: int = DEPTH_THRESHOLD,
              black_threshold: int = BLACK_THRESHOLD,
              min_blank_run: int = MIN_BLANK_RUN) -> np.ndarray:
    """True = 这一行/列要保留。

    空白都删；但"夹在内容之间、比 min_blank_run 更窄"的空白留作分隔线。
    与首尾相连的空白照旧裁掉（那是外边距，不是分隔线）。
    """
    keep = ~blank_mask(gray, axis, depth_threshold, black_threshold)
    if min_blank_run <= 1 or not keep.any():
        return keep

    first = int(np.argmax(keep))
    last = len(keep) - 1 - int(np.argmax(keep[::-1]))
    for start, end in _blank_runs(~keep):
        if start <= first or end > last:      # 外边距：照旧删
            continue
        if end - start < min_blank_run:       # 夹在内容之间的极细空白：留
            keep[start:end] = True
    return keep


def find_content_bbox(img: Image.Image, white_threshold: int = WHITE_THRESHOLD):
    """非白内容的包围盒 (left, top, right, bottom)，全白返回 None。"""
    gray = gray_map(img)
    mask = gray < white_threshold
    if not mask.any():
        return None
    ys, xs = np.where(mask)
    return (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)


def safe_crop_box(box, width: int, height: int, min_size: int = MIN_SIZE):
    """把包围盒夹到图像范围内，并保证最小尺寸（避免裁出 0 宽/0 高）。"""
    if box is None:
        left = max(0, (width - min_size) // 2)
        top = max(0, (height - min_size) // 2)
        return (left, top, min(width, left + min_size), min(height, top + min_size))

    left, top, right, bottom = box
    if right - left < min_size:
        mid = (left + right) // 2
        left = max(0, mid - min_size // 2)
        right = min(width, left + min_size)
    if bottom - top < min_size:
        mid = (top + bottom) // 2
        top = max(0, mid - min_size // 2)
        bottom = min(height, top + min_size)

    left, top = max(0, left), max(0, top)
    right, bottom = min(width, right), min(height, bottom)
    if right <= left:
        right = min(width, left + min_size)
    if bottom <= top:
        bottom = min(height, top + min_size)
    return (left, top, right, bottom)


def crop_non_white(img: Image.Image, white_threshold: int = WHITE_THRESHOLD,
                   min_size: int = MIN_SIZE) -> Image.Image:
    """去掉四周白边（只做一次包围盒裁剪）。"""
    img = _as_rgb(img)
    box = find_content_bbox(img, white_threshold=white_threshold)
    box = safe_crop_box(box, img.width, img.height, min_size=min_size)
    return img.crop(box)


def remove_blank_rows(img: Image.Image, depth_threshold: int = DEPTH_THRESHOLD,
                      min_row_keep: int = 1, black_threshold: int = BLACK_THRESHOLD,
                      min_blank_run: int = MIN_BLANK_RUN) -> Image.Image:
    """删掉整体为空白（整条偏白或整条偏黑）的行。

    夹在内容之间、1 像素宽的空白线保留当分隔线（min_blank_run 可调）。
    复现旧行为：black_threshold=-1, min_blank_run=1。
    """
    img = _as_rgb(img)
    if img.width == 0 or img.height == 0:
        return img

    gray = gray_map(img)
    keep_rows = np.where(~blank_mask(gray, 1, depth_threshold, black_threshold))[0]
    if keep_rows.size == 0:
        return img.crop((0, 0, img.width, min(img.height, max(1, min_row_keep))))

    top, bottom = int(keep_rows.min()), int(keep_rows.max()) + 1
    arr = np.asarray(img, dtype=np.uint8)[top:bottom]

    # 第二段：在裁掉的上下边界之内再逐行判定（旧实现就是两段式）
    keep = keep_mask(gray_map(Image.fromarray(arr)), 1, depth_threshold,
                     black_threshold, min_blank_run)
    out = arr[keep]                                  # ← 一行代替 H 个行数组 + vstack
    if out.shape[0] < min_row_keep:
        out = arr[:min(min_row_keep, arr.shape[0])]
    return Image.fromarray(np.ascontiguousarray(out, dtype=np.uint8))


def remove_blank_columns(img: Image.Image, depth_threshold: int = DEPTH_THRESHOLD,
                         min_col_keep: int = 1, black_threshold: int = BLACK_THRESHOLD,
                         min_blank_run: int = MIN_BLANK_RUN) -> Image.Image:
    """删掉整体为空白（整条偏白或整条偏黑）的列。

    夹在内容之间、1 像素宽的空白线保留当分隔线（min_blank_run 可调）。
    复现旧行为：black_threshold=-1, min_blank_run=1。
    """
    img = _as_rgb(img)
    if img.width == 0 or img.height == 0:
        return img

    gray = gray_map(img)
    keep_cols = np.where(~blank_mask(gray, 0, depth_threshold, black_threshold))[0]
    if keep_cols.size == 0:
        return img.crop((0, 0, min(img.width, max(1, min_col_keep)), img.height))

    left, right = int(keep_cols.min()), int(keep_cols.max()) + 1
    arr = np.asarray(img, dtype=np.uint8)[:, left:right]

    keep = keep_mask(gray_map(Image.fromarray(arr)), 0, depth_threshold,
                     black_threshold, min_blank_run)
    out = arr[:, keep]                               # ← 一行代替 W 个列数组 + concatenate
    if out.shape[1] < min_col_keep:
        out = arr[:, :min(min_col_keep, arr.shape[1])]
    return Image.fromarray(np.ascontiguousarray(out, dtype=np.uint8))


def process_page_image(img: Image.Image, white_threshold: int = WHITE_THRESHOLD,
                       depth_threshold: int = DEPTH_THRESHOLD,
                       min_size: int = MIN_SIZE, black_threshold: int = BLACK_THRESHOLD,
                       min_blank_run: int = MIN_BLANK_RUN) -> Image.Image:
    """完整裁剪管线：去四周白边 → 删空白行 → 删空白列。

    空白 = 整条偏白或整条偏黑（见模块说明）；1 像素宽的空白线留作分隔线。
    """
    img = _as_rgb(img)
    img = crop_non_white(img, white_threshold=white_threshold, min_size=min_size)
    img = remove_blank_rows(img, depth_threshold=depth_threshold, min_row_keep=min_size,
                            black_threshold=black_threshold, min_blank_run=min_blank_run)
    img = remove_blank_columns(img, depth_threshold=depth_threshold, min_col_keep=min_size,
                               black_threshold=black_threshold, min_blank_run=min_blank_run)
    return img


# ---------------------------------------------------------------- 拼接


def stitch_vertical(images, background=(255, 255, 255, 0), align: str = "center"):
    """把多张图纵向拼成一张（默认水平居中，透明底）。

    与旧 copy_images.py 的 stitch_vertical_centered 一致：画布宽取最宽者，
    高取高度之和，逐张 paste 到 (x, y)。
    """
    images = [im for im in images if im is not None]
    if not images:
        return None

    width = max(im.width for im in images)
    height = sum(im.height for im in images)
    canvas = Image.new("RGBA", (width, height), background)

    y = 0
    for im in images:
        if im.mode != "RGBA":
            im = im.convert("RGBA")
        if align == "center":
            x = (width - im.width) // 2
        elif align == "right":
            x = width - im.width
        else:
            x = 0
        canvas.paste(im, (x, y), im)
        y += im.height

    return canvas


def stitch_vertical_centered(images, background=(255, 255, 255, 0)):
    """兼容旧名字。"""
    return stitch_vertical(images, background=background, align="center")


def stitch_image_files(paths, crop: bool = True, background=(255, 255, 255, 0), **crop_kwargs):
    """把一组图片文件裁剪后纵向拼接（离线批量用，不碰剪贴板）。

    两遍扫描：第一遍只量尺寸，第二遍才逐张粘贴。峰值内存 ≈ 画布 + 一张图，
    而不是"所有原图 + 画布"——内存紧的机器上批量拼十几张截图差别很明显。
    """
    paths = list(paths)
    sizes = []
    for p in paths:
        with Image.open(p) as im:
            im = im.convert("RGB")
            if crop:
                im = process_page_image(im, **crop_kwargs)
            sizes.append(im.size)
    if not sizes:
        return None

    width = max(w for w, _ in sizes)
    height = sum(h for _, h in sizes)
    canvas = Image.new("RGBA", (width, height), background)

    y = 0
    for p, (w, h) in zip(paths, sizes):
        with Image.open(p) as im:
            im = im.convert("RGB")
            if crop:
                im = process_page_image(im, **crop_kwargs)
            rgba = im.convert("RGBA")
            canvas.paste(rgba, ((width - w) // 2, y), rgba)
        y += h
    return canvas
