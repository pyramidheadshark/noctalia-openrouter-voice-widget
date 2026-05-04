import QtQuick
import QtQuick.Layouts
import qs.Commons
import qs.Widgets

ColumnLayout {
  id: root

  property var pluginApi: null
  readonly property var mainInstance: pluginApi?.mainInstance

  property string editSecretValue: ""
  property string editSttModel: mainInstance?.configSummary?.sttModel || "openai/whisper-large-v3"
  property string editCleanupModel: mainInstance?.configSummary?.cleanupModel || "google/gemini-3-flash-preview"
  property string editExportDirectory: mainInstance?.configSummary?.exportDirectory || "~/Documents/VoiceTranscripts"
  property string editDefaultPromptPresetId: mainInstance?.configSummary?.defaultPromptPresetId || "ml-dictation-default"
  property int editCompletedRetentionDays: Number(mainInstance?.configSummary?.completedJobRetentionDays || 30)
  property int editFailedRetentionDays: Number(mainInstance?.configSummary?.failedJobRetentionDays || 7)
  property int editFailedAudioTtlHours: Number(mainInstance?.configSummary?.failedJobAudioTtlHours || 24)

  spacing: Style.marginM

  Component.onCompleted: {
    if (mainInstance)
      mainInstance.refreshAll();
  }

  NText {
    text: "OpenRouter"
    pointSize: Style.fontSizeM
    font.weight: Font.Bold
    color: Color.mOnSurface
  }

  Rectangle {
    Layout.fillWidth: true
    radius: Style.radiusM
    color: Color.mSurfaceVariant
    border.width: 1
    border.color: Color.mOutline

    ColumnLayout {
      anchors.fill: parent
      anchors.margins: Style.marginM
      spacing: Style.marginS

      NText {
        text: "Key status: " + (mainInstance?.secretStatusLabel || "Missing")
        pointSize: Style.fontSizeS
        font.weight: Font.DemiBold
        color: Color.mOnSurface
      }

      NText {
        text: mainInstance?.secretState === "invalid" ? "The stored key is present, but the latest auth failure suggests it should be replaced." : "The plaintext key is never written into Noctalia plugin settings. Saving here updates only the local secret file."
        pointSize: Style.fontSizeXS
        color: Color.mOnSurfaceVariant
        Layout.fillWidth: true
        wrapMode: Text.WordWrap
      }

      NTextInput {
        Layout.fillWidth: true
        label: "Replace OpenRouter key"
        description: "Leave blank to keep the existing key unchanged."
        placeholderText: "sk-or-v1-…"
        text: root.editSecretValue
        inputMethodHints: Qt.ImhHiddenText
        onTextChanged: root.editSecretValue = text
      }
    }
  }

  NDivider {
    Layout.fillWidth: true
  }

  NText {
    text: "Models and prompts"
    pointSize: Style.fontSizeM
    font.weight: Font.Bold
    color: Color.mOnSurface
  }

  NTextInput {
    Layout.fillWidth: true
    label: "STT model"
    description: "The transcript-source model used during the transcription stage."
    placeholderText: "openai/whisper-large-v3"
    text: root.editSttModel
    onTextChanged: root.editSttModel = text
  }

  NTextInput {
    Layout.fillWidth: true
    label: "Cleanup model"
    description: "The post-processing model used for transcript cleanup and polish."
    placeholderText: "google/gemini-3-flash-preview"
    text: root.editCleanupModel
    onTextChanged: root.editCleanupModel = text
  }

  ColumnLayout {
    Layout.fillWidth: true
    spacing: Style.marginS

    NLabel {
      label: "Default prompt preset"
      description: "New recordings inherit this preset unless the panel switcher overrides it."
    }

    NComboBox {
      Layout.fillWidth: true
      model: mainInstance?.promptPresetOptions || []
      currentKey: root.editDefaultPromptPresetId
      onSelected: function (key) {
        root.editDefaultPromptPresetId = key;
      }
    }

    Repeater {
      model: mainInstance?.promptPresets || []

      Rectangle {
        required property var modelData

        Layout.fillWidth: true
        radius: Style.radiusM
        color: Color.mSurfaceVariant
        border.width: 1
        border.color: Color.mOutline

        ColumnLayout {
          anchors.fill: parent
          anchors.margins: Style.marginS
          spacing: Style.marginXS

          NText {
            text: modelData.label
            pointSize: Style.fontSizeS
            font.weight: Font.DemiBold
            color: Color.mOnSurface
          }

          NText {
            text: modelData.promptText || ""
            pointSize: Style.fontSizeXS
            color: Color.mOnSurfaceVariant
            Layout.fillWidth: true
            wrapMode: Text.WordWrap
          }
        }
      }
    }
  }

  NDivider {
    Layout.fillWidth: true
  }

  NText {
    text: "Storage and retention"
    pointSize: Style.fontSizeM
    font.weight: Font.Bold
    color: Color.mOnSurface
  }

  NTextInput {
    Layout.fillWidth: true
    label: "Export directory"
    description: "Markdown exports are written here."
    placeholderText: "~/Documents/VoiceTranscripts"
    text: root.editExportDirectory
    onTextChanged: root.editExportDirectory = text
  }

  ColumnLayout {
    Layout.fillWidth: true
    spacing: Style.marginS

    NLabel {
      label: "Completed transcript retention"
      description: root.editCompletedRetentionDays + " days"
    }

    NSpinBox {
      from: 1
      to: 365
      value: root.editCompletedRetentionDays
      onValueChanged: root.editCompletedRetentionDays = value
    }
  }

  ColumnLayout {
    Layout.fillWidth: true
    spacing: Style.marginS

    NLabel {
      label: "Failed transcript retention"
      description: root.editFailedRetentionDays + " days"
    }

    NSpinBox {
      from: 1
      to: 90
      value: root.editFailedRetentionDays
      onValueChanged: root.editFailedRetentionDays = value
    }
  }

  ColumnLayout {
    Layout.fillWidth: true
    spacing: Style.marginS

    NLabel {
      label: "Failed audio TTL"
      description: root.editFailedAudioTtlHours + " hours"
    }

    NSpinBox {
      from: 1
      to: 168
      value: root.editFailedAudioTtlHours
      onValueChanged: root.editFailedAudioTtlHours = value
    }
  }

  function saveSettings() {
    if (!mainInstance) {
      Logger.e("VoiceDictation", "Cannot save settings without the main instance.");
      return;
    }

    mainInstance.saveServiceSettings({
      "defaultPromptPresetId": root.editDefaultPromptPresetId,
      "sttModel": root.editSttModel.trim(),
      "cleanupModel": root.editCleanupModel.trim(),
      "exportDirectory": root.editExportDirectory.trim(),
      "completedJobRetentionDays": root.editCompletedRetentionDays,
      "failedJobRetentionDays": root.editFailedRetentionDays,
      "failedJobAudioTtlHours": root.editFailedAudioTtlHours
    });

    if ((root.editSecretValue || "").trim()) {
      mainInstance.saveSecretValue(root.editSecretValue);
      root.editSecretValue = "";
    }
  }
}
