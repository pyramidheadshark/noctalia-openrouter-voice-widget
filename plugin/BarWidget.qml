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
  readonly property string statusCaption: mainInstance?.statusCaptionForState(lifecycleState) || ""
  readonly property color statusTone: mainInstance?.statusToneForState(lifecycleState) || Color.mPrimary
  readonly property string statusIcon: mainInstance?.statusIconForState(lifecycleState) || "keyboard"

  implicitWidth: widgetRow.implicitWidth + Style.marginS * 2
  implicitHeight: Style.barHeight
  color: Style.capsuleColor
  radius: Style.radiusM
  border.width: 1
  border.color: Color.mOutline

  Component.onCompleted: {
    if (mainInstance)
      mainInstance.refreshAll();
  }

  RowLayout {
    id: widgetRow
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
          Layout.preferredWidth: 28
          Layout.preferredHeight: 28
          radius: 14
          color: Qt.rgba(statusTone.r, statusTone.g, statusTone.b, 0.14)

          NIcon {
            anchors.centerIn: parent
            icon: statusIcon
            color: statusTone
            pointSize: Style.fontSizeM
          }
        }

        ColumnLayout {
          Layout.fillWidth: true
          spacing: 0

          NText {
            text: statusLabel
            color: Color.mOnSurface
            pointSize: Style.fontSizeS
            font.weight: Font.DemiBold
          }

          NText {
            text: lifecycleState === "recording" ? "Tap to stop capture" : (lifecycleState === "idle" || lifecycleState === "ready" || lifecycleState === "cancelled" ? "Tap to start dictation" : statusCaption)
            color: Color.mOnSurfaceVariant
            pointSize: Style.fontSizeXS
            elide: Text.ElideRight
            Layout.fillWidth: true
          }
        }
      }

      MouseArea {
        id: actionMouseArea
        anchors.fill: parent
        hoverEnabled: true
        cursorShape: Qt.PointingHandCursor
        acceptedButtons: Qt.LeftButton | Qt.RightButton
        onClicked: function (mouse) {
          if (mouse.button === Qt.RightButton) {
            pluginApi.togglePanel(root.screen, root);
            return;
          }
          if (mainInstance)
            mainInstance.toggleRecording();
        }
      }
    }
  }
}
