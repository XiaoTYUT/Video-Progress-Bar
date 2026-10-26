# -*- coding: utf-8 -*-
"""QML <-> video_bar 桥接层。

QML 侧通过上下文属性 `bridge` 调用本对象的 Slot / 接收 Signal。
渲染任务委托给 RenderWorker(QThread)。
"""

from __future__ import annotations

import sys
import tempfile
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import video_bar  # noqa: E402
from render_worker import RenderWorker  # noqa: E402

EXAMPLE_SEGMENTS = ROOT / "examples" / "segments.txt"
DEFAULT_MASCOT = ROOT / "donkey_hiker_16-57.gif"


def _to_local(path_or_url: str) -> str:
    """QML 传过来的可能是 file:/// URL 或普通路径, 统一转成本地路径。"""
    if path_or_url.startswith("file:"):
        return QUrl(path_or_url).toLocalFile()
    return path_or_url


class Bridge(QObject):
    """暴露给 QML 的单一入口对象。"""

    fileLoaded = Signal(str, str)              # path, text
    fileSaved = Signal(str)                    # path
    segmentsParsed = Signal(list, float)       # [{start,end,label}], 总时长
    parseFailed = Signal(str)                  # 错误消息
    previewReady = Signal(str)                 # 预览图 file url
    previewFailed = Signal(str)
    renderProgress = Signal(int)               # 0-100
    renderLog = Signal(str)                    # 日志行
    renderFinished = Signal(bool, str, list)   # ok, 消息, 输出文件列表

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._worker: RenderWorker | None = None
        self._current_file: Path | None = None

    # ------------------------------------------------------------------
    # 小工具
    # ------------------------------------------------------------------
    @Slot(str, result=str)
    def urlToPath(self, url: str) -> str:
        return QUrl(url).toLocalFile()

    @Slot(str, result=str)
    def dirOf(self, path: str) -> str:
        return str(Path(path).parent)

    @Slot(result=str)
    def defaultMascot(self) -> str:
        return str(DEFAULT_MASCOT) if DEFAULT_MASCOT.exists() else ""

    @Slot(str)
    def openFolder(self, path: str) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    # ------------------------------------------------------------------
    # 时间段文件读写
    # ------------------------------------------------------------------
    @Slot()
    def loadExample(self) -> None:
        if EXAMPLE_SEGMENTS.exists():
            self._load_path(EXAMPLE_SEGMENTS)

    @Slot(str)
    def loadFile(self, path_or_url: str) -> None:
        self._load_path(Path(_to_local(path_or_url)))

    def _load_path(self, path: Path) -> None:
        try:
            text = path.read_text(encoding="utf-8-sig")
        except OSError as exc:
            self.parseFailed.emit(f"读取失败: {exc}")
            return
        self._current_file = path
        self.fileLoaded.emit(str(path), text)

    @Slot(str, str)
    def saveFile(self, path_or_url: str, text: str) -> None:
        path = Path(_to_local(path_or_url))
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        except OSError as exc:
            self.parseFailed.emit(f"保存失败: {exc}")
            return
        self._current_file = path
        self.fileSaved.emit(str(path))

    # ------------------------------------------------------------------
    # 解析
    # ------------------------------------------------------------------
    @staticmethod
    def _parse_text(text: str) -> list[video_bar.Segment]:
        tmp = Path(tempfile.gettempdir()) / "video_toolkit_segments.txt"
        tmp.write_text(text, encoding="utf-8")
        return video_bar.parse_segments_file(tmp)

    @Slot(str)
    def parseSegments(self, text: str) -> None:
        if not text.strip():
            self.segmentsParsed.emit([], 0.0)
            return
        try:
            segments = self._parse_text(text)
        except (OSError, ValueError) as exc:
            self.parseFailed.emit(str(exc))
            return
        data = [{"start": s.start, "end": s.end, "label": s.label}
                for s in segments]
        self.segmentsParsed.emit(data, segments[-1].end)

    # ------------------------------------------------------------------
    # 单帧预览
    # ------------------------------------------------------------------
    @Slot(str, str, bool, int, int, bool, str)
    def makePreview(self, text: str, at_text: str, transparent: bool,
                    width: int, height: int, draw_labels: bool,
                    mascot_path: str) -> None:
        try:
            segments = self._parse_text(text)
            at = None if not at_text.strip() else video_bar.parse_time(at_text)
            out = Path(tempfile.gettempdir()) / "video_toolkit_preview.png"
            video_bar.render_preview_frame(
                segments, out, at=at, transparent=transparent,
                width=width, height=height, draw_labels=draw_labels,
                mascot_path=mascot_path or None,
            )
        except (OSError, ValueError) as exc:
            self.previewFailed.emit(str(exc))
            return
        self.previewReady.emit(QUrl.fromLocalFile(str(out)).toString())

    # ------------------------------------------------------------------
    # 视频渲染
    # ------------------------------------------------------------------
    @Slot(str, "QVariantMap")
    def startRender(self, text: str, opts: dict) -> None:
        if self._worker is not None and self._worker.isRunning():
            self.renderLog.emit("已有渲染任务进行中")
            return
        try:
            segments = self._parse_text(text)
        except (OSError, ValueError) as exc:
            self.renderLog.emit(f"时间段解析失败: {exc}")
            self.renderFinished.emit(False, str(exc), [])
            return

        width = int(opts.get("width", 1920))
        height = int(opts.get("height", 1080))
        fps = int(opts.get("fps", 30))

        out_dir = str(opts.get("outDir") or "").strip()
        if out_dir:
            out_path = Path(out_dir)
        elif self._current_file is not None:
            out_path = self._current_file.resolve().parent
        else:
            out_path = ROOT / "out"
        try:
            out_path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self.renderFinished.emit(False, f"输出目录不可用: {exc}", [])
            return

        name = "progress_bar_" + datetime.now().strftime("%Y%m%d_%H%M%S")
        mp4 = out_path / f"{name}.mp4" if opts.get("mp4") else None
        mov = out_path / f"{name}.mov" if opts.get("mov") else None
        webm = out_path / f"{name}.webm" if opts.get("webm") else None
        if not any((mp4, mov, webm)):
            self.renderLog.emit("请至少勾选一种输出格式")
            self.renderFinished.emit(False, "未选择输出格式", [])
            return

        worker = RenderWorker(
            segments,
            mp4_path=mp4, mov_path=mov, webm_path=webm,
            width=width, height=height, fps=fps,
            draw_labels=bool(opts.get("drawLabels", True)),
            mascot_path=str(opts.get("mascotPath") or "") or None,
        )
        worker.progress.connect(self.renderProgress)
        worker.log.connect(self.renderLog)
        worker.done.connect(self._on_render_done)
        self._worker = worker

        self.renderLog.emit(
            f"开始渲染: {len(segments)} 个章节, 时长 {segments[-1].end:.2f}s, "
            f"{width}x{height}@{fps}fps -> {out_path}")
        worker.start()

    @Slot()
    def cancelRender(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            self._worker.cancel()
            self.renderLog.emit("正在取消…")

    def _on_render_done(self, ok: bool, message: str, outputs: list) -> None:
        worker = self._worker
        if worker is not None and not worker.isRunning():
            worker.deleteLater()
        self.renderFinished.emit(ok, message, outputs)
