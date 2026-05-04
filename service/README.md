# Service contract

The `service/` side owns recording, OpenRouter calls, persistence, export, and secret handling.

## Live paths

- Runtime socket: `${XDG_RUNTIME_DIR}/noctalia-openrouter-voice-widget.sock`
- Config file: `~/.config/noctalia-openrouter-voice-widget/config.json`
- Secret file: `~/.local/state/noctalia-openrouter-voice-widget/openrouter.key`
- History database: `~/.local/state/noctalia-openrouter-voice-widget/history.sqlite3`
- Job cache directory: `~/.cache/noctalia-openrouter-voice-widget/jobs/`
- Default export directory: `~/Documents/VoiceTranscripts/`

## Secret boundary

- The live OpenRouter API key must be stored only in `~/.local/state/noctalia-openrouter-voice-widget/openrouter.key`.
- The intended permission mode for `openrouter.key` is `0600` so only the local user can read and write it.
- `service/config.example.json` intentionally excludes the API key and documents non-secret settings only.
- The plugin must not persist the OpenRouter API key in Noctalia settings.

## Model roles

- `sttModel` selects the speech-to-text model used for the transcript-source stage.
- `sttModel` defaults to `openai/whisper-large-v3`, and that STT response is the source of truth for the raw transcript stored in history.
- `cleanupModel` selects the text cleanup model used for the prompt-shaped polish stage.
- These remain separate because the cleanup request carries its own model ID plus request metadata such as a service session identifier.

## Persistence contract

- The history database stores session and job identity, raw transcript, processed transcript, prompt preset metadata, model IDs, timestamps, and explicit error state.
- Successful STT writes the raw transcript immediately, along with `stt_model_id`, `stt_request_session_id`, and sanitized request metadata for later auditing/debugging.
- Raw audio is temporary operational state in the jobs cache only and must not be persisted in the default SQLite history schema.
- Prompt context is stored as a preset ID plus prompt snapshot text and `prompt_snapshot_hash` so later exports remain reproducible even if presets change.

## Export contract

- Markdown exports must follow `service/export-template.md`.
- Exports include transcript metadata, a raw transcript section, and a processed transcript section.
- Exports must never include the OpenRouter API key or any secret-file contents.

## Service lifecycle

- The helper is a user-scoped background process launched by `~/.config/systemd/user/noctalia-openrouter-voice-widget.service`.
- It binds a Unix domain socket under `${XDG_RUNTIME_DIR}` and fails fast if `XDG_RUNTIME_DIR` is missing or not writable.
- Startup bootstraps the live config file, SQLite schema, cache/export directories, and safe stale-socket cleanup.
- Service restarts preserve persisted config/history/export state and reconcile unfinished in-flight jobs as `failed` with `error_code=service_restarted` rather than silently dropping them.
- Plugin or panel restarts are expected to reconnect over the same local socket without re-owning service state.

## IPC contract

The wire format is newline-delimited JSON over the Unix socket.

Request shape:

```json
{
  "requestId": "client-generated-id",
  "command": "snapshot",
  "params": {}
}
```

Success response shape:

```json
{
  "ok": true,
  "requestId": "client-generated-id",
  "command": "snapshot",
  "protocolVersion": "v1",
  "serviceVersion": "0.1.0",
  "result": {}
}
```

Error response shape:

```json
{
  "ok": false,
  "requestId": "client-generated-id",
  "command": "startRecording",
  "protocolVersion": "v1",
  "serviceVersion": "0.1.0",
  "error": {
    "code": "recording_active",
    "message": "A recording is already active."
  }
}
```

### Public commands

- `snapshot` — query current health, protocol/service version, idle/recording state, config summary, and history counts.
- `ping` — query lightweight liveness/version information.
- `startRecording` — mutation that allocates one active recording job and returns an explicit ack plus job/session IDs.
- `stopRecording` — mutation that ends the active recording placeholder and returns an explicit ack.
- `cancelJob` — mutation that cancels an active or queued job and returns an explicit ack.
- `listHistory` — query recent transcript job rows from SQLite.
- `exportItem` — mutation that renders a Markdown export from the stored history row and records the export.
- `saveSettings` — mutation that persists only non-secret config values to `config.json`.
- `saveSecret` — mutation that writes the OpenRouter key to `openrouter.key` with mode `0600`.

`snapshot` and `ping` stay query-shaped. All mutations return either `{"ack": true, ...}` or a typed error object.

## Recording control state machine

- The helper owns recording control; the plugin stays a thin IPC caller for bar-widget click-to-toggle and panel actions.
- Exactly one non-terminal job may be active at once. If `startRecording` is called while the current lifecycle state is `recording`, the helper returns a typed `recording_active` rejection with the live state payload so callers can remain idempotent.
- Runtime and persisted job statuses use the same explicit names: `idle`, `recording`, `finalizing`, `transcribing`, `postprocessing`, `ready`, `failed`, and `cancelled`.
- `startRecording` creates `~/.cache/noctalia-openrouter-voice-widget/jobs/<job-id>.wav` as temp operational audio, records the job as `recording`, and exposes the path through request metadata only.
- `stopRecording` moves the active job through `finalizing`, `transcribing`, and `postprocessing` before marking it `ready`; Task 4 keeps those transitions local placeholder stages so Task 5 can later replace `transcribing` with the real OpenRouter STT provider work.
- The `transcribing` stage now calls OpenRouter's `/api/v1/audio/transcriptions` endpoint with the configured `sttModel`, persists the returned raw transcript before any later cleanup stage exists, and keeps the cleanup stage separate for Task 6.
- STT retries are bounded to at most one extra attempt for transient provider failures such as rate limits or 5xx responses.
- Authentication failures are fail-closed: the helper stores a sanitized `stt_auth_failed` job error and does not retry invalid credentials.
- Success removes the temp WAV immediately. Failed jobs remain `failed`, cancelled jobs remain `cancelled`, and raw audio is still excluded from SQLite history rows and Markdown exports.

## Local verification helpers

- `python3 service/noctalia_service.py` starts the helper in the foreground.
- `python3 service/ipc_client.py snapshot` sends a local request over the Unix socket and pretty-prints the JSON reply.
- The local client waits briefly for socket readiness, so an immediate `snapshot` after `systemctl --user restart noctalia-openrouter-voice-widget.service` can succeed without adding localhost HTTP or moving lifecycle logic into the plugin.
