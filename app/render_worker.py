# -*- coding: utf-8 -*-
"""后台渲染线程: 调用 video_bar.render_progress_video, 通过信号回报进度。"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QThread, Signal

import video_bar


class RenderWorker(QThread):
    """在独立线程中跑渲染, 避免阻塞 GUI。

    信号:
        progress(int):   0-100 百分比
        log(str):        日志行
        done(bool, str, list): 成功标志, 消息, 输出文件路径列表
    """

    progress = Signal(int)
    log = Signal(str)
    done = Signal(bool, str, list)

    def __init__(self, segments: list[video_bar.Segment], *,
                 mp4_path: str | Path | None = None,
                 mov_path: str | Path | None = None,
                 webm_path: str | Path | None = None,
                 width: int = 1920, height: int = 1080, fps: int = 30,
                 draw_labels: bool = True,
                 mascot_path: str | Path | None = None,
                 parent=None) -> None:
        super().__init__(parent)
        self._segments = segments
        self._mp4 = Path(mp4_path) if mp4_path else None
        self._mov = Path(mov_path) if mov_path else None
        self._webm = Path(webm_path) if webm_path else None
        self._w, self._h, self._fps = width, height, fps
        self._draw_labels = draw_labels
        self._mascot = mascot_path
        self._cancelled = False

    def cancel(self) -> None:
        """请求取消; 渲染循环下一帧生效。"""
        self._cancelled = True

    def _outputs(self) -> list[str]:
        return [str(p) for p in (self._mp4, self._mov, self._webm) if p]

    def _on_progress(self, done_frames: int, total_frames: int) -> None:
        self.progress.emit(int(done_frames * 100 / max(total_frames, 1)))

    def run(self) -> None:
        try:
            video_bar.render_progress_video(
                self._segments, self._mp4, self._mov, self._webm,
                width=self._w, height=self._h, fps=self._fps,
                draw_labels=self._draw_labels,
                mascot_path=self._mascot,
                progress_cb=self._on_progress,
                cancel_cb=lambda: self._cancelled,
            )
        except Exception as exc:  # 任何渲染错误都回报给界面
            msg = str(exc)
            if "已取消" in msg:
                for p in (self._mp4, self._mov, self._webm):
                    try:
                        if p and p.exists():
                            p.unlink()      # 清掉半成品文件
                    except OSError:
                        pass
                self.done.emit(False, "渲染已取消", [])
            else:
                self.log.emit(f"渲染失败: {msg}")
                self.done.emit(False, msg, [])
            return
        self.done.emit(True, "渲染完成", self._outputs())
