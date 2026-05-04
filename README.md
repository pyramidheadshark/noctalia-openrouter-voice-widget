# Noctalia OpenRouter Voice Widget

Русский • [English](#english)

## Русский

Локальный voice-dictation виджет для `niri + noctalia`:
- **plugin/** — тонкий QML UI (bar + panel + settings)
- **service/** — Python helper (запись, OpenRouter, история, экспорт, секрет)

### Главное
- Секрет хранится **только** в `~/.local/state/noctalia-openrouter-voice-widget/openrouter.key` (`0600`)
- Нет localhost HTTP: только Unix socket `${XDG_RUNTIME_DIR}/noctalia-openrouter-voice-widget.sock`
- Две роли моделей:
  - `sttModel = openai/whisper-large-v3`
  - `cleanupModel = google/gemini-3-flash-preview`
- Хранятся `raw_transcript` + `processed_transcript`, raw audio по умолчанию не ретейнится
- Экспорт в `~/Documents/VoiceTranscripts`

---

## Быстрая установка (локально, нативно)

```bash
cd ~/Repos/noctalia-openrouter-voice-widget
printf '%s\n' 'OPENROUTER_API_KEY=YOUR_KEY' > .env
bash scripts/install-local.sh
```

Скрипт:
- ставит user systemd unit,
- создаёт config из `service/config.example.json` (если нет),
- кладёт ключ из `.env` в секретный файл,
- перезапускает сервис и делает `snapshot` проверку.

---

## Проверка что всё работает

### 1) Базовая живость сервиса
```bash
python3 service/ipc_client.py snapshot
```

### 2) Детерминированный E2E (без микрофона и live OpenRouter)
```bash
python3 service/ipc_client.py startRecording '{"sessionId":"qa-session","mockProvider":"deterministic"}'
python3 service/ipc_client.py stopRecording
python3 service/ipc_client.py listHistory '{"limit":5}'
```

### 3) Экспорт
```bash
python3 service/ipc_client.py exportItem '{"jobId":"REPLACE_WITH_JOB_ID"}'
ls ~/Documents/VoiceTranscripts
```

---

## Интеграция в UI (`niri + noctalia`)

Проверить:
- в `~/.config/noctalia/plugins.json` есть `voice-dictation` с `sourceUrl: "local"`
- в bar widgets есть `{"id":"plugin:voice-dictation"}`
- в `~/.config/niri/cfg/keybinds.kdl` есть `Mod+Shift+D -> plugin:voice-dictation togglePanel`

---

## Документация

- [docs/SETUP.md](docs/SETUP.md) — установка и rollout
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — архитектура и границы
- [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) — диагностика
- [service/README.md](service/README.md) — контракт сервиса/IPC

---

## English

Local voice-dictation widget for `niri + noctalia`:
- **plugin/** — thin QML UI shell (bar + panel + settings)
- **service/** — Python helper (recording, OpenRouter, history, export, secret)

### Core guarantees
- Secret lives **only** in `~/.local/state/noctalia-openrouter-voice-widget/openrouter.key` (`0600`)
- No localhost HTTP; only Unix socket `${XDG_RUNTIME_DIR}/noctalia-openrouter-voice-widget.sock`
- Separate model roles:
  - `sttModel = openai/whisper-large-v3`
  - `cleanupModel = google/gemini-3-flash-preview`
- History keeps `raw_transcript` + `processed_transcript`; raw audio is not retained by default
- Exports are written to `~/Documents/VoiceTranscripts`

### Quick local install

```bash
cd ~/Repos/noctalia-openrouter-voice-widget
printf '%s\n' 'OPENROUTER_API_KEY=YOUR_KEY' > .env
bash scripts/install-local.sh
```

The installer:
- installs the user systemd unit,
- creates config from `service/config.example.json` (if missing),
- writes the key from `.env` into the secret file,
- restarts the helper and runs `snapshot` check.

### Quick verification

```bash
python3 service/ipc_client.py snapshot
python3 service/ipc_client.py startRecording '{"sessionId":"qa-session","mockProvider":"deterministic"}'
python3 service/ipc_client.py stopRecording
python3 service/ipc_client.py listHistory '{"limit":5}'
```

### Docs
- [docs/SETUP.md](docs/SETUP.md)
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md)
- [service/README.md](service/README.md)
