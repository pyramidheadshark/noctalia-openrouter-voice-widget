# Setup

## Scope

This guide covers the implemented local setup for the Noctalia host stack, with `niri`, `Noctalia`, and the `noctalia-openrouter-voice-widget` helper service.

## Fast path (recommended)

From project root:

```bash
printf '%s\n' 'OPENROUTER_API_KEY=YOUR_KEY' > .env
bash scripts/install-local.sh
```

This performs local service install/restart and runs a snapshot health check.

## Prerequisites

- Linux user session with `systemd --user`
- Working `niri`
- Working `Noctalia`, version `4.1.2` or newer
- `python3`
- An OpenRouter API key for live STT and cleanup runs

## Repository layout

- `plugin/`, Noctalia QML plugin shell
- `service/`, local Python helper
- `tests/fixtures/audio/deterministic-sample.wav`, deterministic QA input

## 1. Install the plugin into Noctalia

The plugin manifest ID is `voice-dictation`.

For the documented host rollout, expose this repository through Noctalia's local plugin source, then enable:

- source: `url: "local"`
- plugin: `voice-dictation`
- plugin source URL: `local`

The bar widget entry is:

```json
{"id": "plugin:voice-dictation"}
```

Place it in your bar widget list near `ControlCenter` if you want the same host layout described in the project notes.

## 2. Create the live config

```bash
mkdir -p ~/.config/noctalia-openrouter-voice-widget
cp service/config.example.json ~/.config/noctalia-openrouter-voice-widget/config.json
```

The sample config contains only non-secret settings. Current defaults are:

- `sttModel`: `openai/whisper-large-v3`
- `cleanupModel`: `google/gemini-3-flash-preview`
- `defaultPromptPresetId`: `ml-dictation-default`
- `exportDirectory`: `~/Documents/VoiceTranscripts`

## 3. Save the OpenRouter key

```bash
mkdir -p ~/.local/state/noctalia-openrouter-voice-widget
printf '%s' 'YOUR_OPENROUTER_KEY' > ~/.local/state/noctalia-openrouter-voice-widget/openrouter.key
chmod 600 ~/.local/state/noctalia-openrouter-voice-widget/openrouter.key
```

Do not place the key in `config.json`. Do not place it in Noctalia settings.

## 4. Install or refresh the user service

The helper service name is `noctalia-openrouter-voice-widget.service`.

Use these commands after installing the unit file under `~/.config/systemd/user/`:

```bash
systemctl --user daemon-reload
systemctl --user enable --now noctalia-openrouter-voice-widget.service
systemctl --user restart noctalia-openrouter-voice-widget.service
```

Useful checks:

```bash
systemctl --user status noctalia-openrouter-voice-widget.service
python3 service/ipc_client.py snapshot
```

## 5. Run without systemd, if needed

Foreground helper:

```bash
python3 service/noctalia_service.py
```

Local IPC smoke check:

```bash
python3 service/ipc_client.py snapshot
```

## 6. Deterministic QA flow

This path is designed for repeatable checks on the host UI stack.

### One-off deterministic run

```bash
python3 service/ipc_client.py startRecording '{"sessionId":"qa-session","mockProvider":"deterministic"}'
python3 service/ipc_client.py stopRecording
python3 service/ipc_client.py listHistory '{"limit":5}'
```

### QA mode through environment

```bash
export NOCTALIA_OPENROUTER_QA_MODE=deterministic
python3 service/ipc_client.py startRecording '{"sessionId":"qa-session"}'
python3 service/ipc_client.py stopRecording
python3 service/ipc_client.py exportItem '{"jobId":"REPLACE_WITH_JOB_ID"}'
```

Optional transcript overrides:

```bash
export NOCTALIA_OPENROUTER_STT_FIXTURE_TEXT='fixture raw transcript'
export NOCTALIA_OPENROUTER_CLEANUP_FIXTURE_TEXT='fixture cleaned transcript'
```

### What success looks like

- `snapshot` reports a healthy helper service
- `stopRecording` returns the transition path `finalizing`, `transcribing`, `postprocessing`, `ready`
- `listHistory` shows the job with status `ready`
- `exportItem` writes Markdown into `~/Documents/VoiceTranscripts/`
- the temp WAV is removed after a successful run

## Related docs

- [../README.md](../README.md)
- [ARCHITECTURE.md](ARCHITECTURE.md)
- [TROUBLESHOOTING.md](TROUBLESHOOTING.md)
- [../service/README.md](../service/README.md)
