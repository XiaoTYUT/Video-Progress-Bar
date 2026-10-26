# Video Toolkit（视频生成工具包）

面向视频制作场景的桌面工具包，基于 **Python + PySide6 (QML)**。目标是持续收纳各类视频生成/处理小工具，统一在一个桌面应用里使用。

当前已内置第一个工具：

- **视频进度条生成器** —— 根据章节时间段文件，一键生成**带章节标注的视频进度条**，同时输出白底 MP4 与透明 MOV

工具同时保留完整命令行（CLI）用法，方便脚本化与批量处理。

## 桌面应用（GUI）

### 启动

```bash
pip install -r requirements.txt
python app/main.py
```

### 功能

- 左侧工具导航 + 右侧工具页，后续新工具直接在导航中扩展
- 进度条工具页：
  - **章节编辑器**：打开 / 保存 / 另存为时间段文件，输入停顿后自动解析校验，错误行号实时提示
  - **输出参数**：分辨率预设（1080p 全幅 / 窄幅 1920×270 条带）、帧率、MP4 / MOV / WebM 格式勾选、章节标题开关、播放头 GIF（默认内置小驴）
  - **单帧预览**：指定时间点（秒 / `MM:SS`），白底 / 透明背景切换，秒出图先看效果
  - **视频渲染**：后台线程渲染，实时进度百分比，可随时取消，日志滚动显示，完成后一键打开输出目录

## 命令行（CLI）

### 1. 生成视频

```bash
# 默认输出到输入文件同目录: progress_bar_<时间戳>.mp4 / .mov
python video_bar.py examples/segments.txt

# 自定义输出与规格
python video_bar.py examples/segments.txt \
    --mp4 output/white.mp4 \
    --mov output/alpha.mov \
    --width 1920 --height 1080 --fps 30

# 不显示章节标题
python video_bar.py examples/segments.txt --no-labels
```

### 2. 单帧快速预览（推荐先预览再渲染）

```bash
# 视频正中间时刻, 白底 PNG
python video_bar.py examples/segments.txt --preview

# 指定时间点(秒 / MM:SS / HH:MM:SS)
python video_bar.py examples/segments.txt --preview --at 00:10

# 透明背景 PNG(模拟叠加到素材上的效果)
python video_bar.py examples/segments.txt --preview --transparent-frame

# 指定输出路径
python video_bar.py examples/segments.txt --preview --at 12 --frame-out preview.png
```

### 3. 作为 Python 库调用

```python
from video_bar import parse_segments_file, render_progress_video, render_preview_frame

segments = parse_segments_file("examples/segments.txt")

render_progress_video(segments, "white.mp4", "alpha.mov",
                      width=1920, height=1080, fps=30)

render_preview_frame(segments, "preview.png", at=10)                # 白底
render_preview_frame(segments, "preview_a.png", at=10, transparent=True)  # 透明
```

### 命令行参数

| 参数 | 说明 |
| --- | --- |
| `segments` | 时间段文件路径（必填） |
| `--mp4` / `--mov` | 两种视频的输出路径 |
| `--webm` / `--only-webm` / `--only-mov` | 输出格式组合控制 |
| `--width` / `--height` / `--fps` | 画面与帧率参数，默认 1920 / 1080 / 30 |
| `--no-labels` | 不绘制章节标题 |
| `--preview` | 单帧预览模式：只输出一张 PNG，不渲染视频 |
| `--at` | 预览帧时间点，默认取视频正中间 |
| `--transparent-frame` | 预览帧保留透明背景，默认白底 |
| `--frame-out` | 预览帧输出路径 |
| `--mascot` / `--mascot-height` | 播放头 GIF 及缩放高度 |
| `--ffmpeg` | ffmpeg 可执行文件路径（PATH 中找不到时使用） |

## 输入文件格式

每行一个章节，只需填写**结束时间**（开始时间自动接上一段，第一段从 0 开始）：
`结束时间  标题(可省略)`，`#` 开头为注释，空行忽略。

也可写完整格式 `开始时间, 结束时间, 标题` 显式指定区间（兼容旧格式）。

```text
# 我的视频章节
00:06, 片头
00:14 第一部分:背景介绍
00:20, 第二部分:核心演示
29; 片尾

# 完整写法(兼容)
00:15 - 01:20 显式区间
```

解析器会自动校验：结束必须晚于开始、章节之间不允许重叠，出错时给出具体行号。

## 输出规格

| 项目 | 白底版本 | 透明版本 |
| --- | --- | --- |
| 容器 / 编码 | MP4 / H.264 High（CRF 18） | MOV / ProRes 4444 |
| 像素格式 | yuv420p（全平台兼容） | yuva444p10le（无损 Alpha 通道） |
| 背景 | 纯白 | 全透明 |
| 分辨率 / 帧率 | 默认 1920×1080 @ 30fps，均可自定义 | 同左 |
| 时长 | 等于时间段文件最后一个章节的结束时间 | 同左 |

另支持 **透明 WebM**（VP9 + Alpha），体积小，适合网页播放或预览；剪映场景请用 MOV（剪映不支持 VP9 透明通道）。

## 环境要求

- Python 3.10+
- Python 依赖：`numpy`、`pillow`（GUI 额外需要 `PySide6`，均已在 requirements.txt 中）
- [FFmpeg](https://ffmpeg.org/) 可执行文件，需包含 **libx264** 与 **prores** 编码器
  - Windows 推荐 [gyan.dev full build](https://www.gyan.dev/ffmpeg/builds/)，安装后加入 PATH
  - 验证：`ffmpeg -version`
  - 也可在运行时用 `--ffmpeg` 参数指定 `ffmpeg.exe` 的完整路径

## 安装

```bash
git clone https://github.com/XiaoTYUT/Video-Progress-Bar.git
cd Video-Progress-Bar
pip install -r requirements.txt
```

## 项目结构

```
Video-Progress-Bar/
├── video_bar.py              # 进度条渲染核心: 时间段解析 + 帧渲染 + CLI (库/GUI 共用)
├── requirements.txt        # Python 依赖
├── donkey_hiker_*.gif      # 内置播放头素材(小驴)
├── app/                    # 桌面应用 (PySide6 + QML)
│   ├── main.py             #   入口: python app/main.py
│   ├── backend.py          #   桥接层: QML <-> 渲染核心
│   ├── render_worker.py    #   QThread 后台渲染线程
│   └── qml/
│       ├── Main.qml            # 主窗口: 左侧工具导航 + 右侧页面
│       └── ProgressBarPage.qml # 进度条工具页
└── examples/
    └── segments.txt        # 时间段输入示例
```

## 工作原理

1. 解析时间段文件，得到章节列表与总时长
2. 用 Pillow 预渲染静态图层（轨道、章节黑/白双套文字、分界刻度）
3. NumPy 按当前播放进度逐帧向量化合成：深绿填充 + 按填充边界切换文字颜色 + Alpha 混合
4. 同一条 RGBA 原始像素流送入 FFmpeg：一路与白色背景合成后编码 H.264 MP4，一路保留 Alpha 编码 ProRes 4444 MOV

## 常见问题

**Q：中文显示成方框？**
A：系统缺少中文字体。Windows 安装微软雅黑/黑体，Linux 可 `apt install fonts-noto-cjk`，macOS 自带苹方。

**Q：透明 MOV 为什么这么大？**
A：ProRes 4444 是帧内压缩的制作中间格式，体积本身远大于 H.264，属正常现象；它用于剪辑合成而非分发。需要小体积可选 WebM 输出。

**Q：只想快速看一眼效果？**
A：GUI 里点「生成预览」，或 CLI 用 `--preview`，生成一张 PNG 仅需一瞬间，确认满意后再渲染完整视频。

## Roadmap

- [x] 图形界面（PySide6 / QML 桌面应用）
- [x] 透明 WebM 输出
- [x] 窄幅条带模式（1920×270）与播放头 GIF
- [ ] GUI 接入 MOV 多线程分块并行渲染
- [ ] 颜色、条高、位置等样式参数化
- [ ] 支持 JSON / CSV / SRT 等更多输入格式
- [ ] 更多工具入驻（帧率转换、倍速处理等）
