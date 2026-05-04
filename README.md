# Noctalia OpenRouter Voice Widget

Русский | [English](#english)

## Русский

Тонкий voice-dictation плагин для Noctalia с отдельным локальным helper service.

### Что это

Проект разделён на две части:

- `plugin/`, QML UI-оболочка для Noctalia
- `service/`, локальный Python helper для записи, OpenRouter, истории, экспорта и хранения секрета

Плагин не хранит OpenRouter ключ и не владеет пайплайном обработки. Источник истины, это сервис.

### Что уже реализовано

- user-scoped systemd service для helper процесса
- локальный Unix socket IPC, без localhost HTTP
- отдельные модели для STT и cleanup
- история с raw и processed transcript
- экспорт Markdown в `~/Documents/VoiceTranscripts/`
- deterministic QA режим без микрофона и без live OpenRouter вызовов

### Пути и границы безопасности

- Конфиг: `~/.config/noctalia-openrouter-voice-widget/config.json`
- Секрет: `~/.local/state/noctalia-openrouter-voice-widget/openrouter.key`
- База истории: `~/.local/state/noctalia-openrouter-voice-widget/history.sqlite3`
- Кэш job-ов: `~/.cache/noctalia-openrouter-voice-widget/jobs/`
- Сокет: `${XDG_RUNTIME_DIR}/noctalia-openrouter-voice-widget.sock`

OpenRouter API key должен жить только в `~/.local/state/noctalia-openrouter-voice-widget/openrouter.key`. Для этого файла ожидается режим `0600`. Не храните ключ в Noctalia settings, `.env`, `config.json` или export-файлах.

### Модели по умолчанию

- `sttModel`: `openai/whisper-large-v3`
- `cleanupModel`: `google/gemini-3-flash-preview`

Это разные роли. `sttModel` создаёт raw transcript, `cleanupModel` полирует текст и сохраняет processed transcript отдельно.

### Быстрый старт, niri + noctalia

1. Подготовьте хост с рабочими `niri` и `Noctalia`.
2. Добавьте этот плагин в Noctalia через локальный source `url: "local"`.
3. Включите плагин `voice-dictation` с `sourceUrl: "local"`.
4. Подключите виджет в bar, например `{"id": "plugin:voice-dictation"}` рядом с `ControlCenter`.
5. Убедитесь, что user service `noctalia-openrouter-voice-widget.service` установлен в `~/.config/systemd/user/`.
6. Скопируйте пример конфига и при необходимости отредактируйте не-секретные настройки:

```bash
mkdir -p ~/.config/noctalia-openrouter-voice-widget
cp service/config.example.json ~/.config/noctalia-openrouter-voice-widget/config.json
```

7. Сохраните OpenRouter ключ в секретный файл:

```bash
mkdir -p ~/.local/state/noctalia-openrouter-voice-widget
printf '%s' 'YOUR_OPENROUTER_KEY' > ~/.local/state/noctalia-openrouter-voice-widget/openrouter.key
chmod 600 ~/.local/state/noctalia-openrouter-voice-widget/openrouter.key
```

8. Запустите или перезапустите helper service:

```bash
systemctl --user daemon-reload
systemctl --user restart noctalia-openrouter-voice-widget.service
systemctl --user status noctalia-openrouter-voice-widget.service
```

9. Проверьте локальный IPC ответ:

```bash
python3 service/ipc_client.py snapshot
```

### Локальная разработка без systemd

Можно поднять helper в foreground:

```bash
python3 service/noctalia_service.py
```

В другой сессии:

```bash
python3 service/ipc_client.py snapshot
```

### Детерминированный QA режим

Для детерминированной проверки используется fixture WAV: `tests/fixtures/audio/deterministic-sample.wav`.

Вариант 1, разовый вызов через IPC:

```bash
python3 service/ipc_client.py startRecording '{"sessionId":"qa-session","mockProvider":"deterministic"}'
python3 service/ipc_client.py stopRecording
python3 service/ipc_client.py listHistory '{"limit":5}'
```

Вариант 2, включить QA режим через переменную окружения:

```bash
export NOCTALIA_OPENROUTER_QA_MODE=deterministic
python3 service/ipc_client.py startRecording '{"sessionId":"qa-session"}'
python3 service/ipc_client.py stopRecording
```

При необходимости можно переопределить deterministic transcript text:

```bash
export NOCTALIA_OPENROUTER_STT_FIXTURE_TEXT='fixture raw transcript'
export NOCTALIA_OPENROUTER_CLEANUP_FIXTURE_TEXT='fixture cleaned transcript'
```

### Ожидаемый пайплайн обработки

`startRecording` создаёт активный job. `stopRecording` переводит его через состояния `finalizing` -> `transcribing` -> `postprocessing` -> `ready`. После успешного завершения временный WAV удаляется из job cache. По умолчанию raw audio не хранится в history.

### Управление сервисом

```bash
systemctl --user start noctalia-openrouter-voice-widget.service
systemctl --user restart noctalia-openrouter-voice-widget.service
systemctl --user stop noctalia-openrouter-voice-widget.service
journalctl --user -u noctalia-openrouter-voice-widget.service -n 100 --no-pager
```

### Где что читать дальше

- [docs/SETUP.md](docs/SETUP.md), установка и host rollout
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), устройство plugin/service boundary
- [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md), частые проблемы и проверки
- [service/README.md](service/README.md), service contract и IPC детали

---

## English

Thin voice dictation plugin for Noctalia, backed by a separate local helper service.

### What this project is

The workspace is split into two product areas:

- `plugin/`, the QML UI shell for Noctalia
- `service/`, the local Python helper for recording, OpenRouter calls, history, exports, and secret handling

The plugin does not own the OpenRouter key or the processing pipeline. The service is the source of truth.

### What is implemented today

- user-scoped systemd helper service
- local Unix socket IPC, no localhost HTTP surface
- separate STT and cleanup model roles
- history retention for raw and processed transcripts
- Markdown export to `~/Documents/VoiceTranscripts/`
- deterministic QA mode with no microphone and no live OpenRouter calls

### Paths and security boundary

- Config: `~/.config/noctalia-openrouter-voice-widget/config.json`
- Secret: `~/.local/state/noctalia-openrouter-voice-widget/openrouter.key`
- History DB: `~/.local/state/noctalia-openrouter-voice-widget/history.sqlite3`
- Job cache: `~/.cache/noctalia-openrouter-voice-widget/jobs/`
- Socket: `${XDG_RUNTIME_DIR}/noctalia-openrouter-voice-widget.sock`

The OpenRouter API key must live only in `~/.local/state/noctalia-openrouter-voice-widget/openrouter.key`. The intended mode is `0600`. Do not store the key in Noctalia settings, `.env`, `config.json`, or exports.

### Default models

- `sttModel`: `openai/whisper-large-v3`
- `cleanupModel`: `google/gemini-3-flash-preview`

These roles are intentionally separate. `sttModel` produces the raw transcript, `cleanupModel` polishes text and stores the processed transcript separately.

### Quick start, niri + noctalia

1. Prepare a host with working `niri` and `Noctalia`.
2. Add this plugin to Noctalia through a local source with `url: "local"`.
3. Enable the `voice-dictation` plugin with `sourceUrl: "local"`.
4. Mount the bar widget, for example `{"id": "plugin:voice-dictation"}` near `ControlCenter`.
5. Make sure the user service `noctalia-openrouter-voice-widget.service` is installed under `~/.config/systemd/user/`.
6. Copy the sample config, then adjust only non-secret settings if needed:

```bash
mkdir -p ~/.config/noctalia-openrouter-voice-widget
cp service/config.example.json ~/.config/noctalia-openrouter-voice-widget/config.json
```

7. Save the OpenRouter key into the secret file:

```bash
mkdir -p ~/.local/state/noctalia-openrouter-voice-widget
printf '%s' 'YOUR_OPENROUTER_KEY' > ~/.local/state/noctalia-openrouter-voice-widget/openrouter.key
chmod 600 ~/.local/state/noctalia-openrouter-voice-widget/openrouter.key
```

8. Start or restart the helper service:

```bash
systemctl --user daemon-reload
systemctl --user restart noctalia-openrouter-voice-widget.service
systemctl --user status noctalia-openrouter-voice-widget.service
```

9. Verify the local IPC path:

```bash
python3 service/ipc_client.py snapshot
```

### Local development without systemd

You can run the helper in the foreground:

```bash
python3 service/noctalia_service.py
```

In another shell:

```bash
python3 service/ipc_client.py snapshot
```

### Deterministic QA mode

Deterministic QA uses the checked-in fixture WAV at `tests/fixtures/audio/deterministic-sample.wav`.

Option 1, one-off IPC flow:

```bash
python3 service/ipc_client.py startRecording '{"sessionId":"qa-session","mockProvider":"deterministic"}'
python3 service/ipc_client.py stopRecording
python3 service/ipc_client.py listHistory '{"limit":5}'
```

Option 2, enable QA mode through an environment variable:

```bash
export NOCTALIA_OPENROUTER_QA_MODE=deterministic
python3 service/ipc_client.py startRecording '{"sessionId":"qa-session"}'
python3 service/ipc_client.py stopRecording
```

You can also override deterministic transcript text when needed:

```bash
export NOCTALIA_OPENROUTER_STT_FIXTURE_TEXT='fixture raw transcript'
export NOCTALIA_OPENROUTER_CLEANUP_FIXTURE_TEXT='fixture cleaned transcript'
```

### Expected processing flow

`startRecording` allocates the active job. `stopRecording` moves it through `finalizing` -> `transcribing` -> `postprocessing` -> `ready`. On success the temporary WAV is removed from the job cache. Raw audio is not retained in history by default.

### Service control commands

```bash
systemctl --user start noctalia-openrouter-voice-widget.service
systemctl --user restart noctalia-openrouter-voice-widget.service
systemctl --user stop noctalia-openrouter-voice-widget.service
journalctl --user -u noctalia-openrouter-voice-widget.service -n 100 --no-pager
```

### Read next

- [docs/SETUP.md](docs/SETUP.md), install and host rollout
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), plugin and service boundary
- [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md), common checks and failures
- [service/README.md](service/README.md), service contract and IPC details
