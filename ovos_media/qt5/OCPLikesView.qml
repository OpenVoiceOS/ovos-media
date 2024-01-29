/*
 *  Copyright 2018 by Aditya Mehra <aix.m@outlook.com>
 *  Copyright 2018 Marco Martin <mart@kde.org>
 *
 *  This program is free software: you can redistribute it and/or modify
 *  it under the terms of the GNU General Public License as published by
 *  the Free Software Foundation, either version 3 of the License, or
 *  (at your option) any later version.

 *  This program is distributed in the hope that it will be useful,
 *  but WITHOUT ANY WARRANTY; without even the implied warranty of
 *  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 *  GNU General Public License for more details.

 *  You should have received a copy of the GNU General Public License
 *  along with this program.  If not, see <http://www.gnu.org/licenses/>.
 */

import QtQuick 2.9
import QtQuick.Layouts 1.4
import QtGraphicalEffects 1.0
import QtQuick.Controls 2.3
import org.kde.kirigami 2.8 as Kirigami
import Mycroft 1.0 as Mycroft
import "delegates" as Delegates

Item {
    id: delegate
    property var likedCardsModel: sessionData.likedCards

    onFocusChanged: {
        if (focus) {
            likedListView.forceActiveFocus()
        }
    }

    onLikedCardsModelChanged: {
        likedListView.forceLayout()
    }

    ColumnLayout {
        id: colLay1
        anchors.fill: parent

        Item {            
            Layout.fillWidth: true            
            Layout.fillHeight: true

            Item {
                id: likesContainer
                anchors.fill: parent
                anchors.leftMargin: Kirigami.Units.gridUnit
                anchors.rightMargin: Kirigami.Units.gridUnit

                ListView {
                    id: likedListView
                    keyNavigationEnabled: true
                    spacing: Kirigami.Units.largeSpacing
                    currentIndex: 0
                    anchors.fill: parent
                    orientation: ListView.Horizontal
                    snapMode: ListView.SnapToItem
                    model: likedCardsModel
                    focus: false
                    interactive: true
                    delegate: Delegates.LikedCard {}
                    Layout.alignment: Qt.AlignVCenter
                    Layout.fillHeight: true
                    highlightRangeMode: ListView.StrictlyEnforceRange
                    KeyNavigation.up: answerButton
                    KeyNavigation.left: answerButton
                    KeyNavigation.down: homepageButtonTangle
                    KeyNavigation.right: homepageButtonTangle
                }
            }
        }
    }
}

