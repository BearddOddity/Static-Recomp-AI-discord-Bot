#!/usr/bin/env sh
# Mac / Linux: sets everything up on first run, then keeps the bot running.
# It restarts the bot after a self-update (exit code 3) or a crash, and stops for
# good on Ctrl+C (exit 0) or a setting that needs fixing (exit 2).
cd "$(dirname "$0")" || exit 1

if [ ! -f .env ]; then
  cp .env.example .env && chmod 600 .env
  echo "Created .env. Open it, fill in DISCORD_TOKEN and your AI key (see SETUP.md), then run ./start.sh again."
  exit 1
fi

PYTHON=python3
command -v $PYTHON >/dev/null 2>&1 || PYTHON=python
if [ ! -d .venv ]; then
  echo "First run: creating a Python environment..."
  $PYTHON -m venv .venv || exit 1
fi
. .venv/bin/activate

while true; do
  # Every start picks up requirements added by an update.
  pip install --quiet --upgrade -r requirements.txt
  python -m bot.main
  code=$?
  case $code in
    0) exit 0 ;;
    2) echo "Fix the setting above in .env, then start the bot again."; exit 2 ;;
    3) echo "Updated. Restarting..." ;;
    *) echo "The bot stopped (exit code $code). Restarting in 15 seconds..."; sleep 15 ;;
  esac
done
