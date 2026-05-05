import QtQuick
import QtQuick.Layouts
import Quickshell
import qs.Commons
import qs.Widgets

Rectangle {
  id: root

  property var pluginApi: null
  property ShellScreen screen
  property string widgetId: ""
  property string section: ""
  property int sectionWidgetIndex: -1
  property int sectionWidgetsCount: 0

  readonly property var mainInstance: pluginApi?.mainInstance
  readonly property string lifecycleState: mainInstance?.lifecycleState || "idle"
  readonly property string statusLabel: mainInstance?.statusLabelForState(lifecycleState) || "Idle"
  readonly property color statusTone: mainInstance?.statusToneForState(lifecycleState) || Color.mPrimary
  readonly property string statusIcon: mainInstance?.statusIconForState(lifecycleState) || "keyboard"

  implicitWidth: 210
  implicitHeight: Style.barHeight
  clip: true
  color: Style.capsuleColor
  radius: Style.radiusM
  border.width: 1
  border.color: Color.mOutline

  Component.onCompleted: {
    if (mainInstance)
      mainInstance.refreshAll();
  }

  RowLayout {
    anchors.fill: parent
    anchors.margins: Style.marginXS
    spacing: Style.marginXS

    Rectangle {
      id: actionSegment
      Layout.fillWidth: true
      Layout.fillHeight: true
      radius: Style.radiusM - 2
      color: actionMouseArea.containsPress ? Color.mSurface : Color.mSurfaceVariant

      RowLayout {
        anchors.fill: parent
        anchors.margins: Style.marginS
        spacing: Style.marginS

        Rectangle {
          Layout.preferredWidth: 22
          Layout.preferredHeight: 22
          radius: 11
          color: Qt.rgba(statusTone.r, statusTone.g, statusTone.b, 0.14)

          NIcon {
            anchors.centerIn: parent
            icon: statusIcon
            color: statusTone
            pointSize: Style.fontSizeS
          }
        }

        NText {
          Layout.fillWidth: true
          text: statusLabel
          color: Color.mOnSurface
          pointSize: Style.fontSizeS
          font.weight: Font.DemiBold
          elide: Text.ElideRight
          horizontalAlignment: Text.AlignHCenter
          verticalAlignment: Text.AlignVCenter
        }

        Item {
          Layout.preferredWidth: 22
          Layout.preferredHeight: 22
        }
      }

      MouseArea {
        id: actionMouseArea
        anchors.fill: parent
        hoverEnabled: true
        cursorShape: Qt.PointingHandCursor
        acceptedButtons: Qt.LeftButton
        onClicked: {
          if (mainInstance)
            mainInstance.toggleRecording();
        }
      }
    }

    Rectangle {
      id: panelSegment
      Layout.preferredWidth: 28
      Layout.fillHeight: true
      radius: Style.radiusM - 2
      color: panelMouseArea.containsPress ? Color.mSurface : Color.mSurfaceVariant

      NIcon {
        anchors.centerIn: parent
        icon: "settings"
        color: Color.mOnSurfaceVariant
        pointSize: Style.fontSizeS
      }

      MouseArea {
        id: panelMouseArea
        anchors.fill: parent
        hoverEnabled: true
        cursorShape: Qt.PointingHandCursor
        acceptedButtons: Qt.LeftButton
        onClicked: pluginApi.togglePanel(root.screen, root)
      }
    }
  }
}
