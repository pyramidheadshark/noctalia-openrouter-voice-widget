import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Services.UI

Item {
  id: root

  property var pluginApi: null
  property var snapshotData: ({})
  property var configSummary: ({})
  property var historyItems: []
  property var promptPresets: []
  property bool serviceAvailable: false
  property bool snapshotLoading: false
  property bool historyLoading: false
  property bool promptLoading: false
  property bool mutationLoading: false
  property bool pendingStopRequest: false
  property string lastErrorCode: ""
  property string lastErrorMessage: ""

  readonly property string pluginDirPath: pluginApi?.pluginDir || ""
  readonly property string lifecycleState: snapshotData?.state?.lifecycleState || "idle"
  readonly property string activeJobId: snapshotData?.state?.activeJobId || ""
  readonly property string activeSessionId: snapshotData?.state?.activeSessionId || ""
  readonly property string lastJobId: snapshotData?.state?.lastJobId || ""
  readonly property string statusGroup: statusGroupForState(lifecycleState)
  readonly property string defaultPromptPresetId: configSummary?.defaultPromptPresetId || "ml-dictation-default"
  readonly property string selectedPromptPresetId: pluginApi?.pluginSettings?.selectedPromptPresetId || defaultPromptPresetId
  readonly property int historyLimit: pluginApi?.pluginSettings?.historyLimit || pluginApi?.manifest?.metadata?.defaultSettings?.historyLimit || 20
  readonly property bool secretConfigured: Boolean(snapshotData?.health?.secretConfigured ?? configSummary?.secretConfigured)
  readonly property string latestHistoryErrorCode: historyItems.length > 0 ? (historyItems[0].errorCode || "") : ""
  readonly property string secretState: secretStateLabel()
  readonly property string secretStatusLabel: {
    if (secretState === "invalid")
      return "Invalid";
    if (secretState === "configured")
      return "Configured";
    return "Missing";
  }
  readonly property string currentPromptLabel: promptLabelForId(selectedPromptPresetId)
  readonly property var promptPresetOptions: promptPresets.map(function (preset) {
      return {
        "key": preset.presetId,
        "name": preset.label
      };
    })

  Component.onCompleted: refreshAll()

  Timer {
    id: refreshTimer
    interval: 3500
    repeat: true
    running: true
    onTriggered: root.refreshAll()
  }

  function statusGroupForState(state) {
    if (state === "recording")
      return "recording";
    if (state === "finalizing" || state === "transcribing" || state === "postprocessing")
      return "processing";
    if (state === "failed")
      return "error";
    return "idle";
  }

  function statusLabelForState(state) {
    if (state === "recording")
      return "Recording";
    if (state === "finalizing")
      return "Finalizing";
    if (state === "transcribing")
      return "Transcribing";
    if (state === "postprocessing")
      return "Cleaning";
    if (state === "failed")
      return "Error";
    if (state === "ready")
      return "Ready";
    if (state === "cancelled")
      return "Cancelled";
    return "Idle";
  }

  function statusCaptionForState(state) {
    if (state === "recording")
      return activeSessionId ? "Session " + activeSessionId : "Listening for speech";
    if (state === "finalizing" || state === "transcribing" || state === "postprocessing")
      return activeJobId ? "Job " + activeJobId : "Service is processing the latest capture";
    if (state === "failed")
      return lastErrorMessage || "The last job failed. Open the panel for details.";
    if (state === "ready")
      return lastJobId ? "Latest job is ready in history" : "Latest transcript is ready";
    if (state === "cancelled")
      return "Last recording was cancelled";
    return secretConfigured ? "Ready for a new capture" : "Add your OpenRouter key in settings";
  }

  function statusIconForState(state) {
    if (state === "recording")
      return "disc";
    if (state === "finalizing" || state === "transcribing" || state === "postprocessing")
      return "refresh";
    if (state === "failed")
      return "alert-circle";
    if (state === "ready")
      return "circle-check";
    if (state === "cancelled")
      return "x";
    return "keyboard";
  }

  function statusToneForState(state) {
    var group = statusGroupForState(state);
    if (group === "recording")
      return Color.mPrimary;
    if (group === "processing")
      return Color.mPrimary;
    if (group === "error")
      return Color.mError;
    return Color.mOnSurfaceVariant;
  }

  function secretStateLabel() {
    if (!secretConfigured)
      return "missing";
    if (lastErrorCode === "stt_auth_failed" || lastErrorCode === "cleanup_auth_failed" || latestHistoryErrorCode === "stt_auth_failed" || latestHistoryErrorCode === "cleanup_auth_failed")
      return "invalid";
    return "configured";
  }

  function promptLabelForId(presetId) {
    for (var i = 0; i < promptPresets.length; ++i) {
      if (promptPresets[i].presetId === presetId)
        return promptPresets[i].label;
    }
    return presetId || "Preset unavailable";
  }

  function promptTextForId(presetId) {
    for (var i = 0; i < promptPresets.length; ++i) {
      if (promptPresets[i].presetId === presetId)
        return promptPresets[i].promptText || "";
    }
    return "";
  }

  function transcriptTextForItem(item) {
    if (!item)
      return "";
    return item.processedTranscript || item.rawTranscript || "";
  }

  function transcriptPreview(text) {
    var value = (text || "").trim();
    if (value.length <= 180)
      return value;
    return value.slice(0, 177) + "...";
  }

  function formatSessionLabel() {
    if (activeSessionId)
      return activeSessionId;
    if (historyItems.length > 0)
      return historyItems[0].sessionId || "No recent session";
    return "No session yet";
  }

  function buildArgs(command, params) {
    return [
      "python3",
      "-c",
      "import os, subprocess, sys; plugin_dir = os.path.realpath(sys.argv[1]); script = os.path.join(plugin_dir, 'service', 'ipc_client.py'); fallback = os.path.join(os.path.dirname(plugin_dir), 'service', 'ipc_client.py'); target = script if os.path.exists(script) else fallback; raise SystemExit(subprocess.call(['python3', target, sys.argv[2], sys.argv[3]]))",
      pluginDirPath,
      command,
      JSON.stringify(params || {})
    ];
  }

  function beginProcess(process, command, params) {
    if (!pluginDirPath) {
      setTransportFailure("plugin_not_ready", "Plugin directory is not ready yet.");
      return;
    }
    process.outputText = "";
    process.errorText = "";
    process.commandName = command;
    process.command = buildArgs(command, params);
    process.running = true;
  }

  function setTransportFailure(code, message) {
    serviceAvailable = false;
    lastErrorCode = code;
    lastErrorMessage = message;
    ToastService.showError(message);
  }

  function setServiceError(error, fallbackCommand) {
    serviceAvailable = true;
    lastErrorCode = error?.code || "service_error";
    lastErrorMessage = error?.message || ("Service request failed for " + fallbackCommand);
  }

  function finishJsonProcess(process, successHandler) {
    var rawText = (process.outputText || "").trim();
    if (!rawText) {
      var failureMessage = (process.errorText || "Failed to contact the helper service.").trim();
      setTransportFailure("transport_error", failureMessage);
      return;
    }

    try {
      var payload = JSON.parse(rawText);
      if (!payload.ok) {
        setServiceError(payload.error || {}, payload.command || process.commandName);
        refreshAll();
        return;
      }
      serviceAvailable = true;
      lastErrorCode = "";
      lastErrorMessage = "";
      successHandler(payload.result || {});
    } catch (error) {
      setTransportFailure("invalid_response", "Received an invalid JSON response from the helper service.");
    }
  }

  function refreshAll() {
    loadSnapshot();
    loadHistory();
    loadPromptPresets();
  }

  function loadSnapshot() {
    snapshotLoading = true;
    beginProcess(snapshotProcess, "snapshot", {});
  }

  function loadHistory() {
    historyLoading = true;
    beginProcess(historyProcess, "listHistory", {
      "limit": historyLimit
    });
  }

  function loadPromptPresets() {
    promptLoading = true;
    beginProcess(promptProcess, "listPromptPresets", {});
  }

  function persistSelectedPromptPreset(presetId) {
    if (!pluginApi || !presetId)
      return;
    pluginApi.pluginSettings.selectedPromptPresetId = presetId;
    pluginApi.saveSettings();
  }

  function setSelectedPromptPreset(presetId) {
    persistSelectedPromptPreset(presetId);
    ToastService.showNotice("Prompt preset set to " + promptLabelForId(presetId));
  }

  function savePromptPreset(presetId, promptText) {
    var normalizedPreset = (presetId || "").trim();
    var normalizedText = (promptText || "").trim();
    if (!normalizedPreset || !normalizedText)
      return;
    mutationLoading = true;
    beginProcess(promptSaveProcess, "savePromptPreset", {
      "presetId": normalizedPreset,
      "label": promptLabelForId(normalizedPreset),
      "promptText": normalizedText
    });
  }


  function toggleRecording() {
    if (mutationLoading) {
      if (statusGroup === "recording" || mutationProcess.commandName === "startRecording") {
        pendingStopRequest = true;
        ToastService.showNotice("Stop requested. Waiting for recording start to settle.");
      } else {
        ToastService.showNotice("Please wait for the current recording command to finish.");
      }
      return;
    }
    if (statusGroup === "recording") {
      stopRecording();
      return;
    }
    if (statusGroup === "processing") {
      ToastService.showNotice("The helper is still processing the last capture.");
      return;
    }
    startRecording();
  }

  function startRecording() {
    mutationLoading = true;
    var params = {
      "clientSource": "plugin",
      "promptPresetId": selectedPromptPresetId || defaultPromptPresetId
    };
    if (activeSessionId)
      params.sessionId = activeSessionId;
    beginProcess(mutationProcess, "startRecording", params);
  }

  function stopRecording() {
    if (mutationLoading) {
      pendingStopRequest = true;
      return;
    }
    mutationLoading = true;
    beginProcess(mutationProcess, "stopRecording", {});
  }

  function exportHistoryItem(jobId) {
    if (!jobId)
      return;
    mutationLoading = true;
    beginProcess(exportProcess, "exportItem", {
      "jobId": jobId
    });
  }

  function copyHistoryItem(item) {
    var text = transcriptTextForItem(item);
    if (!text) {
      ToastService.showNotice("There is no transcript text to copy yet.");
      return;
    }
    clipboardProcess.command = [
      "python3",
      "-c",
      "import subprocess, sys; subprocess.run(['wl-copy'], input=sys.argv[1], text=True, check=True)",
      text
    ];
    clipboardProcess.running = true;
  }

  function saveServiceSettings(settings) {
    mutationLoading = true;
    beginProcess(settingsProcess, "saveSettings", {
      "settings": settings
    });
  }

  function saveSecretValue(secret) {
    var normalized = (secret || "").trim();
    if (!normalized)
      return;
    mutationLoading = true;
    beginProcess(secretProcess, "saveSecret", {
      "secret": normalized
    });
  }

  Process {
    id: snapshotProcess
    property string commandName: "snapshot"
    property string outputText: ""
    property string errorText: ""
    stdout: StdioCollector {
      onStreamFinished: snapshotProcess.outputText = text
    }
    stderr: StdioCollector {
      onStreamFinished: snapshotProcess.errorText = text
    }
    onExited: function (exitCode, exitStatus) {
      root.snapshotLoading = false;
      root.finishJsonProcess(snapshotProcess, function (result) {
        root.snapshotData = result;
        root.configSummary = result.configSummary || {};
      });
    }
  }

  Process {
    id: historyProcess
    property string commandName: "listHistory"
    property string outputText: ""
    property string errorText: ""
    stdout: StdioCollector {
      onStreamFinished: historyProcess.outputText = text
    }
    stderr: StdioCollector {
      onStreamFinished: historyProcess.errorText = text
    }
    onExited: function (exitCode, exitStatus) {
      root.historyLoading = false;
      root.finishJsonProcess(historyProcess, function (result) {
        root.historyItems = result.items || [];
      });
    }
  }

  Process {
    id: promptProcess
    property string commandName: "listPromptPresets"
    property string outputText: ""
    property string errorText: ""
    stdout: StdioCollector {
      onStreamFinished: promptProcess.outputText = text
    }
    stderr: StdioCollector {
      onStreamFinished: promptProcess.errorText = text
    }
    onExited: function (exitCode, exitStatus) {
      root.promptLoading = false;
      root.finishJsonProcess(promptProcess, function (result) {
        root.promptPresets = result.items || [];
        if (!pluginApi?.pluginSettings?.selectedPromptPresetId && result.defaultPromptPresetId)
          root.persistSelectedPromptPreset(result.defaultPromptPresetId);
      });
    }
  }

  Process {
    id: mutationProcess
    property string commandName: ""
    property string outputText: ""
    property string errorText: ""
    stdout: StdioCollector {
      onStreamFinished: mutationProcess.outputText = text
    }
    stderr: StdioCollector {
      onStreamFinished: mutationProcess.errorText = text
    }
    onExited: function (exitCode, exitStatus) {
      root.mutationLoading = false;
      var replayStop = root.pendingStopRequest;
      root.pendingStopRequest = false;
      root.finishJsonProcess(mutationProcess, function (result) {
        if (mutationProcess.commandName === "startRecording") {
          ToastService.showNotice("Recording started.");
        } else if (mutationProcess.commandName === "stopRecording") {
          if (result?.job?.status === "cancelled" && (result?.job?.errorCode === "audio_missing" || result?.job?.errorCode === "no_speech_detected"))
            ToastService.showNotice("Recording stopped, but no speech was captured. Try speaking louder/longer and verify the selected microphone.");
          else
            ToastService.showNotice("Recording stopped. Processing transcript.");
        }
        if (result.state)
          root.snapshotData.state = result.state;
        root.refreshAll();
        if (replayStop && root.statusGroup === "recording")
          root.stopRecording();
      });
    }
  }

  Process {
    id: exportProcess
    property string commandName: "exportItem"
    property string outputText: ""
    property string errorText: ""
    stdout: StdioCollector {
      onStreamFinished: exportProcess.outputText = text
    }
    stderr: StdioCollector {
      onStreamFinished: exportProcess.errorText = text
    }
    onExited: function (exitCode, exitStatus) {
      root.mutationLoading = false;
      root.finishJsonProcess(exportProcess, function (result) {
        ToastService.showNotice("Exported transcript to " + (result.exportPath || "the configured export directory") + ".");
        root.refreshAll();
      });
    }
  }

  Process {
    id: settingsProcess
    property string commandName: "saveSettings"
    property string outputText: ""
    property string errorText: ""
    stdout: StdioCollector {
      onStreamFinished: settingsProcess.outputText = text
    }
    stderr: StdioCollector {
      onStreamFinished: settingsProcess.errorText = text
    }
    onExited: function (exitCode, exitStatus) {
      root.mutationLoading = false;
      root.finishJsonProcess(settingsProcess, function (result) {
        root.configSummary = result.configSummary || root.configSummary;
        if (result.configSummary?.defaultPromptPresetId)
          root.persistSelectedPromptPreset(result.configSummary.defaultPromptPresetId);
        ToastService.showNotice("Voice Dictation settings saved.");
        root.refreshAll();
      });
    }
  }

  Process {
    id: secretProcess
    property string commandName: "saveSecret"
    property string outputText: ""
    property string errorText: ""
    stdout: StdioCollector {
      onStreamFinished: secretProcess.outputText = text
    }
    stderr: StdioCollector {
      onStreamFinished: secretProcess.errorText = text
    }
    onExited: function (exitCode, exitStatus) {
      root.mutationLoading = false;
      root.finishJsonProcess(secretProcess, function (result) {
        ToastService.showNotice("OpenRouter key saved to the local secret file.");
        root.refreshAll();
      });
    }
  }

  Process {
    id: promptSaveProcess
    property string commandName: "savePromptPreset"
    property string outputText: ""
    property string errorText: ""
    stdout: StdioCollector {
      onStreamFinished: promptSaveProcess.outputText = text
    }
    stderr: StdioCollector {
      onStreamFinished: promptSaveProcess.errorText = text
    }
    onExited: function (exitCode, exitStatus) {
      root.mutationLoading = false;
      root.finishJsonProcess(promptSaveProcess, function (result) {
        ToastService.showNotice("System prompt preset saved.");
        root.refreshAll();
      });
    }
  }

  Process {
    id: clipboardProcess
    property string errorText: ""
    stderr: StdioCollector {
      onStreamFinished: clipboardProcess.errorText = text
    }
    onExited: function (exitCode, exitStatus) {
      if (exitCode === 0)
        ToastService.showNotice("Transcript copied to the clipboard.");
      else
        ToastService.showError((clipboardProcess.errorText || "Clipboard copy failed.").trim());
    }
  }

  IpcHandler {
    target: "plugin:voice-dictation"

    function togglePanel() {
      if (!pluginApi)
        return;
      pluginApi.withCurrentScreen(function (screen) {
        pluginApi.togglePanel(screen);
      });
    }

    function openPanel() {
      if (!pluginApi)
        return;
      pluginApi.withCurrentScreen(function (screen) {
        pluginApi.openPanel(screen);
      });
    }

    function closePanel() {
      if (!pluginApi)
        return;
      pluginApi.withCurrentScreen(function (screen) {
        pluginApi.closePanel(screen);
      });
    }

    function refresh() {
      root.refreshAll();
    }

    function startRecording() {
      root.startRecording();
    }

    function stopRecording() {
      root.stopRecording();
    }
  }
}
