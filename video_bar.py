# -*- coding: utf-8 -*-
"""
视频进度条生成器 (demo)

输入: 包含进度条时间段(章节)信息的文本文件, 每行一个时间段:
    结束时间, 标题(可省略)            # 开始时间自动接上一段, 第一段从 0 开始
    开始时间, 结束时间, 标题(可省略)   # 也可显式指定区间(兼容旧格式)
    例:
        15, 开场
        01:20 第一章
        00:15 - 01:20 显式区间
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
from datetime import datetime
from pathlib import Path
from typing import Callable, NamedTuple

import numpy as np
from PIL import Image, ImageDraw, ImageFont

# ---------------------------------------------------------------------------
# 视觉样式常量 (RGBA)。透明版本中 track/tick 使用半透明, 叠加到白底下呈浅灰色
# ---------------------------------------------------------------------------
FILL_RGBA = (46, 79, 60, 255)       # 已播放部分: 深绿色
TRACK_RGBA = (232, 245, 233, 160)   # 未播放轨道: 极浅绿毛玻璃
TICK_RGBA = (96, 80, 64, 220)       # 章节分界刻度: 未播放区低饱和低亮度棕色
TICK_FILLED_RGBA = (255, 255, 255, 220)  # 章节分界刻度: 已播放区白色
LABEL_FILLED_RGBA = (255, 255, 255, 235)  # 条内文字: 已播放区域白色(深绿底)
LABEL_TRACK_RGBA = (0, 0, 0, 235)    # 条内文字: 未播放区域黑色(浅绿底)
LABEL_FONT_RATIO = 0.90             # 字号相对进度条高度的比例(只放大字, 不加粗条)


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
    """解析时间段文件, 返回按开始时间排序的 Segment 列表。

    每行两种写法:
        结束时间, 标题(可省略)           # 开始时间自动接上一段, 第一段从 0 开始
        开始时间, 结束时间, 标题(可省略)  # 显式区间(兼容旧格式)
    """
    lines = Path(path).read_text(encoding="utf-8-sig").splitlines()
    segments: list[Segment] = []
    cursor = 0.0  # 上一段结束时间, 省略开始时间时由此接续
    for lineno, raw in enumerate(lines, 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        tokens = list(_TOKEN_RE.finditer(line))
        if not tokens:
            raise ValueError(f"第 {lineno} 行没有时间值: {raw.rstrip()!r}")
        try:
            if len(tokens) == 1:
                end = parse_time(tokens[0].group())
                start = cursor
            else:
                start = parse_time(tokens[0].group())
                end = parse_time(tokens[1].group())
        except ValueError as exc:
            raise ValueError(f"第 {lineno} 行时间解析失败: {exc}") from exc
        label = line[tokens[-1].end():].strip(" \t,;，；-–—|\"'")
        segments.append(Segment(start, end, label))
        cursor = end

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
# 优先含中文(CJK)字形的常规字重, 找不到再退粗体
_FONT_FAMILIES = (
    # Windows
    "msyh.ttc", "msyhl.ttc", "msyhbd.ttc", "simhei.ttf",
    "arial.ttf", "segoeui.ttf",
    # macOS
    "PingFang.ttc", "Hiragino Sans GB.ttc", "STHeiti Medium.ttc",
    # Linux (Noto CJK 常位于 fonts 目录的子文件夹中)
    "NotoSansCJK-Regular.ttc", "NotoSansCJK-Regular.otf",
    "NotoSansCJK-Bold.ttc", "wqy-microhei.ttc", "wqy-zenhei.ttc",
    # 西文兜底
    "DejaVuSans.ttf", "Arial.ttf",
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
    预渲染黑/白两套文字, 逐帧按填充位置切换(深绿底白字 / 浅绿底黑字)。
    章节分界刻度也分两套: 已播放区白色, 未播放区棕色。

    返回 (base, text_dark, text_light, ticks, ticks_filled, (y0, bar_h)),
    文字/刻度图层形状均为 (bar_h, width, 4)。
    """
    duration = segments[-1].end
    # 元素尺寸以 1080p 为基准: 小画面(如 1920x270 条带)不缩水, 大画面等比放大
    scale = max(height / 1080, 1.0)
    bar_h = max(6, round(38 * scale))                # 基准 38px, 收窄
    y0 = height - bar_h                              # 紧贴画面最底边
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
        # 字号只与条高挂钩(变大字但不加粗条): 38px 条高 -> 约 34px 字号
        font = _load_font(max(12, round(bar_h * LABEL_FONT_RATIO)))
        d_dark = ImageDraw.Draw(text_dark)
        d_light = ImageDraw.Draw(text_light)
        pad = max(8, round(bar_h * 0.22))
        mid_y = bar_h // 2
        for i, seg in enumerate(segments):
            if not seg.label:
                continue
            sx = to_x(seg.start)
            next_x = to_x(segments[i + 1].start) if i + 1 < len(segments) \
                else bar_w
            max_w = next_x - sx - 2 * pad
            cx = sx + (next_x - sx) // 2          # 章节内水平居中
            label = _fit_label(seg.label, d_dark, font, max_w)
            if not label:
                continue
            # 微阴影: 保证白字在浅色毛玻璃上也可读
            shadow_off = max(1, bar_h // 20)
            for d in (d_dark, d_light):
                d.text((cx + shadow_off, mid_y + shadow_off), label,
                       font=font, fill=(0, 0, 0, 140), anchor="mm")
            d_dark.text((cx, mid_y), label, font=font,
                        fill=LABEL_TRACK_RGBA, anchor="mm")
            d_light.text((cx, mid_y), label, font=font,
                         fill=LABEL_FILLED_RGBA, anchor="mm")

    # 章节分界刻度(条内竖线, 逐帧压在最上层): 未播放区棕色 / 已播放区白色
    ticks = Image.new("RGBA", (bar_w, bar_h), (0, 0, 0, 0))
    ticks_filled = Image.new("RGBA", (bar_w, bar_h), (0, 0, 0, 0))
    td = ImageDraw.Draw(ticks)
    tdf = ImageDraw.Draw(ticks_filled)
    tick_w = max(2, round(width / 640))            # 1080p -> 3px
    for seg in segments[1:]:
        tx = to_x(seg.start)
        box = [tx - tick_w // 2, 0,
               tx - tick_w // 2 + tick_w - 1, bar_h - 1]
        td.rectangle(box, fill=TICK_RGBA)
        tdf.rectangle(box, fill=TICK_FILLED_RGBA)

    return (np.asarray(base, dtype=np.uint8),
            np.asarray(text_dark, dtype=np.uint8),
            np.asarray(text_light, dtype=np.uint8),
            np.asarray(ticks, dtype=np.uint8),
            np.asarray(ticks_filled, dtype=np.uint8),
            (y0, bar_h))


class _Layers(NamedTuple):
    """条内静态图层及其预计算的 alpha/rgb 浮点视图。"""
    text_dark: np.ndarray
    text_light: np.ndarray
    ticks: np.ndarray
    ticks_filled: np.ndarray
    td_a: np.ndarray
    td_rgb: np.ndarray
    tl_a: np.ndarray
    tl_rgb: np.ndarray
    tk_a: np.ndarray
    tk_rgb: np.ndarray
    tkf_a: np.ndarray
    tkf_rgb: np.ndarray


def _prepare_layers(
    segments: list[Segment], width: int, height: int, draw_labels: bool
):
    """构建静态图层并预拆分 alpha/rgb, 供视频渲染与单帧预览共用。"""
    base, text_dark, text_light, ticks, ticks_filled, (y0, bar_h) = \
        _build_static_layers(segments, width, height, draw_labels)
    layers = _Layers(
        text_dark=text_dark, text_light=text_light,
        ticks=ticks, ticks_filled=ticks_filled,
        td_a=text_dark[..., 3:4].astype(np.float32) / 255.0,
        td_rgb=text_dark[..., :3].astype(np.float32),
        tl_a=text_light[..., 3:4].astype(np.float32) / 255.0,
        tl_rgb=text_light[..., :3].astype(np.float32),
        tk_a=ticks[..., 3:4].astype(np.float32) / 255.0,
        tk_rgb=ticks[..., :3].astype(np.float32),
        tkf_a=ticks_filled[..., 3:4].astype(np.float32) / 255.0,
        tkf_rgb=ticks_filled[..., :3].astype(np.float32),
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

    # 章节分界刻度压在最上层: 已播放区白线, 未播放区棕线(按填充位置列切分)
    if fill_w > 0:
        _alpha_blend(band[:, :fill_w],
                     layers.tkf_rgb[:, :fill_w], layers.tkf_a[:, :fill_w],
                     layers.ticks_filled[..., 3:4][:, :fill_w])
    if fill_w < bar_w:
        _alpha_blend(band[:, fill_w:],
                     layers.tk_rgb[:, fill_w:], layers.tk_a[:, fill_w:],
                     layers.ticks[..., 3:4][:, fill_w:])


def load_mascot_frames(gif_path: str | Path, target_height: int
                       ) -> tuple[list[Image.Image], list[float], float]:
    """加载 GIF 逐帧并按 target_height 等比缩放, 保留透明通道。

    返回 (帧列表, 每帧时长秒列表, 一轮总时长秒)。
    """
    im = Image.open(gif_path)
    w0, h0 = im.size
    target_width = max(1, round(target_height * w0 / h0))
    frames: list[Image.Image] = []
    durations: list[float] = []
    for i in range(im.n_frames):
        im.seek(i)
        frames.append(im.convert("RGBA").resize(
            (target_width, target_height), Image.LANCZOS))
        durations.append(im.info.get("duration", 100) / 1000.0)
    total = sum(durations) or 1.0
    return frames, durations, total


def _paste_mascot(frame: np.ndarray,
                  mascot_frames: list[Image.Image],
                  mascot_durations: list[float],
                  mascot_total: float,
                  t: float, fill_w: int, bar_y_top: int, width: int) -> None:
    """按时间 t 选 GIF 帧, 贴到进度条填充右端的顶部(in-place)。"""
    elapsed = t % mascot_total
    idx = 0
    acc = 0.0
    for i, d in enumerate(mascot_durations):
        if acc + d > elapsed:
            idx = i
            break
        acc += d
    mascot = mascot_frames[idx]
    mw, mh = mascot.size
    cx = min(max(fill_w, mw // 2), width - mw // 2)
    x = cx - mw // 2
    y = bar_y_top - mh                          # 小驴脚踩进度条顶边
    pil = Image.fromarray(frame)
    pil.paste(mascot, (x, y), mascot)
    frame[:] = np.asarray(pil)


def render_preview_frame(
    segments: list[Segment],
    out_path: str | Path,
    at: float | None = None,
    transparent: bool = False,
    width: int = 1920,
    height: int = 1080,
    draw_labels: bool = True,
    mascot_path: str | Path | None = None,
    mascot_height: int | None = None,
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

    if mascot_path:
        mh = mascot_height or round(140 * max(height / 1080, 1.0))
        mf, md, mt = load_mascot_frames(mascot_path, mh)
        _paste_mascot(frame, mf, md, mt, at, fill_w, y0, width)

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
    mp4_path: str | Path | None,
    mov_path: str | Path | None,
    webm_path: str | Path | None = None,
    width: int = 1920,
    height: int = 1080,
    fps: int = 30,
    draw_labels: bool = True,
    ffmpeg: str = "ffmpeg",
    mascot_path: str | Path | None = None,
    mascot_height: int | None = None,
    frame_start: int = 0,
    frame_end: int | None = None,
    progress_cb: Callable[[int, int], None] | None = None,
    cancel_cb: Callable[[], bool] | None = None,
) -> None:
    """渲染进度条视频, 按传入路径输出对应格式, 传 None 跳过该格式。

    支持任意组合: 白底 MP4(H.264) / 透明 MOV(ProRes4444) / 透明 WebM(VP9)。
    frame_start/frame_end 用于分块并行渲染(帧区间左闭右开)。
    progress_cb(已完成帧数, 总帧数) 每帧回调, 供 GUI 更新进度条。
    cancel_cb() 返回 True 时终止编码并抛出 RuntimeError("渲染已取消")。
    """
    duration = segments[-1].end
    total_frames = int(round(duration * fps))
    if total_frames <= 0:
        raise ValueError("视频时长为 0")
    i0 = max(0, int(frame_start))
    i1 = total_frames if frame_end is None else max(i0 + 1, min(int(frame_end), total_frames))

    base, y0, bar_h, bar_w, layers = _prepare_layers(
        segments, width, height, draw_labels
    )
    y1 = y0 + bar_h

    # 小驴播放头: 循环外只加载一次
    mascot = None
    if mascot_path:
        mh = mascot_height or round(140 * max(height / 1080, 1.0))
        mascot = load_mascot_frames(mascot_path, mh)
        print(f"[播放头] {mascot_path} -> {len(mascot[0])} 帧, "
              f"尺寸 {mascot[0][0].size}, 缩放高 {mh}px")

    mp4_path = Path(mp4_path) if mp4_path else None
    mov_path = Path(mov_path) if mov_path else None
    webm_path = Path(webm_path) if webm_path else None
    out_paths = [p for p in (mp4_path, mov_path, webm_path) if p]
    if not out_paths:
        raise ValueError("至少需要指定一个输出文件 (mp4/mov/webm)")
    for p in out_paths:
        p.parent.mkdir(parents=True, exist_ok=True)

    # 同一条 raw RGBA 流分发给各编码器; MP4 需先叠加白底再压 H.264
    cmd = [
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
        "-f", "rawvideo", "-pix_fmt", "rgba",
        "-s", f"{width}x{height}", "-r", str(fps), "-i", "-",
    ]
    if mp4_path:
        cmd += [
            "-filter_complex",
            f"color=white:s={width}x{height}:r={fps}[bg];"
            f"[bg][0:v]overlay=shortest=1:format=auto[flat]",
            "-map", "[flat]",
            "-c:v", "libx264", "-crf", "18", "-preset", "medium",
            "-pix_fmt", "yuv420p", "-movflags", "+faststart",
            "-an", str(mp4_path),
        ]
    if mov_path:
        cmd += [
            "-map", "0:v",
            "-c:v", "prores_ks", "-profile:v", "4",
            "-pix_fmt", "yuva444p10le", "-vendor", "apl0",
            "-an", str(mov_path),
        ]
    if webm_path:
        cmd += [
            "-map", "0:v",
            "-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p",
            "-auto-alt-ref", "0",            # alpha 通道必须关闭 auto-alt-ref
            "-metadata:s:v", "alpha_mode=1", # 显式声明 alpha, 否则被丢弃
            "-b:v", "0", "-crf", "28",       # 质量模式
            "-deadline", "good", "-cpu-used", "4",
            "-an", str(webm_path),
        ]

    log = tempfile.NamedTemporaryFile(
        mode="wb", suffix=".log", delete=False, prefix="ffmpeg_"
    )
    log_path = Path(log.name)
    frame_bytes = width * height * 4
    try:
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=log)
        last_pct = -10
        for i in range(i0, i1):
            if cancel_cb is not None and cancel_cb():
                proc.stdin.close()
                proc.terminate()
                proc.wait()
                raise RuntimeError("渲染已取消")
            t = i / fps
            fill_w = min(bar_w, max(0, round(t / duration * bar_w)))
            if i == total_frames - 1:
                fill_w = bar_w               # 末帧强制 100%, 进度条正好填满

            # 每帧从 base 重置, 避免小驴等移动元素残留形成拖影
            frame = base.copy()
            _paint_band(frame[y0:y1], fill_w, bar_w, layers)

            if mascot is not None:
                _paste_mascot(frame, mascot[0], mascot[1], mascot[2],
                              t, fill_w, y0, bar_w)

            proc.stdin.write(frame.tobytes())

            if progress_cb is not None:
                progress_cb(i - i0 + 1, i1 - i0)

            pct = int((i - i0 + 1) * 100 / (i1 - i0))
            if pct >= last_pct + 10:
                print(f"\r[{i0}-{i1}] 渲染进度: {pct}%", end="", flush=True)
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
    if mp4_path:
        print(f"[白底 MP4 ] {mp4_path} ({mp4_path.stat().st_size / 1e6:.2f} MB)")
    if mov_path:
        _patch_prores_vendor(mov_path)
        print(f"[透明 MOV ] {mov_path} ({mov_path.stat().st_size / 1e6:.2f} MB)")
    if webm_path:
        print(f"[透明 WebM] {webm_path} "
              f"({webm_path.stat().st_size / 1e6:.2f} MB)")
    if code == 0 and log_path.exists():
        log_path.unlink()


def _patch_prores_vendor(mov_path: Path) -> None:
    """把 ProRes 轨道的 vendor 标记 FFMP 改为 apl0。

    ffmpeg 的 prores_ks 编码器忽略 -vendor 参数, 写出的 vendor_id 是 FFMP,
    剪映/QuickTime 等按 Apple 规范解析的软件对 FFMP 标记的文件兼容性差。
    vendor 字段位于 moov 原子(文件头或尾部), 只扫描首尾, 不触碰帧数据区。
    """
    with open(mov_path, "r+b") as f:
        head = f.read(1 << 20)
        if b"FFMP" in head:
            f.seek(0)
            f.write(head.replace(b"FFMP", b"apl0"))
        f.seek(0, 2)
        size = f.tell()
        tail_len = min(size, 20 << 20)
        f.seek(size - tail_len)
        tail = f.read(tail_len)
        if b"FFMP" in tail:
            f.seek(size - tail_len)
            f.write(tail.replace(b"FFMP", b"apl0"))


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
    parser.add_argument("--webm", action="store_true",
                        help="额外输出透明 WebM(VP9+alpha), 剪映/网页兼容性更好")
    parser.add_argument("--only-webm", action="store_true",
                        help="只输出透明 WebM, 跳过 MP4 与 MOV")
    parser.add_argument("--only-mov", action="store_true",
                        help="只输出透明 MOV, 跳过 MP4 与 WebM")
    parser.add_argument("--fs", type=int, default=0, help=argparse.SUPPRESS)
    parser.add_argument("--fe", type=int, help=argparse.SUPPRESS)
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
    parser.add_argument("--mascot",
                        help="小驴/播放头 GIF 路径, 贴在进度条填充右端随进度移动")
    parser.add_argument("--mascot-height", type=int,
                        help="播放头缩放高度(像素), 默认画面高度的 7.5%%")
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
                mascot_path=args.mascot,
                mascot_height=args.mascot_height,
            )
        except (OSError, ValueError) as exc:
            print(f"预览帧生成失败: {exc}", file=sys.stderr)
            return 2
        return 0

    # 默认输出: 输入文件同目录下的 progress_bar_<时间戳>.<ext>
    out_dir = Path(args.segments).resolve().parent
    default_name = "progress_bar_" + datetime.now().strftime("%Y%m%d_%H%M%S")
    webm_path = (str(out_dir / f"{default_name}.webm")
                 if (args.webm or args.only_webm) else None)
    if args.only_webm:
        mp4_path = mov_path = None
    elif args.only_mov:
        mp4_path = webm_path = None
        mov_path = args.mov or str(out_dir / f"{default_name}.mov")
    else:
        mp4_path = args.mp4 or str(out_dir / f"{default_name}.mp4")
        mov_path = args.mov or str(out_dir / f"{default_name}.mov")

    try:
        render_progress_video(
            segments, mp4_path, mov_path, webm_path,
            width=args.width, height=args.height, fps=args.fps,
            draw_labels=not args.no_labels, ffmpeg=args.ffmpeg,
            mascot_path=args.mascot,
            mascot_height=args.mascot_height,
            frame_start=args.fs, frame_end=args.fe,
        )
    except Exception as exc:
        print(f"渲染失败: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
