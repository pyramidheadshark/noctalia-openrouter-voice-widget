#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE_NAME="noctalia-openrouter-voice-widget.service"
UNIT_TARGET="$HOME/.config/systemd/user/$SERVICE_NAME"
CONFIG_DIR="$HOME/.config/noctalia-openrouter-voice-widget"
STATE_DIR="$HOME/.local/state/noctalia-openrouter-voice-widget"
PLUGIN_DIR="$HOME/.config/noctalia/plugins/voice-dictation"
PLUGIN_SERVICE_DIR="$PLUGIN_DIR/service"
NOCTALIA_PLUGINS_JSON="$HOME/.config/noctalia/plugins.json"
NIRI_KEYBINDS="$HOME/.config/niri/cfg/keybinds.kdl"
CONFIG_TARGET="$CONFIG_DIR/config.json"
SECRET_TARGET="$STATE_DIR/openrouter.key"

mkdir -p "$HOME/.config/systemd/user" "$CONFIG_DIR" "$STATE_DIR" "$PLUGIN_DIR" "$PLUGIN_SERVICE_DIR"

cat >"$UNIT_TARGET" <<EOF
[Unit]
Description=Noctalia OpenRouter Voice Widget helper service
After=default.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 %h/Repos/noctalia-openrouter-voice-widget/service/noctalia_service.py
WorkingDirectory=%h/Repos/noctalia-openrouter-voice-widget
Restart=always
RestartSec=2
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=default.target
EOF

if [[ ! -f "$CONFIG_TARGET" ]]; then
  cp "$ROOT_DIR/service/config.example.json" "$CONFIG_TARGET"
fi

if [[ -f "$ROOT_DIR/.env" ]]; then
  # shellcheck disable=SC1091
  source "$ROOT_DIR/.env"
  if [[ -n "${OPENROUTER_API_KEY:-}" ]]; then
    printf '%s' "$OPENROUTER_API_KEY" > "$SECRET_TARGET"
    chmod 600 "$SECRET_TARGET"
  fi
fi

# Install plugin files into Noctalia local plugin directory.
cp "$ROOT_DIR/plugin/manifest.json" "$PLUGIN_DIR/manifest.json"
cp "$ROOT_DIR/plugin/Main.qml" "$PLUGIN_DIR/Main.qml"
cp "$ROOT_DIR/plugin/BarWidget.qml" "$PLUGIN_DIR/BarWidget.qml"
cp "$ROOT_DIR/plugin/Panel.qml" "$PLUGIN_DIR/Panel.qml"
cp "$ROOT_DIR/plugin/Settings.qml" "$PLUGIN_DIR/Settings.qml"
cp "$ROOT_DIR/service/ipc_client.py" "$PLUGIN_SERVICE_DIR/ipc_client.py"

# Ensure Noctalia plugin state has voice-dictation enabled.
python3 - <<'PY'
import json
from pathlib import Path

plugins_json = Path.home() / ".config" / "noctalia" / "plugins.json"
plugins_json.parent.mkdir(parents=True, exist_ok=True)

if plugins_json.exists():
    data = json.loads(plugins_json.read_text(encoding="utf-8"))
else:
    data = {"version": 2, "sources": [], "states": {}}

states = data.setdefault("states", {})

state = states.setdefault("voice-dictation", {})
state["enabled"] = True
state["sourceUrl"] = "https://github.com/noctalia-dev/noctalia-plugins"

sources = data.setdefault("sources", [])
clean_sources = []
for src in sources:
    url = (src.get("url") or "").strip()
    name = (src.get("name") or "").strip().lower()
    if name in {"local plugins", "voice dictation local"}:
        continue
    if url == "local":
        continue
    if url.startswith("file://") and "noctalia-openrouter-voice-widget" in url:
        continue
    clean_sources.append(src)
data["sources"] = clean_sources

plugins_json.write_text(json.dumps(data, indent=4) + "\n", encoding="utf-8")
PY

# Ensure keybind uses current Noctalia IPC shape.
if [[ -f "$NIRI_KEYBINDS" ]]; then
  python3 - <<'PY'
from pathlib import Path

keybinds = Path.home() / ".config" / "niri" / "cfg" / "keybinds.kdl"
text = keybinds.read_text(encoding="utf-8")
text = text.replace(
    'qs -c noctalia-shell ipc call plugin:voice-dictation togglePanel',
    'qs -c noctalia-shell ipc call plugin togglePanel voice-dictation',
)
if 'plugin togglePanel voice-dictation' not in text:
    marker = 'Mod+F1                              hotkey-overlay-title="Toggle Keybind Cheatsheet: noctalia keybind-cheatsheet" { spawn-sh "qs -c noctalia-shell ipc call plugin:keybind-cheatsheet toggle"; }\n'
    add = '    Mod+Shift+D                         hotkey-overlay-title="Toggle Voice Dictation Panel: noctalia voice-dictation" { spawn-sh "qs -c noctalia-shell ipc call plugin togglePanel voice-dictation"; }\n'
    if marker in text:
        text = text.replace(marker, marker + add)
keybinds.write_text(text, encoding="utf-8")
PY
  niri msg action load-config-file >/dev/null 2>&1 || true
fi

systemctl --user daemon-reload
systemctl --user enable --now "$SERVICE_NAME"
systemctl --user restart "$SERVICE_NAME"

echo "Installed. Service status:"
systemctl --user --no-pager --lines=20 status "$SERVICE_NAME" || true

echo
echo "Snapshot check:"
python3 "$ROOT_DIR/service/ipc_client.py" snapshot

echo
echo "If Noctalia is already running, reload shell/plugin layer manually from your active desktop session:"
echo "  niri msg action load-config-file"
echo "  qs -c noctalia-shell ipc call plugin openPanel voice-dictation"
