# Video Progress Bar

根据章节时间段文件，一键生成**带章节标注的视频进度条**，同时输出两种制作规格：

- **白色背景 MP4**（H.264）——可直接播放、分享
- **透明背景 MOV**（ProRes 4444，带 Alpha 通道）——可直接拖入 Premiere / Final Cut / DaVinci 叠加到视频上

当前为功能验证 Demo（命令行，无 GUI），核心渲染链路已完整可用。

## 功能特性

- 纯文本时间段输入，支持秒 / `MM:SS` / `HH:MM:SS`（可带小数），逗号、横线、分号、Tab 等多种分隔写法
- 一次渲染同步产出白底 MP4 与透明 MOV，无需渲染两遍
- 进度条满宽贴底，章节标题内嵌显示：红色已播放区域白字、灰色未播放区域黑字，分界刻度清晰
- 标题过长自动省略号截断，自动适配 Windows / macOS / Linux 中文字体
- **单帧预览模式**：不启动编码器、不等待，秒出 PNG，先看效果再渲染成片
- 渲染基于 NumPy 向量化合成 + FFmpeg 硬编码管线，输出为视频制作行业标准格式

## 输出规格

| 项目 | 白底版本 | 透明版本 |
| --- | --- | --- |
| 容器 / 编码 | MP4 / H.264 High（CRF 18） | MOV / ProRes 4444 |
| 像素格式 | yuv420p（全平台兼容） | yuva444p12le（无损 Alpha 通道） |
| 背景 | 纯白 | 全透明 |
| 分辨率 / 帧率 | 默认 1920×1080 @ 30fps，均可自定义 | 同左 |
| 时长 | 等于时间段文件最后一个章节的结束时间 | 同左 |

## 环境要求

- Python 3.10+
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

Python 依赖仅两个：`numpy`、`pillow`。

## 输入文件格式

每行一个章节：`开始时间  结束时间  标题(可省略)`，`#` 开头为注释，空行忽略。

```text
# 我的视频章节
00:00, 00:06, 片头
00:06 - 00:14 第一部分:背景介绍
00:14, 20.0, 第二部分:核心演示
20, 24; 片尾
```

解析器会自动校验：结束必须晚于开始、章节之间不允许重叠，出错时给出具体行号。

## 使用

### 1. 生成视频

```bash
# 默认输出到输入文件同目录: *_white.mp4 与 *_alpha.mov
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
| `--width` / `--height` / `--fps` | 画面与帧率参数，默认 1920 / 1080 / 30 |
| `--no-labels` | 不绘制章节标题 |
| `--preview` | 单帧预览模式：只输出一张 PNG，不渲染视频 |
| `--at` | 预览帧时间点，默认取视频正中间 |
| `--transparent-frame` | 预览帧保留透明背景，默认白底 |
| `--frame-out` | 预览帧输出路径 |
| `--ffmpeg` | ffmpeg 可执行文件路径（PATH 中找不到时使用） |

## 项目结构

```
Video-Progress-Bar/
├── video_bar.py          # 核心程序：时间段解析 + 帧渲染 + CLI
├── requirements.txt      # Python 依赖
├── .gitignore
└── examples/
    └── segments.txt      # 时间段输入示例
```

## 工作原理

1. 解析时间段文件，得到章节列表与总时长
2. 用 Pillow 预渲染静态图层（轨道、章节黑/白双套文字、分界刻度）
3. NumPy 按当前播放进度逐帧向量化合成：红色填充 + 按填充边界切换文字颜色 + Alpha 混合
4. 同一条 RGBA 原始像素流送入 FFmpeg：一路与白色背景合成后编码 H.264 MP4，一路保留 Alpha 编码 ProRes 4444 MOV

## 常见问题

**Q：中文显示成方框？**
A：系统缺少中文字体。Windows 安装微软雅黑/黑体，Linux 可 `apt install fonts-noto-cjk`，macOS 自带苹方。

**Q：透明 MOV 为什么这么大？**
A：ProRes 4444 是帧内压缩的制作中间格式，体积本身远大于 H.264，属正常现象；它用于剪辑合成而非分发。

**Q：只想快速看一眼效果？**
A：用 `--preview`，生成一张 PNG 仅需一瞬间，确认满意后再渲染完整视频。

## Roadmap

- [ ] 颜色、条高、位置等样式参数化（命令行 / 配置文件）
- [ ] 支持 JSON / CSV / SRT 等更多输入格式
- [ ] 图形界面（GUI）
- [ ] 圆形播放头、章节动画等更多视觉样式
