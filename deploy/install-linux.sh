#!/usr/bin/env sh
# Installs the bot as a Linux service (systemd): it starts when the machine boots,
# restarts after crashes and self-updates, and needs nobody logged in.
#   ./deploy/install-linux.sh            install or reinstall
#   sudo systemctl stop ai-discord-bot   stop it
#   journalctl -u ai-discord-bot -f      watch the logs
set -e
DIR="$(cd "$(dirname "$0")/.." && pwd)"
NAME="${SERVICE_NAME:-ai-discord-bot}"

if [ ! -f "$DIR/.env" ]; then
  cp "$DIR/.env.example" "$DIR/.env"
  echo "Created $DIR/.env. Fill it in (see SETUP.md), then run this script again."
  exit 1
fi
if [ ! -d "$DIR/.git" ]; then
  echo "Note: this folder isn't a git clone, so the bot can't update itself."
  echo "      For self-updates, install with: git clone <repo URL>"
fi

sudo tee "/etc/systemd/system/$NAME.service" >/dev/null <<UNIT
[Unit]
Description=AI Discord bot ($DIR)
Wants=network-online.target
After=network-online.target

[Service]
User=$(id -un)
WorkingDirectory=$DIR
ExecStart=/bin/sh $DIR/start.sh
# start.sh already restarts after updates and crashes; this covers start.sh itself.
Restart=always
RestartSec=15

[Install]
WantedBy=multi-user.target
UNIT

sudo systemctl daemon-reload
sudo systemctl enable --now "$NAME"
echo "Installed. The bot runs now and after every reboot."
echo "Logs: journalctl -u $NAME -f"
