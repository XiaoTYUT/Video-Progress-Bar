# -*- coding: utf-8 -*-
"""视频生成工具包 - 桌面应用入口 (PySide6 + QML)。

运行:
    python app/main.py
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
APP_DIR = Path(__file__).resolve().parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from backend import Bridge  # noqa: E402


def main() -> int:
    QQuickStyle.setStyle("FluentWinUI3")
    app = QGuiApplication(sys.argv)
    app.setApplicationName("视频生成工具包")

    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("bridge", Bridge())
    engine.load(str(APP_DIR / "qml" / "Main.qml"))
    if not engine.rootObjects():
        print("QML 加载失败", file=sys.stderr)
        return 1
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
