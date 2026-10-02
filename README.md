# clipkit

剪贴板图片工具：**拼接 / 裁剪 / 文本公式格式化**，一个进程、一个托盘图标。

把原来两个各自常驻托盘的脚本合并成一个程序：

| 旧文件 | 原来的能力 |
|---|---|
| `copy_images.py` | 监听剪贴板积累图片，停止时纵向拼接 |
| `clip2line_cut&$.py` | 监听剪贴板：图片去空白裁剪 + 文本公式格式化 |

合并的首要理由不是"少一个文件"，而是这两个程序会**互相吃掉对方的输出**：它们都监听同一个
剪贴板、也都会往回写。你复制一张截图，裁剪工具改写完，拼接工具立刻把**裁过的图**收进队列；
拼接完成写回后，裁剪工具又拼好的图再裁一遍。产物已经被对方改过一次，而且行为不可预测。

---

## 三步用起来

1. 右键托盘图标 → **拼接模式**
2. 连续截图（`Win+Shift+S` 等），每张都会自动裁剪后收集，图标角标显示已收集张数
3. **左键点图标** = 完成拼接并复制（或右键菜单「完成拼接并复制（N 张）」）

## 三种图片行为（互斥）

| 模式 | 行为 | 什么时候写剪贴板 |
|---|---|---|
| 拼接模式 | 逐张裁剪 → 收集 | **只在「完成拼接」时写一次**（收集期间一次都不写） |
| 即时裁剪 | 每张立刻裁剪并写回 | 每张写一次 |
| 关闭 | 完全不处理图片 | 不写 |

拼接模式下**收集期间完全不碰剪贴板**，所以你在收集截图的过程中照常复制文字、粘贴文字都没问题。

文本公式格式化是独立开关，与图片行为互不干扰。图标配色：灰=关闭，绿=即时裁剪，蓝=拼接模式。

## 为什么"图片监听不影响文本复制"

三条规则写在代码里，也有测试盯着：

1. **类型判定按安全优先级：文件 > 文本 > 图片**（`clipboard.classify`）。
   浏览器 / Office / 资源管理器复制时会同时放"文本 + HTML + DIB"（网页选区、Excel 区域）。
   这类富剪贴板一律按**文本**处理 —— 若当成截图去裁剪改写，你想粘贴的文字就没了。
   只有纯图片剪贴板（截图工具，通常只有 `CF_DIB`/`PNG`）才当图片。
2. **轮询不打开剪贴板**。序列号走 `user32.GetClipboardSequenceNumber()`，不需要
   `OpenClipboard`，每秒 4 次的空转不会去锁剪贴板、不干扰别的程序正在复制/粘贴。
   只有序列号真的变了，才打开剪贴板读格式位。
3. **读路径绝不写**。只有真要写回结果时才 `EmptyClipboard`；文本没有公式标记时，
   一个字节都不动剪贴板。

顺带修掉一个会**静默致死**的旧问题：旧版监听循环没有异常保护，在资源管理器里复制文件时
`grabclipboard()` 返回的是**文件路径列表**而不是图片，下游 `AttributeError` 直接打死监听
线程，而托盘图标还显示"监听中"——程序看着在跑，其实已经废了。现在走 `safe_tick()`：
异常被记进 `last_error`，循环继续。

## 安装

```powershell
pip install -r requirements.txt
```

依赖：`pillow`、`numpy`、`pywin32`、`pystray`（后两个只在 Windows 需要）。
原 `clip2line_cut&$.py` 依赖的 `pyperclip` 已去掉——它的功能就是 `win32clipboard` 的薄封装。

## 运行

```powershell
python -m clipkit                 # 启动托盘（默认图片功能关闭，不会动剪贴板）
python -m clipkit stitch          # 启动并直接进入拼接模式
python -m clipkit crop --text     # 即时裁剪 + 文本公式格式化
python -m clipkit simulate .\shots -o all.png   # 离线：逐张裁剪后拼接，完全不碰剪贴板
python -m clipkit doctor          # 环境自检（依赖、剪贴板可读性）
```

扁平布局，不需要先安装。也可以用 `pip install -e .` 之后跑 `clipkit stitch`。

退出码：`0` 正常，`1` 失败，`2` 用法错误。

## 测试

```powershell
python -m unittest discover -v
```

测试全部用**内存假剪贴板**，不碰真实剪贴板、不起托盘，所以在没有桌面会话的地方也能跑。

重点在 `tests/test_legacy_equivalence.py`：它把旧文件按路径加载进来，对 **11 组合成图 × 5 个
函数**做逐像素对拍（`find_content_bbox` / `crop_non_white` / `remove_blank_rows` /
`remove_blank_columns` / `process_page_image`，外加与 `copy_images.py` 的拼接对拍）。
旧文件退役后这个测试会自动跳过，不会变成长期负担。

## 目录结构

```
clipkit/
├─ clipkit/
│  ├─ formula.py      纯函数：公式格式化（行为与原实现一致）
│  ├─ imaging.py      纯函数：去空白裁剪 + 纵向拼接
│  ├─ clipboard.py    剪贴板后端：格式位判定 / 无锁序列号 / 读写文本与图片
│  ├─ monitor.py      状态机：轮询 → 判类型 → 分发（拼接/裁剪/文本）
│  ├─ tray.py         pystray 托盘：模式单选 + 主操作 + 气泡提示
│  └─ __main__.py     CLI：tray / stitch / crop / simulate / doctor
├─ tests/             55+ 个测试，含与旧实现的逐像素对拍
├─ tools/bench_imaging.py   耗时 / 峰值内存基准（可 --compare 旧实现 A/B）
└─ requirements.txt
```

分层的用意：`formula` / `imaging` 是纯函数，跟 Windows 和剪贴板无关，可以单独测、单独复用；
`clipboard` 把系统耦合收在一处；`monitor` 只依赖"后端协议"，所以能用假剪贴板把状态机测透。

## 性能与内存

裁剪管线保留了旧实现的全部判定逻辑（灰度权重、运算顺序、阈值、两段式裁剪），只把两处
**对象爆炸**搬掉了：旧写法为每一行/每一列各建一个 numpy 数组再 `vstack`/`concatenate`
整块复制，1920×1080 的截图就是 1080 个行数组 + 1920 个列数组；现在用布尔掩码一次取完。

自己测：

```powershell
python tools/bench_imaging.py --compare "..\clip2line_cut&$.py"
```

## 与旧文件的关系

`copy_images.py`、`clip2line_cut&$.py` 保持原样，**没有改动、也没有删除**。验证新版没问题后，
把 `launcher.py` 的目标换成新版即可：

```python
TARGETS_RAW = [
    ("clip", "clipkit"),      # 需要给 launcher 加"带参数启动"支持（--mode stitch 等）
]
```

**注意**：试用期间不要让新旧同时常驻，否则又会回到"两个程序抢一个剪贴板"的状态。

## 已知边界

* 托盘/剪贴板部分只能在 Windows 上跑；纯函数与状态机测试跨平台。
* 拼接队列把已裁剪的图片放在内存里，收集几十张 4K 截图会吃内存。离线批量（`simulate`）
  走的是两遍扫描：峰值 ≈ 画布 + 一张图。
* 写回剪贴板后的 0.9 秒内不处理新内容（防止自己触发自己的回环）。默认值在
  `ClipboardMonitor(self_write_grace=...)` 可调。
* `pdf_manage/line_cropper.py`、`pdf_reader` 按约定**不改**；它们里面那份裁剪代码是
  旧实现的拷贝，将来想收编可以改成本库的消费者。
