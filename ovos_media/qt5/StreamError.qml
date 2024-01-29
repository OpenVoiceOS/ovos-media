import QtQuick.Layouts 1.4
import QtQuick 2.4
import QtQuick.Controls 2.0
import org.kde.kirigami 2.10 as Kirigami

import Mycroft 1.0 as Mycroft
import org.kde.lottie 1.0

Mycroft.Delegate {
    id: root
    skillBackgroundSource: sessionData.image ? sessionData.image : "https://source.unsplash.com/1920x1080/?+music"
    fillWidth: true

    Rectangle {
        anchors.fill: parent
        anchors.margins: Mycroft.Units.gridUnit * 2
        color: Kirigami.Theme.backgroundColor

        ColumnLayout {
            id: grid
            anchors.fill: parent
            anchors.margins: Kirigami.Units.largeSpacing

            Label {
                id: statusLabel
                Layout.alignment: Qt.AlignHCenter
                font.pixelSize: root.width * 0.035
                wrapMode: Text.WordWrap
                renderType: Text.NativeRendering
                font.family: "Noto Sans Display"
                font.styleName: "Black"
                text: qsTr(sessionData.error)
                color: Kirigami.Theme.textColor
            }

            LottieAnimation {
                id: statusIcon
                visible: true
                enabled: true
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.alignment: Qt.AlignHCenter
                loops: Animation.Infinite
                fillMode: Image.PreserveAspectFit
                running: true
                source: Qt.resolvedUrl(sessionData.animation)
            }
        }
    }
}