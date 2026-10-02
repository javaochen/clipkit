"""clipkit —— 剪贴板图片工具：拼接 / 裁剪 / 文本公式格式化。

一个进程、一个托盘图标，解决原先 copy_images.py 与 clip2line_cut&$.py 两个常驻
程序抢同一个剪贴板的问题。

模块划分：
    formula    纯函数：文本数学公式格式化（\\( \\) → $，\\[ \\] → $$）
    imaging    纯函数：去空白裁剪 + 纵向拼接（无剪贴板依赖）
    clipboard  剪贴板后端：按格式位判定内容类型、无锁读取序列号、读写文本/图片
    monitor    状态机：轮询 → 判定类型 → 分发（拼接收集 / 即时裁剪 / 文本格式化）
    tray       pystray 托盘界面

用法：
    python -m clipkit                 启动托盘（默认暂停）
    python -m clipkit stitch          启动并直接进入拼接模式
    python -m clipkit crop            启动并直接进入即时裁剪模式
    python -m clipkit simulate 目录    离线：逐张裁剪后拼接，完全不碰剪贴板
    python -m clipkit doctor          环境自检
"""

__version__ = "0.1.0"
