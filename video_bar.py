# -*- coding: utf-8 -*-
"""
视频进度条生成器 (demo)

输入: 包含进度条时间段(章节)信息的文本文件, 每行一个时间段:
    开始时间, 结束时间, 标题(可省略)
    例:
        00:00, 00:15, 开场
        00:15 - 01:20 第一章
        75.5, 140
    时间支持: 秒(12 / 12.5)、MM:SS、HH:MM:SS(末尾可带小数)
    # 开头为注释, 空行忽略

输出:
    1. 白色背景 MP4  (H.264 / yuv420p, 可直接用于普通播放与分享)
    2. 透明背景 MOV  (ProRes 4444 / yuva444p10le, 可直接叠加到剪辑软件)

用法:
    # 生成视频(白底MP4 + 透明MOV)
    python video_bar.py examples/segments.txt
    python video_bar.py segments.txt --mp4 out/white.mp4 --mov out/alpha.mov \
        --width 1920 --height 1080 --fps 30

    # 快速预览单帧(不渲染视频, 直接出 PNG)
    python video_bar.py examples/segments.txt --preview                 # 中间时刻, 白底
    python video_bar.py examples/segments.txt --preview --at 00:10      # 指定时间点
    python video_bar.py examples/segments.txt --preview --transparent-frame  # 透明背景

也可作为库调用 render_preview_frame() / render_progress_video()。
"""

from __future__ import annotations

import argparse
import functools
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

import numpy as np
from PIL import Image, ImageDraw, ImageFont

# ---------------------------------------------------------------------------
# 视觉样式常量 (RGBA)。透明版本中 track/tick 使用半透明, 叠加到白底下呈浅灰色
# ---------------------------------------------------------------------------
FILL_RGBA = (255, 59, 48, 255)     # 已播放部分: 不透明红色
TRACK_RGBA = (0, 0, 0, 38)         # 未播放轨道: 叠加白底后约为 #D9D9D9
TICK_RGBA = (0, 0, 0, 170)         # 章节分界刻度
LABEL_FILLED_RGBA = (255, 255, 255, 235)  # 条内文字: 已播放区域(红底)用白色
LABEL_TRACK_RGBA = (0, 0, 0, 235)         # 条内文字: 未播放轨道用黑色


@dataclass(frozen=True)
class Segment:
    start: float
    end: float
    label: str = ""


# ---------------------------------------------------------------------------
# 时间段文件解析
# ---------------------------------------------------------------------------
_TOKEN_RE = re.compile(
    r"\d{1,2}:(?:[0-5]?\d):(?:[0-5]?\d)(?:\.\d+)?"   # HH:MM:SS[.ms]
    r"|\d{1,2}:(?:[0-5]?\d)(?:\.\d+)?"               # MM:SS[.ms]
    r"|\d+(?:\.\d+)?"                                 # 秒
)


def parse_time(token: str) -> float:
    """把 '12' / '01:23.5' / '1:02:03' 解析为秒。"""
    token = token.strip().rstrip("sS")
    if not token:
        raise ValueError("空时间值")
    parts = token.split(":")
    if len(parts) > 3 or any(p == "" for p in parts):
        raise ValueError(f"无法解析的时间: {token!r}")
    values = [float(p) for p in parts]
    if len(parts) >= 2 and not (0 <= values[-1] < 60):
        raise ValueError(f"秒数必须在 00-59 之间: {token!r}")
    if len(parts) == 3 and not (0 <= values[1] < 60):
        raise ValueError(f"分钟必须在 00-59 之间: {token!r}")
    seconds = 0.0
    for v in values:
        seconds = seconds * 60 + v
    return seconds


def parse_segments_file(path: str | Path) -> list[Segment]:
    """解析时间段文件, 返回按开始时间排序的 Segment 列表。"""
    lines = Path(path).read_text(encoding="utf-8-sig").splitlines()
    segments: list[Segment] = []
    for lineno, raw in enumerate(lines, 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        tokens = list(_TOKEN_RE.finditer(line))
        if len(tokens) < 2:
            raise ValueError(
                f"第 {lineno} 行缺少开始/结束时间: {raw.rstrip()!r}"
            )
        try:
            start = parse_time(tokens[0].group())
            end = parse_time(tokens[1].group())
        except ValueError as exc:
            raise ValueError(f"第 {lineno} 行时间解析失败: {exc}") from exc
        label = line[tokens[1].end():].strip(" \t,;，；-–—|\"'")
        segments.append(Segment(start, end, label))

    if not segments:
        raise ValueError("文件中没有任何时间段")

    segments.sort(key=lambda s: s.start)
    for i, seg in enumerate(segments):
        if seg.end <= seg.start:
            raise ValueError(f"时间段结束必须晚于开始: {seg}")
        if i > 0 and seg.start < segments[i - 1].end - 1e-6:
            raise ValueError(
                f"时间段重叠: {segments[i - 1]} 与 {seg}"
            )
    return segments


# ---------------------------------------------------------------------------
# 字体加载
# ---------------------------------------------------------------------------
# 优先含中文(CJK)字形的粗体, 其次系统西文粗体(按平台排列)
_FONT_FAMILIES = (
    # Windows
    "msyhbd.ttc", "simhei.ttf", "msyh.ttc", "arialbd.ttf", "segoeuib.ttf",
    # macOS
    "PingFang.ttc", "STHeiti Medium.ttc", "Hiragino Sans GB.ttc",
    # Linux (Noto CJK 常位于 fonts 目录的子文件夹中)
    "NotoSansCJK-Bold.ttc", "NotoSansCJK-Bold.otf",
    "wqy-zenhei.ttc", "wqy-microhei.ttc",
    # 西文兜底
    "DejaVuSans-Bold.ttf", "Arial.ttf",
)


def _font_search_dirs() -> list[Path]:
    """返回当前平台常见的系统/用户字体目录。"""
    home = Path.home()
    if platform.system() == "Windows":
        win = Path(os.environ.get("WINDIR", r"C:\Windows"))
        return [win / "Fonts", home / "AppData/Local/Microsoft/Windows/Fonts"]
    if platform.system() == "Darwin":
        return [Path("/System/Library/Fonts"), Path("/Library/Fonts"),
                home / "Library/Fonts"]
    return [Path("/usr/share/fonts"), Path("/usr/local/share/fonts"),
            home / ".local/share/fonts", home / ".fonts"]


@functools.lru_cache(maxsize=1)
def _index_system_fonts() -> dict[str, Path]:
    """扫描字体目录, 建立 小写文件名 -> 完整路径 的索引(只扫描一次)。"""
    index: dict[str, Path] = {}
    suffixes = {".ttf", ".ttc", ".otf"}
    for directory in _font_search_dirs():
        if not directory.is_dir():
            continue
        try:
            for path in directory.rglob("*"):
                if path.suffix.lower() in suffixes and path.is_file():
                    index.setdefault(path.name.lower(), path)
        except OSError:
            continue
    return index


def _load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """按优先级查找支持中文的粗体字体, 找不到则回退 Pillow 默认字体。"""
    available = _index_system_fonts()
    for name in _FONT_FAMILIES:
        candidate = available.get(name.lower())
        if candidate is not None:
            try:
                return ImageFont.truetype(str(candidate), size)
            except OSError:
                pass
    return ImageFont.load_default()


# ---------------------------------------------------------------------------
# 进度条渲染
# ---------------------------------------------------------------------------
def _fit_label(text: str, draw: ImageDraw.ImageDraw, font, max_w: float) -> str:
    """文字超出 max_w 时用省略号截断; 连省略号都放不下则返回空串。"""
    if draw.textlength(text, font=font) <= max_w:
        return text
    ellipsis = "…"
    if max_w <= draw.textlength(ellipsis, font=font):
        return ""
    out = ""
    for ch in text:
        if draw.textlength(out + ch + ellipsis, font=font) > max_w:
            break
        out += ch
    return out + ellipsis


def _build_static_layers(
    segments: list[Segment],
    width: int,
    height: int,
    draw_labels: bool,
):
    """构建静态图层。

    进度条满宽贴在画面最底部, 直角(贴边样式); 章节文字垂直居中绘制在条内,
    预渲染黑/白两套文字, 逐帧按填充位置切换(红底白字 / 灰轨黑字)。

    返回 (base, text_dark, text_light, ticks, (y0, bar_h)),
    前三个文字/刻度图层形状均为 (bar_h, width, 4)。
    """
    duration = segments[-1].end
    bar_h = max(8, round(height * 0.055))          # 1080p -> 59px, 加粗
    y0 = height - bar_h                            # 紧贴画面最底边
    bar_w = width                                  # 满宽

    def to_x(t: float) -> int:
        return round(t / duration * bar_w)

    # 全帧底色: 仅底部条带区域为轨道色, 其余全透明
    base = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    ImageDraw.Draw(base).rectangle(
        [0, y0, width - 1, height - 1], fill=TRACK_RGBA
    )

    # 条内文字双图层(黑字用于未播放轨道, 白字用于红色填充区)
    text_dark = Image.new("RGBA", (bar_w, bar_h), (0, 0, 0, 0))
    text_light = Image.new("RGBA", (bar_w, bar_h), (0, 0, 0, 0))
    if draw_labels:
        font = _load_font(max(10, round(bar_h * 0.46)))
        d_dark = ImageDraw.Draw(text_dark)
        d_light = ImageDraw.Draw(text_light)
        pad = max(8, round(bar_h * 0.22))
        mid_y = bar_h // 2
        for i, seg in enumerate(segments):
            if not seg.label:
                continue
            tx = to_x(seg.start) + pad
            next_x = to_x(segments[i + 1].start) if i + 1 < len(segments) \
                else bar_w
            label = _fit_label(seg.label, d_dark, font, next_x - tx - pad)
            if not label:
                continue
            d_dark.text((tx, mid_y), label, font=font,
                        fill=LABEL_TRACK_RGBA, anchor="lm")
            d_light.text((tx, mid_y), label, font=font,
                         fill=LABEL_FILLED_RGBA, anchor="lm")

    # 章节分界刻度(条内竖线, 逐帧绘制在填充与文字之上)
    ticks = Image.new("RGBA", (bar_w, bar_h), (0, 0, 0, 0))
    td = ImageDraw.Draw(ticks)
    tick_w = max(2, round(width / 640))            # 1080p -> 3px
    for seg in segments[1:]:
        tx = to_x(seg.start)
        td.rectangle([tx - tick_w // 2, 0,
                      tx - tick_w // 2 + tick_w - 1, bar_h - 1],
                     fill=TICK_RGBA)

    return (np.asarray(base, dtype=np.uint8),
            np.asarray(text_dark, dtype=np.uint8),
            np.asarray(text_light, dtype=np.uint8),
            np.asarray(ticks, dtype=np.uint8),
            (y0, bar_h))


class _Layers(NamedTuple):
    """条内静态图层及其预计算的 alpha/rgb 浮点视图。"""
    text_dark: np.ndarray
    text_light: np.ndarray
    ticks: np.ndarray
    td_a: np.ndarray
    td_rgb: np.ndarray
    tl_a: np.ndarray
    tl_rgb: np.ndarray
    tk_a: np.ndarray
    tk_rgb: np.ndarray


def _prepare_layers(
    segments: list[Segment], width: int, height: int, draw_labels: bool
):
    """构建静态图层并预拆分 alpha/rgb, 供视频渲染与单帧预览共用。"""
    base, text_dark, text_light, ticks, (y0, bar_h) = _build_static_layers(
        segments, width, height, draw_labels
    )
    layers = _Layers(
        text_dark=text_dark, text_light=text_light, ticks=ticks,
        td_a=text_dark[..., 3:4].astype(np.float32) / 255.0,
        td_rgb=text_dark[..., :3].astype(np.float32),
        tl_a=text_light[..., 3:4].astype(np.float32) / 255.0,
        tl_rgb=text_light[..., :3].astype(np.float32),
        tk_a=ticks[..., 3:4].astype(np.float32) / 255.0,
        tk_rgb=ticks[..., :3].astype(np.float32),
    )
    return base, y0, bar_h, width, layers


def _alpha_blend(dst: np.ndarray, src_rgb: np.ndarray,
                 src_a: np.ndarray, src_a_u8: np.ndarray) -> None:
    """对条带做 in-place alpha 合成, 同时抬升 dst 的 alpha。"""
    rgb = dst[..., :3].astype(np.float32)
    dst[..., :3] = (src_rgb * src_a + rgb * (1.0 - src_a)).astype(np.uint8)
    dst[..., 3] = np.maximum(dst[..., 3],
                             src_a_u8.reshape(dst.shape[:2]))


def _paint_band(band: np.ndarray, fill_w: int, bar_w: int,
                layers: _Layers) -> None:
    """在底部条带上按当前填充宽度绘制一帧(in-place)。

    视频逐帧渲染与单帧预览共用此函数, 保证预览效果与成片完全一致。
    """
    # 底色: 整条轨道灰 + 已播放区域红
    band[..., :3] = TRACK_RGBA[:3]
    band[..., 3] = TRACK_RGBA[3]
    if fill_w > 0:
        band[:, :fill_w, :3] = FILL_RGBA[:3]
        band[:, :fill_w, 3] = FILL_RGBA[3]

    # 条内文字: 红底上白字, 未播放轨道上黑字(按填充位置列切分)
    if fill_w > 0:
        _alpha_blend(band[:, :fill_w],
                     layers.tl_rgb[:, :fill_w], layers.tl_a[:, :fill_w],
                     layers.text_light[..., 3:4][:, :fill_w])
    if fill_w < bar_w:
        _alpha_blend(band[:, fill_w:],
                     layers.td_rgb[:, fill_w:], layers.td_a[:, fill_w:],
                     layers.text_dark[..., 3:4][:, fill_w:])

    # 章节分界刻度压在最上层
    _alpha_blend(band, layers.tk_rgb, layers.tk_a, layers.ticks[..., 3:4])


def render_preview_frame(
    segments: list[Segment],
    out_path: str | Path,
    at: float | None = None,
    transparent: bool = False,
    width: int = 1920,
    height: int = 1080,
    draw_labels: bool = True,
) -> Path:
    """渲染进度条在某一时刻的单帧图片用于快速预览(不经过 ffmpeg)。

    参数:
        at: 时间点(秒)。None 时取视频正中间(duration/2)。
        transparent: True 输出带透明背景的 PNG(等同 MOV 效果);
                     False 输出白底 PNG(等同 MP4 效果)。
    返回:
        实际保存的图片路径。
    """
    duration = segments[-1].end
    if at is None:
        at = duration / 2
    at = min(max(float(at), 0.0), duration)
    fill_w = min(width, max(0, round(at / duration * width)))
    if at >= duration:
        fill_w = width

    base, y0, bar_h, bar_w, layers = _prepare_layers(
        segments, width, height, draw_labels
    )
    frame = base.copy()
    _paint_band(frame[y0:y0 + bar_h], fill_w, bar_w, layers)

    img = Image.fromarray(frame)    # HxWx4 数组自动推断为 RGBA
    if not transparent:
        flat = Image.new("RGB", (width, height), (255, 255, 255))
        flat.paste(img, mask=img.split()[3])
        img = flat

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path)
    print(f"[预览帧] t={at:.2f}s (填充 {fill_w / width * 100:.1f}%)"
          f"{' 透明背景' if transparent else ' 白色背景'}")
    print(f"已保存: {out_path}")
    return out_path


def render_progress_video(
    segments: list[Segment],
    mp4_path: str | Path,
    mov_path: str | Path,
    width: int = 1920,
    height: int = 1080,
    fps: int = 30,
    draw_labels: bool = True,
    ffmpeg: str = "ffmpeg",
) -> None:
    """渲染进度条视频, 一次编码同时产出白底 MP4 与透明 MOV。"""
    duration = segments[-1].end
    total_frames = int(round(duration * fps))
    if total_frames <= 0:
        raise ValueError("视频时长为 0")

    base, y0, bar_h, bar_w, layers = _prepare_layers(
        segments, width, height, draw_labels
    )
    y1 = y0 + bar_h

    mp4_path, mov_path = Path(mp4_path), Path(mov_path)
    mp4_path.parent.mkdir(parents=True, exist_ok=True)
    mov_path.parent.mkdir(parents=True, exist_ok=True)

    # 同一条 raw RGBA 流: 一路压到白底输出 H.264, 一路保留 alpha 输出 ProRes4444
    cmd = [
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
        "-f", "rawvideo", "-pix_fmt", "rgba",
        "-s", f"{width}x{height}", "-r", str(fps), "-i", "-",
        "-filter_complex",
        f"color=white:s={width}x{height}:r={fps}[bg];"
        f"[bg][0:v]overlay=shortest=1:format=auto[flat]",
        "-map", "[flat]",
        "-c:v", "libx264", "-crf", "18", "-preset", "medium",
        "-pix_fmt", "yuv420p", "-movflags", "+faststart",
        "-an", str(mp4_path),
        "-map", "0:v",
        "-c:v", "prores_ks", "-profile:v", "4",
        "-pix_fmt", "yuva444p10le", "-vendor", "apl0",
        "-an", str(mov_path),
    ]

    log = tempfile.NamedTemporaryFile(
        mode="wb", suffix=".log", delete=False, prefix="ffmpeg_"
    )
    log_path = Path(log.name)
    frame_bytes = width * height * 4
    try:
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=log)
        frame = base.copy()
        band = frame[y0:y1]                    # 底部条带视图, 逐帧整幅重绘
        last_pct = -10
        for i in range(total_frames):
            t = i / fps
            fill_w = min(bar_w, max(0, round(t / duration * bar_w)))
            if i == total_frames - 1:
                fill_w = bar_w               # 末帧强制 100%, 进度条正好填满

            _paint_band(band, fill_w, bar_w, layers)

            proc.stdin.write(frame.tobytes())

            pct = int((i + 1) * 100 / total_frames)
            if pct >= last_pct + 10:
                print(f"\r渲染进度: {pct}%", end="", flush=True)
                last_pct = pct

        proc.stdin.close()
        code = proc.wait()
        print()
        if code != 0:
            raise RuntimeError(
                f"ffmpeg 编码失败(退出码 {code}), 日志: {log_path}"
            )
    finally:
        log.close()

    print(f"时长 {duration:.2f}s / {total_frames} 帧, "
          f"{width}x{height}@{fps}fps")
    print(f"[白底 MP4 ] {mp4_path} ({mp4_path.stat().st_size / 1e6:.2f} MB)")
    print(f"[透明 MOV ] {mov_path} ({mov_path.stat().st_size / 1e6:.2f} MB)")
    if code == 0 and log_path.exists():
        log_path.unlink()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="视频进度条生成 demo: 时间段文件 -> 白底MP4 + 透明MOV"
    )
    parser.add_argument("segments", help="时间段文件路径")
    parser.add_argument("--mp4", help="白底 MP4 输出路径")
    parser.add_argument("--mov", help="透明 MOV 输出路径")
    parser.add_argument("--width", type=int, default=1920)
    parser.add_argument("--height", type=int, default=1080)
    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument("--no-labels", action="store_true",
                        help="不绘制章节标题文字")
    parser.add_argument("--preview", action="store_true",
                        help="只生成一张预览帧 PNG(不渲染视频, 秒出图)")
    parser.add_argument("--at",
                        help="预览帧时间点: 秒/MM:SS/HH:MM:SS, 默认视频正中间")
    parser.add_argument("--frame-out",
                        help="预览帧输出路径(默认与输入同目录), .png 格式")
    parser.add_argument("--transparent-frame", action="store_true",
                        help="预览帧保留透明背景(等同 MOV 效果), 默认白底")
    parser.add_argument("--ffmpeg", default=shutil.which("ffmpeg") or "ffmpeg",
                        help="ffmpeg 可执行文件路径")
    args = parser.parse_args(argv)

    try:
        segments = parse_segments_file(args.segments)
    except (OSError, ValueError) as exc:
        print(f"输入文件解析失败: {exc}", file=sys.stderr)
        return 1

    print(f"共 {len(segments)} 个时间段:")
    for s in segments:
        title = f"  {s.label}" if s.label else ""
        print(f"  {s.start:7.2f} - {s.end:7.2f}{title}")

    # 单帧预览模式: 不启动 ffmpeg, 直接输出 PNG
    if args.preview:
        try:
            at = None if args.at is None else parse_time(args.at)
        except ValueError as exc:
            print(f"--at 时间解析失败: {exc}", file=sys.stderr)
            return 1
        stem = Path(args.segments).with_suffix("")
        tag = "alpha" if args.transparent_frame else "white"
        t_tag = "mid" if at is None else str(at).replace(".", "_")
        frame_path = args.frame_out or f"{stem}_preview_{tag}_{t_tag}s.png"
        try:
            render_preview_frame(
                segments, frame_path, at=at,
                transparent=args.transparent_frame,
                width=args.width, height=args.height,
                draw_labels=not args.no_labels,
            )
        except (OSError, ValueError) as exc:
            print(f"预览帧生成失败: {exc}", file=sys.stderr)
            return 2
        return 0

    stem = Path(args.segments).with_suffix("")
    mp4_path = args.mp4 or f"{stem}_white.mp4"
    mov_path = args.mov or f"{stem}_alpha.mov"

    try:
        render_progress_video(
            segments, mp4_path, mov_path,
            width=args.width, height=args.height, fps=args.fps,
            draw_labels=not args.no_labels, ffmpeg=args.ffmpeg,
        )
    except Exception as exc:
        print(f"渲染失败: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
