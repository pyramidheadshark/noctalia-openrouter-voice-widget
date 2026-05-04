# Troubleshooting

## First checks

### Service is not responding

Run:

```bash
systemctl --user status noctalia-openrouter-voice-widget.service
python3 service/ipc_client.py snapshot
```

If `snapshot` fails right after a restart, retry once after the service finishes binding its Unix socket. The local IPC client already does a short bounded readiness retry for this case.

### `XDG_RUNTIME_DIR` problems

The service socket lives under `${XDG_RUNTIME_DIR}`. If that variable is missing or points to an unwritable path, the helper should fail fast.

Check:

```bash
printenv XDG_RUNTIME_DIR
ls "$XDG_RUNTIME_DIR"
```

This can happen in `su` or `sudo` shells that do not preserve the user session environment.

## Secret and auth issues

### OpenRouter auth fails

Confirm that the live key exists only here:

```bash
ls -l ~/.local/state/noctalia-openrouter-voice-widget/openrouter.key
```

Expected:

- path exists
- file is readable by the local user
- intended mode is `0600`

The key must not be stored in:

- `~/.config/noctalia-openrouter-voice-widget/config.json`
- Noctalia plugin settings
- `.env` files used for general shell setup

### Live key was changed

After replacing the key, restart the helper service:

```bash
systemctl --user restart noctalia-openrouter-voice-widget.service
python3 service/ipc_client.py snapshot
```

## Recording and state issues

### A second recording start is rejected

That is expected. The helper allows only one non-terminal job at a time. A duplicate `startRecording` returns a typed `recording_active` error with state details.

### Temp audio was not removed

Raw audio is operational state only. On successful completion, the temp WAV should be removed from `~/.cache/noctalia-openrouter-voice-widget/jobs/`.

If it remains, inspect recent service logs:

```bash
journalctl --user -u noctalia-openrouter-voice-widget.service -n 100 --no-pager
```

Look for a job that stopped in `failed` or `cancelled` instead of `ready`.

## Deterministic QA checks

### Verify the fixture path

```bash
ls tests/fixtures/audio/deterministic-sample.wav
```

### Run a deterministic end-to-end check

```bash
python3 service/ipc_client.py startRecording '{"sessionId":"qa-session","mockProvider":"deterministic"}'
python3 service/ipc_client.py stopRecording
python3 service/ipc_client.py listHistory '{"limit":5}'
```

Success indicators:

- the job reaches `ready`
- the transition path includes `finalizing`, `transcribing`, `postprocessing`
- the export contains both raw and processed transcript sections
- metadata shows deterministic transport for STT and cleanup

### Override deterministic text

```bash
export NOCTALIA_OPENROUTER_STT_FIXTURE_TEXT='fixture raw transcript'
export NOCTALIA_OPENROUTER_CLEANUP_FIXTURE_TEXT='fixture cleaned transcript'
```

Use this when you need stable text assertions without changing the fixture WAV.

## Export issues

### Export file not found

Exports are written to `~/Documents/VoiceTranscripts/` by default.

Check:

```bash
python3 service/ipc_client.py listHistory '{"limit":5}'
python3 service/ipc_client.py exportItem '{"jobId":"REPLACE_WITH_JOB_ID"}'
ls ~/Documents/VoiceTranscripts
```

The current filename pattern is `{{completedAt}}-{{jobId}}.md`.

## Where to look next

- [../README.md](../README.md)
- [SETUP.md](SETUP.md)
- [ARCHITECTURE.md](ARCHITECTURE.md)
- [../service/README.md](../service/README.md)
