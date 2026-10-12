"""clipkit 的小配置：保存文件夹、开关类参数。

存放位置优先级：
    1. 环境变量 CLIPKIT_CONFIG 指定的文件（测试 / 多实例用）
    2. %APPDATA%\\clipkit\\config.json（Windows）
    3. ~/.config/clipkit/config.json（其它平台）

读不到、写不进去都不致命：读失败就回落默认值，写失败只返回 False 让调用方提示，
绝不因为配置问题拦住程序启动。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

DEFAULTS: dict = {
    "save_dir": "",              # 空字符串 = 用 exporter.default_folder()
    "process_on_enable": True,   # 开启功能时是否先把"当前剪贴板"处理一次
}


def config_path() -> Path:
    env = os.environ.get("CLIPKIT_CONFIG", "").strip()
    if env:
        return Path(env)
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / "clipkit" / "config.json"
    return Path.home() / ".config" / "clipkit" / "config.json"


def load() -> dict:
    """读配置；任何异常都回落默认值（配置坏了不该拦启动）。"""
    data = dict(DEFAULTS)
    try:
        raw = json.loads(config_path().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return data
    except Exception:
        return data

    if isinstance(raw, dict):
        for key, default in DEFAULTS.items():
            if key not in raw:
                continue
            value = raw[key]
            if isinstance(default, bool):
                data[key] = bool(value)
            elif isinstance(default, str):
                data[key] = str(value)
    return data


def update(**changes) -> bool:
    """合并写回配置；成功返回 True。"""
    data = load()
    data.update({k: v for k, v in changes.items() if k in DEFAULTS})
    path = config_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return True
    except Exception:
        return False


def save_dir() -> Path:
    """当前生效的保存文件夹（未配置则用默认位置）。"""
    from .exporter import default_folder

    configured = str(load().get("save_dir") or "").strip()
    return Path(configured) if configured else default_folder()
