import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs
import QtQuick.Layouts

// 视频进度条工具页: 时间段编辑 / 输出参数 / 单帧预览 / 视频渲染 / 日志
Page {
    id: page

    property string currentFile: ""
    property int segCount: 0
    property double totalDuration: 0
    property bool parseOk: false
    property bool rendering: false
    property string lastOutDir: ""

    function log(line) {
        logArea.append("[" + new Date().toLocaleTimeString() + "] " + line)
    }

    function fmt(sec) {
        sec = Math.round(sec)
        var m = Math.floor(sec / 60), s = sec % 60
        return (m < 10 ? "0" + m : m) + ":" + (s < 10 ? "0" + s : s)
    }

    FileDialog {
        id: openDlg
        title: "打开时间段文件"
        nameFilters: ["时间段文件 (*.txt)", "所有文件 (*)"]
        onAccepted: bridge.loadFile(selectedFile.toString())
    }
    FileDialog {
        id: saveDlg
        title: "保存时间段文件"
        fileMode: FileDialog.SaveFile
        nameFilters: ["时间段文件 (*.txt)"]
        onAccepted: bridge.saveFile(selectedFile.toString(), editor.text)
    }
    FolderDialog {
        id: outDirDlg
        title: "选择输出目录"
        onAccepted: outDirField.text = bridge.urlToPath(selectedFolder.toString())
    }
    FileDialog {
        id: mascotDlg
        title: "选择播放头 GIF"
        nameFilters: ["GIF 动画 (*.gif)"]
        onAccepted: mascotField.text = bridge.urlToPath(selectedFile.toString())
    }

    // 编辑停顿 400ms 后自动重新解析
    Timer {
        id: parseTimer
        interval: 400
        onTriggered: bridge.parseSegments(editor.text)
    }

    Connections {
        target: bridge

        function onFileLoaded(path, text) {
            page.currentFile = path
            editor.text = text
            page.log("已加载: " + path)
        }
        function onFileSaved(path) {
            page.currentFile = path
            page.log("已保存: " + path)
        }
        function onSegmentsParsed(list, duration) {
            page.segCount = list.length
            page.totalDuration = duration
            page.parseOk = list.length > 0
            statusLabel.text = page.parseOk
                ? list.length + " 个章节 · 总时长 " + page.fmt(duration)
                : ""
            statusLabel.color = "#2e7d32"
        }
        function onParseFailed(msg) {
            page.parseOk = false
            page.segCount = 0
            statusLabel.text = msg
            statusLabel.color = "#c62828"
        }
        function onPreviewReady(url) {
            previewImage.source = ""
            previewImage.source = url
        }
        function onPreviewFailed(msg) {
            page.log("预览失败: " + msg)
        }
        function onRenderProgress(pct) {
            renderBar.value = pct / 100.0
            pctLabel.text = pct + "%"
        }
        function onRenderLog(line) { page.log(line) }
        function onRenderFinished(ok, message, outputs) {
            page.rendering = false
            page.log(message)
            if (ok && outputs.length > 0) {
                page.lastOutDir = bridge.dirOf(outputs[0])
                for (var i = 0; i < outputs.length; i++)
                    page.log("完成: " + outputs[i])
            }
        }
    }

    Component.onCompleted: {
        mascotField.text = bridge.defaultMascot()
        bridge.loadExample()
    }

    RowLayout {
        anchors.fill: parent
        anchors.margins: 12
        spacing: 12

        // ---------------- 左列: 时间段编辑 ----------------
        ColumnLayout {
            Layout.preferredWidth: 400
            Layout.fillHeight: true
            spacing: 8

            RowLayout {
                Layout.fillWidth: true
                Button { text: "打开…"; onClicked: openDlg.open() }
                Button {
                    text: "保存"
                    enabled: editor.text.length > 0
                    onClicked: {
                        if (page.currentFile)
                            bridge.saveFile(page.currentFile, editor.text)
                        else
                            saveDlg.open()
                    }
                }
                Button { text: "另存为…"; onClicked: saveDlg.open() }
                Item { Layout.fillWidth: true }
            }

            Label {
                Layout.fillWidth: true
                elide: Label.ElideMiddle
                text: page.currentFile ? page.currentFile : "未关联文件（可直接编辑后另存为）"
                color: "#666666"
                font.pixelSize: 12
            }

            Frame {
                Layout.fillWidth: true
                Layout.fillHeight: true
                padding: 0

                ScrollView {
                    anchors.fill: parent
                    TextArea {
                        id: editor
                        font.family: "Consolas"
                        font.pixelSize: 13
                        wrapMode: TextEdit.NoWrap
                        placeholderText: "# 每行一个章节: 结束时间, 标题\n00:06, 片头\n00:14, 第一部分"
                        onTextChanged: parseTimer.restart()
                    }
                }
            }

            Label {
                id: statusLabel
                Layout.fillWidth: true
                wrapMode: Label.Wrap
                font.pixelSize: 12
            }
        }

        // ---------------- 右列: 参数 / 预览 / 渲染 / 日志 ----------------
        ColumnLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 8

            GroupBox {
                title: "输出参数"
                Layout.fillWidth: true

                GridLayout {
                    anchors.fill: parent
                    columns: 6
                    rowSpacing: 6
                    columnSpacing: 8

                    Label { text: "预设" }
                    ComboBox {
                        id: presetCombo
                        Layout.columnSpan: 5
                        Layout.fillWidth: true
                        model: ["自定义", "1080p 全幅 (1920×1080 @30)", "窄幅条带 (1920×270 @10)"]
                        currentIndex: 1
                        onActivated: function(index) {
                            if (index === 1) {
                                wSpin.value = 1920; hSpin.value = 1080; fpsSpin.value = 30
                            } else if (index === 2) {
                                wSpin.value = 1920; hSpin.value = 270; fpsSpin.value = 10
                            }
                        }
                    }

                    Label { text: "宽" }
                    SpinBox {
                        id: wSpin
                        from: 16; to: 7680; stepSize: 10; value: 1920
                        editable: true
                        Layout.fillWidth: true
                        onValueModified: presetCombo.currentIndex = 0
                    }
                    Label { text: "高" }
                    SpinBox {
                        id: hSpin
                        from: 16; to: 4320; stepSize: 10; value: 1080
                        editable: true
                        Layout.fillWidth: true
                        onValueModified: presetCombo.currentIndex = 0
                    }
                    Label { text: "帧率" }
                    SpinBox {
                        id: fpsSpin
                        from: 1; to: 120; value: 30
                        editable: true
                        Layout.fillWidth: true
                        onValueModified: presetCombo.currentIndex = 0
                    }

                    CheckBox { id: mp4Chk; text: "MP4 白底"; checked: true }
                    CheckBox { id: movChk; text: "MOV 透明"; checked: true }
                    CheckBox { id: webmChk; text: "WebM 透明" }
                    CheckBox {
                        id: labelsChk
                        text: "章节标题"
                        checked: true
                        Layout.columnSpan: 3
                    }

                    Label { text: "输出目录" }
                    TextField {
                        id: outDirField
                        Layout.columnSpan: 4
                        Layout.fillWidth: true
                        placeholderText: "留空 = 时间段文件所在目录"
                    }
                    Button { text: "浏览…"; onClicked: outDirDlg.open() }

                    CheckBox { id: mascotChk; text: "播放头 GIF"; checked: true }
                    TextField {
                        id: mascotField
                        Layout.columnSpan: 4
                        Layout.fillWidth: true
                        enabled: mascotChk.checked
                        placeholderText: "小驴 GIF 路径"
                    }
                    Button {
                        text: "浏览…"
                        enabled: mascotChk.checked
                        onClicked: mascotDlg.open()
                    }
                }
            }

            GroupBox {
                title: "单帧预览"
                Layout.fillWidth: true

                ColumnLayout {
                    anchors.fill: parent
                    spacing: 6

                    RowLayout {
                        Layout.fillWidth: true
                        Label { text: "时间点" }
                        TextField {
                            id: atField
                            Layout.preferredWidth: 110
                            placeholderText: "留空 = 中间"
                        }
                        RadioButton { id: whiteBg; text: "白底"; checked: true }
                        RadioButton { text: "透明" }
                        Button {
                            text: "生成预览"
                            enabled: page.parseOk && !page.rendering
                            onClicked: bridge.makePreview(
                                editor.text, atField.text, !whiteBg.checked,
                                wSpin.value, hSpin.value, labelsChk.checked,
                                mascotChk.checked ? mascotField.text : "")
                        }
                        Item { Layout.fillWidth: true }
                    }

                    Rectangle {
                        Layout.fillWidth: true
                        Layout.preferredHeight: 190
                        color: whiteBg.checked ? "#ffffff" : "#9e9e9e"
                        border.color: "#d0d0d0"
                        clip: true

                        Image {
                            id: previewImage
                            anchors.fill: parent
                            fillMode: Image.PreserveAspectFit
                            cache: false
                        }
                        Label {
                            anchors.centerIn: parent
                            visible: previewImage.status !== Image.Ready
                            text: "生成预览后在此显示"
                            color: "#888888"
                        }
                    }
                }
            }

            GroupBox {
                title: "渲染视频"
                Layout.fillWidth: true

                RowLayout {
                    anchors.fill: parent
                    spacing: 8

                    Button {
                        text: "开始渲染"
                        highlighted: true
                        enabled: page.parseOk && !page.rendering
                        onClicked: {
                            page.rendering = true
                            renderBar.value = 0
                            pctLabel.text = "0%"
                            bridge.startRender(editor.text, ({
                                width: wSpin.value,
                                height: hSpin.value,
                                fps: fpsSpin.value,
                                mp4: mp4Chk.checked,
                                mov: movChk.checked,
                                webm: webmChk.checked,
                                outDir: outDirField.text,
                                drawLabels: labelsChk.checked,
                                mascotPath: mascotChk.checked ? mascotField.text : ""
                            }))
                        }
                    }
                    Button {
                        text: "取消"
                        enabled: page.rendering
                        onClicked: bridge.cancelRender()
                    }
                    ProgressBar {
                        id: renderBar
                        Layout.fillWidth: true
                        from: 0
                        to: 1
                    }
                    Label { id: pctLabel; text: "" }
                    Button {
                        text: "打开输出目录"
                        enabled: page.lastOutDir !== ""
                        onClicked: bridge.openFolder(page.lastOutDir)
                    }
                }
            }

            GroupBox {
                title: "日志"
                Layout.fillWidth: true
                Layout.fillHeight: true

                ScrollView {
                    anchors.fill: parent
                    TextArea {
                        id: logArea
                        readOnly: true
                        font.family: "Consolas"
                        font.pixelSize: 12
                        wrapMode: TextEdit.Wrap
                    }
                }
            }
        }
    }
}
