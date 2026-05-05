import QtQuick
import QtQuick.Layouts
import qs.Commons
import qs.Widgets

Item {
  id: root

  property var pluginApi: null
  readonly property var mainInstance: pluginApi?.mainInstance
  readonly property var geometryPlaceholder: panelContainer
  readonly property bool allowAttach: true
  property real contentPreferredWidth: 760 * Style.uiScaleRatio
  property real contentPreferredHeight: 660 * Style.uiScaleRatio

  anchors.fill: parent

  Rectangle {
    id: panelContainer
    anchors.fill: parent
    color: "transparent"

    Rectangle {
      anchors.fill: parent
      radius: Style.radiusL
      color: Color.mSurface
      border.width: 1
      border.color: Color.mOutline
    }

    ColumnLayout {
      anchors.fill: parent
      anchors.margins: Style.marginL
      spacing: Style.marginL

      RowLayout {
        Layout.fillWidth: true
        spacing: Style.marginM

        ColumnLayout {
          Layout.fillWidth: true
          spacing: Style.marginXS

          NText {
            text: "Voice Dictation"
            pointSize: Style.fontSizeL
            font.weight: Font.Bold
            color: Color.mOnSurface
          }

          NText {
            text: mainInstance?.statusCaptionForState(mainInstance?.lifecycleState || "idle") || "Helper service is unavailable."
            pointSize: Style.fontSizeS
            color: Color.mOnSurfaceVariant
            Layout.fillWidth: true
            wrapMode: Text.WordWrap
          }
        }

        Rectangle {
          Layout.preferredWidth: statusRow.implicitWidth + Style.marginM * 2
          Layout.preferredHeight: statusRow.implicitHeight + Style.marginS * 2
          radius: Style.radiusM
          color: Color.mSurfaceVariant

          RowLayout {
            id: statusRow
            anchors.centerIn: parent
            spacing: Style.marginS

            NIcon {
              icon: mainInstance?.statusIconForState(mainInstance?.lifecycleState || "idle") || "waveform"
              color: mainInstance?.statusToneForState(mainInstance?.lifecycleState || "idle") || Color.mPrimary
              pointSize: Style.fontSizeM
            }

            NText {
              text: mainInstance?.statusLabelForState(mainInstance?.lifecycleState || "idle") || "Idle"
              color: Color.mOnSurface
              pointSize: Style.fontSizeS
              font.weight: Font.DemiBold
            }
          }
        }
      }

      Rectangle {
        Layout.fillWidth: true
        radius: Style.radiusL
        color: Color.mSurfaceVariant
        border.width: 1
        border.color: Color.mOutline

        ColumnLayout {
          anchors.fill: parent
          anchors.margins: Style.marginL
          spacing: Style.marginM

          RowLayout {
            Layout.fillWidth: true
            spacing: Style.marginM

            ColumnLayout {
              Layout.fillWidth: true
              spacing: Style.marginXS

              NText {
                text: "Current session"
                pointSize: Style.fontSizeM
                font.weight: Font.DemiBold
                color: Color.mOnSurface
              }

              NText {
                text: mainInstance?.formatSessionLabel() || "No session yet"
                pointSize: Style.fontSizeS
                color: Color.mOnSurfaceVariant
                Layout.fillWidth: true
                wrapMode: Text.WrapAnywhere
              }
            }

            NButton {
              text: mainInstance?.statusGroup === "recording" ? "Stop" : "Record"
              enabled: Boolean(mainInstance) && !mainInstance.mutationLoading
              onClicked: {
                if (mainInstance)
                  mainInstance.toggleRecording();
              }
            }
          }

          RowLayout {
            Layout.fillWidth: true
            spacing: Style.marginM

            ColumnLayout {
              Layout.fillWidth: true
              spacing: Style.marginXS

              NText {
                text: "Prompt preset"
                pointSize: Style.fontSizeM
                font.weight: Font.DemiBold
                color: Color.mOnSurface
              }

              NComboBox {
                Layout.fillWidth: true
                model: mainInstance?.promptPresetOptions || []
                currentKey: mainInstance?.selectedPromptPresetId || ""
                onSelected: function (key) {
                  if (mainInstance)
                    mainInstance.setSelectedPromptPreset(key);
                }
              }

              NText {
                text: mainInstance?.promptTextForId(mainInstance?.selectedPromptPresetId || "") || ""
                pointSize: Style.fontSizeXS
                color: Color.mOnSurfaceVariant
                Layout.fillWidth: true
                wrapMode: Text.WordWrap
              }
            }
          }
        }
      }

      Rectangle {
        Layout.fillWidth: true
        Layout.fillHeight: true
        radius: Style.radiusL
        color: Color.mSurfaceVariant
        border.width: 1
        border.color: Color.mOutline

        ColumnLayout {
          anchors.fill: parent
          anchors.margins: Style.marginL
          spacing: Style.marginM

          RowLayout {
            Layout.fillWidth: true
            spacing: Style.marginM

            ColumnLayout {
              Layout.fillWidth: true
              spacing: Style.marginXS

              NText {
                text: "Recent history"
                pointSize: Style.fontSizeM
                font.weight: Font.DemiBold
                color: Color.mOnSurface
              }

              NText {
                text: "Each entry keeps copy and export explicit, so nothing is typed or exported automatically."
                pointSize: Style.fontSizeXS
                color: Color.mOnSurfaceVariant
                Layout.fillWidth: true
                wrapMode: Text.WordWrap
              }
            }

            NButton {
              text: "Refresh"
              onClicked: {
                if (mainInstance)
                  mainInstance.refreshAll();
              }
            }
          }

          NScrollView {
            Layout.fillWidth: true
            Layout.fillHeight: true

            ListView {
              clip: true
              spacing: Style.marginS
              model: mainInstance?.historyItems || []

              delegate: Rectangle {
                required property var modelData

                width: ListView.view.width
                implicitHeight: cardLayout.implicitHeight + Style.marginM * 2
                radius: Style.radiusM
                color: Color.mSurface
                border.width: 1
                border.color: Color.mOutline

                ColumnLayout {
                  id: cardLayout
                  anchors.fill: parent
                  anchors.margins: Style.marginM
                  spacing: Style.marginS

                  RowLayout {
                    Layout.fillWidth: true
                    spacing: Style.marginM

                    ColumnLayout {
                      Layout.fillWidth: true
                      spacing: Style.marginXS

                      NText {
                        text: modelData.sessionId || "Unknown session"
                        pointSize: Style.fontSizeS
                        font.weight: Font.DemiBold
                        color: Color.mOnSurface
                        Layout.fillWidth: true
                        elide: Text.ElideRight
                      }

                      NText {
                        text: (modelData.status || "idle") + " • " + (modelData.completedAt || modelData.createdAt || "")
                        pointSize: Style.fontSizeXS
                        color: Color.mOnSurfaceVariant
                        Layout.fillWidth: true
                        elide: Text.ElideRight
                      }
                    }

                    Rectangle {
                      Layout.preferredWidth: badgeText.implicitWidth + Style.marginM * 2
                      Layout.preferredHeight: badgeText.implicitHeight + Style.marginXS * 2
                      radius: Style.radiusM
                      color: Color.mSurfaceVariant

                      NText {
                        id: badgeText
                        anchors.centerIn: parent
                        text: modelData.promptPresetId || "preset"
                        pointSize: Style.fontSizeXS
                        color: Color.mOnSurfaceVariant
                      }
                    }
                  }

                  NText {
                    text: mainInstance?.transcriptPreview(mainInstance?.transcriptTextForItem(modelData) || "") || "Transcript pending"
                    pointSize: Style.fontSizeS
                    color: Color.mOnSurface
                    Layout.fillWidth: true
                    wrapMode: Text.WordWrap
                  }

                  RowLayout {
                    Layout.fillWidth: true
                    spacing: Style.marginM

                    NButton {
                      text: "Copy"
                      enabled: Boolean(mainInstance?.transcriptTextForItem(modelData))
                      onClicked: {
                        if (mainInstance)
                          mainInstance.copyHistoryItem(modelData);
                      }
                    }

                    NButton {
                      text: "Export"
                      enabled: Boolean(modelData.jobId)
                      onClicked: {
                        if (mainInstance)
                          mainInstance.exportHistoryItem(modelData.jobId);
                      }
                    }

                    Item {
                      Layout.fillWidth: true
                    }

                    NText {
                      text: modelData.cleanupModel || modelData.sttModel || ""
                      pointSize: Style.fontSizeXS
                      color: Color.mOnSurfaceVariant
                    }
                  }
                }
              }
            }
          }

          NText {
            visible: (mainInstance?.historyItems || []).length === 0
            text: mainInstance?.historyLoading ? "Loading history…" : "No transcript history yet. Record once to populate this list."
            pointSize: Style.fontSizeS
            color: Color.mOnSurfaceVariant
            Layout.fillWidth: true
            horizontalAlignment: Text.AlignHCenter
          }
        }
      }
    }
  }
}
