import QtQuick 2.12
import QtQuick.Window 2.12
import QtQuick.Controls 2.12
import org.kde.kirigami 2.11 as Kirigami
import Mycroft 1.0 as Mycroft
import QtQuick.Layouts 1.12
import QtGraphicalEffects 1.0
import QtMultimedia 5.12
import "code/helper.js" as HelperJS

Button {
    id: playerControlButton
    Layout.preferredWidth: horizontalMode ? Math.round(parent.width / 6) - Mycroft.Units.gridUnit : Math.round(parent.width / 6)
    Layout.fillHeight: true
    Layout.maximumHeight: width + Mycroft.Units.gridUnit
    property var horizontalMode
    property var controlIcon
    property color controlIconColor
    property color controlBackgroundColor: HelperJS.isLight(Kirigami.Theme.backgroundColor) ? Qt.lighter(Kirigami.Theme.backgroundColor, 1.5) : Qt.darker(Kirigami.Theme.backgroundColor, 1.5)


    contentItem: Kirigami.Icon {
        anchors.fill: parent
        anchors.margins: horizontalMode ? Mycroft.Units.gridUnit : Mycroft.Units.gridUnit * 0.5
        source: playerControlButton.controlIcon

        ColorOverlay {
            source: parent
            anchors.fill: parent
            color: playerControlButton.controlIconColor
        }
    }

    background: Rectangle {
        id: playerControlButtonBackground
        radius: 5
        color: playerControlButton.controlBackgroundColor
        border.color: playerControlButton.activeFocus ? Kirigami.Theme.highlightColor : "transparent"
        border.width: playerControlButton.activeFocus ? 2 : 0
        layer.enabled: true
        layer.effect: DropShadow {
            horizontalOffset: 1
            verticalOffset: 2
        }
    }
}
