#!/usr/bin/env sh
# Mac / Linux: sets everything up on first run, then starts the bot.
set -e
cd "$(dirname "$0")"

if [ ! -f .env ]; then
  cp .env.example .env
  echo "Created .env. Open it, fill in DISCORD_TOKEN and your AI key (see SETUP.md), then run ./start.sh again."
  exit 1
fi

PYTHON=python3
command -v $PYTHON >/dev/null 2>&1 || PYTHON=python
if [ ! -d .venv ]; then
  echo "First run: creating a Python environment..."
  $PYTHON -m venv .venv
fi
. .venv/bin/activate
pip install --quiet --upgrade -r requirements.txt
exec python -m bot.main
