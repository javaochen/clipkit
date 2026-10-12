"""把收集到的截图分开保存到指定文件夹。

文件名默认 `<日期>_<时分秒>.png`，例如 2026-10-12_14-03-40.png；
同一秒内的多张自动加序号（_2、_3…），并且**绝不覆盖**文件夹里已有的文件。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

TIME_FORMAT = "%Y-%m-%d_%H-%M-%S"
EXTENSION = ".png"


@dataclass
class Shot:
    """一张收集到的截图：图像 + 它进入剪贴板的时刻（文件名用这个时间）。"""

    image: Image.Image
    taken_at: float = field(default_factory=time.time)


def default_folder() -> Path:
    """默认保存位置：<用户>\\Pictures\\clipkit；没有 Pictures 目录就退到用户目录下。"""
    pictures = Path.home() / "Pictures"
    return (pictures if pictures.is_dir() else Path.home()) / "clipkit"


def unique_path(folder: Path, taken_at: float, used: set | None = None,
                fmt: str = TIME_FORMAT, ext: str = EXTENSION) -> Path:
    """给这一张图挑一个不冲突的文件名。

    used 是同一批里已经用掉的文件名（同秒多张时用），磁盘上已存在的也不会被覆盖。
    """
    base = time.strftime(fmt, time.localtime(taken_at))
    taken = used if used is not None else set()
    index = 1
    while True:
        name = f"{base}{ext}" if index == 1 else f"{base}_{index}{ext}"
        path = folder / name
        if name.lower() not in taken and not path.exists():
            return path
        index += 1


def save_shots(shots, folder, fmt: str = TIME_FORMAT, ext: str = EXTENSION) -> list[Path]:
    """把一组 Shot 逐张存进 folder（不存在会自动创建），返回写出的路径。

    任何一张失败都会向上抛（调用方负责报告），已经写出的文件保持不动。
    """
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)

    used: set = set()
    paths: list[Path] = []
    for shot in shots:
        path = unique_path(folder, shot.taken_at, used, fmt=fmt, ext=ext)
        used.add(path.name.lower())
        shot.image.save(path)
        paths.append(path)
    return paths


def save_images(images, folder, *, now: float | None = None,
                fmt: str = TIME_FORMAT, ext: str = EXTENSION) -> list[Path]:
    """便利版：直接给一组 PIL 图，用同一个时刻命名（没有记录拍摄时间时用）。"""
    stamp = time.time() if now is None else now
    return save_shots([Shot(img, stamp) for img in images], folder, fmt=fmt, ext=ext)
