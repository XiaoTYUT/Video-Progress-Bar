import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ApplicationWindow {
    id: win
    width: 1180
    height: 760
    minimumWidth: 960
    minimumHeight: 620
    visible: true
    title: "视频生成工具包"

    RowLayout {
        anchors.fill: parent
        spacing: 0

        // 左侧工具导航: 后续新工具在此追加
        Rectangle {
            Layout.preferredWidth: 190
            Layout.fillHeight: true
            color: "#f5f5f4"

            ColumnLayout {
                anchors.fill: parent
                spacing: 0

                Label {
                    Layout.fillWidth: true
                    padding: 16
                    text: "工具箱"
                    font.pixelSize: 15
                    font.bold: true
                    color: "#404040"
                }

                ListView {
                    id: nav
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    clip: true
                    model: ["视频进度条"]
                    currentIndex: 0
                    delegate: ItemDelegate {
                        width: ListView.view.width
                        text: modelData
                        highlighted: ListView.isCurrentItem
                        onClicked: nav.currentIndex = index
                    }
                }
            }
        }

        Rectangle {
            Layout.preferredWidth: 1
            Layout.fillHeight: true
            color: "#e0e0e0"
        }

        StackLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            currentIndex: nav.currentIndex

            ProgressBarPage {}
        }
    }
}
