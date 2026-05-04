# Architecture

## Overview

The project is intentionally split into a thin UI shell and a stateful local helper service.

- `plugin/` handles presentation, local user actions, and IPC requests
- `service/` owns recording, OpenRouter calls, history, export, config, and secret handling

That split keeps the Noctalia plugin small and keeps the sensitive state in one local service boundary.

## Main components

### Plugin

Key files:

- `plugin/Main.qml`
- `plugin/BarWidget.qml`
- `plugin/Panel.qml`
- `plugin/Settings.qml`
- `plugin/manifest.json`

Responsibilities:

- render the bar-first UI and panel
- show current lifecycle state
- send local IPC commands
- keep only presentation-oriented plugin settings, such as the selected prompt preset and history limit

The plugin does not persist the OpenRouter key. It calls the service for `saveSecret`, `saveSettings`, recording control, history reads, and exports.

### Service

Key files:

- `service/noctalia_service.py`
- `service/ipc_client.py`
- `service/history.schema.sql`
- `service/config.example.json`
- `service/export-template.md`

Responsibilities:

- own the recording lifecycle
- call OpenRouter for STT and cleanup in live mode
- persist transcript history in SQLite
- export Markdown transcript files
- store the OpenRouter key in the local secret file
- support deterministic QA without a microphone or live provider

## Live paths

- socket: `${XDG_RUNTIME_DIR}/noctalia-openrouter-voice-widget.sock`
- config: `~/.config/noctalia-openrouter-voice-widget/config.json`
- secret: `~/.local/state/noctalia-openrouter-voice-widget/openrouter.key`
- history DB: `~/.local/state/noctalia-openrouter-voice-widget/history.sqlite3`
- jobs cache: `~/.cache/noctalia-openrouter-voice-widget/jobs/`
- exports: `~/Documents/VoiceTranscripts/`

## IPC boundary

The transport is newline-delimited JSON over a Unix domain socket. There is no localhost HTTP surface.

Public commands documented by the current contract:

- `snapshot`
- `ping`
- `startRecording`
- `stopRecording`
- `cancelJob`
- `listHistory`
- `listPromptPresets`
- `exportItem`
- `savePromptPreset`
- `saveSettings`
- `saveSecret`
- `duplicatePromptPreset`

The plugin is expected to reconnect after panel or plugin restarts. The local client also tolerates brief socket-readiness gaps right after a helper-service restart.

## Recording pipeline

The service owns a single active recording job at a time.

State machine:

`idle` -> `recording` -> `finalizing` -> `transcribing` -> `postprocessing` -> `ready`

Other terminal states:

- `failed`
- `cancelled`

Behavior:

- `startRecording` allocates the active job and temp WAV path
- `stopRecording` runs the transition path and persists results
- STT stores the raw transcript with `sttModel`
- cleanup stores the processed transcript with `cleanupModel`
- successful completion removes the temp WAV from the jobs cache
- raw audio is not retained in durable history by default

## Prompt presets and transcript retention

Prompt presets live in the service-owned SQLite database, not in Noctalia plugin settings.

Each job stores:

- prompt preset ID
- prompt snapshot text
- prompt snapshot hash
- raw transcript
- processed transcript
- model IDs
- sanitized request metadata

That keeps old history reproducible even if a prompt preset is edited later.

## Security boundary

The most important boundary is the secret path.

- live key path: `~/.local/state/noctalia-openrouter-voice-widget/openrouter.key`
- intended mode: `0600`
- the key is excluded from `service/config.example.json`
- exports and history must not contain secret-file contents

## Deterministic QA lane

The repo includes `tests/fixtures/audio/deterministic-sample.wav`.

Deterministic mode can be entered in two supported ways:

- pass `mockProvider: "deterministic"` to `startRecording`
- set `NOCTALIA_OPENROUTER_QA_MODE=deterministic`

In that mode, the service materializes the fixture WAV into the temp job path and replaces live STT and cleanup calls with deterministic local responses. Optional fixture text overrides still work through:

- `NOCTALIA_OPENROUTER_STT_FIXTURE_TEXT`
- `NOCTALIA_OPENROUTER_CLEANUP_FIXTURE_TEXT`

## Related docs

- [../README.md](../README.md)
- [SETUP.md](SETUP.md)
- [TROUBLESHOOTING.md](TROUBLESHOOTING.md)
- [../service/README.md](../service/README.md)
