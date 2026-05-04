#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE_NAME="noctalia-openrouter-voice-widget.service"
UNIT_TARGET="$HOME/.config/systemd/user/$SERVICE_NAME"
CONFIG_DIR="$HOME/.config/noctalia-openrouter-voice-widget"
STATE_DIR="$HOME/.local/state/noctalia-openrouter-voice-widget"
CONFIG_TARGET="$CONFIG_DIR/config.json"
SECRET_TARGET="$STATE_DIR/openrouter.key"

mkdir -p "$HOME/.config/systemd/user" "$CONFIG_DIR" "$STATE_DIR"

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

systemctl --user daemon-reload
systemctl --user enable --now "$SERVICE_NAME"
systemctl --user restart "$SERVICE_NAME"

echo "Installed. Service status:"
systemctl --user --no-pager --lines=20 status "$SERVICE_NAME" || true

echo
echo "Snapshot check:"
python3 "$ROOT_DIR/service/ipc_client.py" snapshot
