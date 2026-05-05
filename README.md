# Noctalia OpenRouter Voice Widget

Русский • [English](#english)

## Русский

Локальный voice-dictation плагин для `niri + noctalia`:
- **plugin/** — QML UI (бар-виджет, панель, настройки)
- **service/** — Python helper (запись микрофона, OpenRouter STT/cleanup, история, экспорт)

### Текущий статус (зафиксировано)
- ✅ Сервис стабильно поднимается как user systemd unit
- ✅ Запись/стоп работают через локальный IPC socket
- ✅ История и транскрипты сохраняются (`rawTranscript` + `processedTranscript`)
- ✅ Панель `voice-dictation` загружается без QML crash
- ✅ Горячая клавиша панели: `Mod+Shift+D`

---

## Быстрый старт (нативно, локально)

```bash
cd ~/Repos/noctalia-openrouter-voice-widget
printf '%s\n' 'OPENROUTER_API_KEY=YOUR_KEY' > .env
bash scripts/install-local.sh
```

Что делает installer:
- ставит/обновляет `~/.config/systemd/user/noctalia-openrouter-voice-widget.service`
- копирует plugin-файлы в `~/.config/noctalia/plugins/voice-dictation/`
- пишет секрет в `~/.local/state/noctalia-openrouter-voice-widget/openrouter.key` (0600)
- нормализует keybind и перезапускает helper
- выполняет `snapshot` sanity check

---

## Где в UI настройки ключа / моделей / системного промпта

`Noctalia Settings → Plugins → Voice Dictation`

Там доступны:
- OpenRouter key
- STT model
- Cleanup model
- Default prompt preset
- Текст системного промпта (для выбранного preset)

---

## Проверка работоспособности

### 1) Health
```bash
python3 service/ipc_client.py snapshot
```
Ожидаемо: `"ok": true`, `health.status = "ok"`.

### 2) Живой цикл записи
```bash
python3 service/ipc_client.py startRecording
sleep 4
python3 service/ipc_client.py stopRecording
python3 service/ipc_client.py listHistory '{"limit":3}'
```
Ожидаемо: в истории появляется новый item, обычно `status: ready` (или `cancelled` при тишине).

### 3) Экспорт
```bash
python3 service/ipc_client.py exportItem '{"jobId":"REPLACE_WITH_JOB_ID"}'
ls ~/Documents/VoiceTranscripts
```

---

## UI управление

- **ЛКМ по виджету в баре** — старт/стоп записи
- **Кнопка `settings` справа в виджете** — открыть/закрыть панель
- **`Mod+Shift+D`** — toggle панели

---

## Важные пути

- Repo: `~/Repos/noctalia-openrouter-voice-widget`
- Plugin deploy: `~/.config/noctalia/plugins/voice-dictation/`
- Service config: `~/.config/noctalia-openrouter-voice-widget/config.json`
- Secret: `~/.local/state/noctalia-openrouter-voice-widget/openrouter.key`
- Socket: `${XDG_RUNTIME_DIR}/noctalia-openrouter-voice-widget.sock`
- History DB: `~/.local/state/noctalia-openrouter-voice-widget/history.sqlite3`

---

## Troubleshooting (коротко)

1. **Плагин не открывается / черная панель**
   - перезапусти shell:
   ```bash
   pkill -f "qs -c noctalia-shell"; nohup qs -c noctalia-shell >/tmp/noctalia-shell.log 2>&1 &
   ```

2. **Нет текста после записи**
   - проверь `listHistory` и `snapshot`
   - проверь микрофон и speaking level

3. **Нужен полный traceback**
   ```bash
   journalctl --user -u noctalia-openrouter-voice-widget.service -n 300 --no-pager
   ```

Подробно: [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md)

---

## English

Local voice-dictation plugin for `niri + noctalia`:
- **plugin/** — QML UI (bar widget, panel, settings)
- **service/** — Python helper (mic capture, OpenRouter STT/cleanup, history, export)

### Current status
- ✅ Service runs reliably as a user systemd unit
- ✅ Start/stop recording works via local Unix socket IPC
- ✅ Transcript history is persisted (`rawTranscript` + `processedTranscript`)
- ✅ `voice-dictation` panel loads without QML crash
- ✅ Panel hotkey is `Mod+Shift+D`

### Quick install
```bash
cd ~/Repos/noctalia-openrouter-voice-widget
printf '%s\n' 'OPENROUTER_API_KEY=YOUR_KEY' > .env
bash scripts/install-local.sh
```

### Where to configure key/models/system prompt in UI
`Noctalia Settings → Plugins → Voice Dictation`

### Quick verify
```bash
python3 service/ipc_client.py snapshot
python3 service/ipc_client.py startRecording
sleep 4
python3 service/ipc_client.py stopRecording
python3 service/ipc_client.py listHistory '{"limit":3}'
```

### Docs
- [docs/SETUP.md](docs/SETUP.md)
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md)
- [service/README.md](service/README.md)
