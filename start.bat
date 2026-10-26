@echo off
rem Windows: sets everything up on first run, then starts the bot.
cd /d "%~dp0"

if not exist .env (
  copy .env.example .env >nul
  echo Created .env. Open it in Notepad, fill in DISCORD_TOKEN and your AI key ^(see SETUP.md^), then run start.bat again.
  pause
  exit /b 1
)

if not exist .venv (
  echo First run: creating a Python environment...
  py -3 -m venv .venv || python -m venv .venv
)
call .venv\Scripts\activate.bat
pip install --quiet --upgrade -r requirements.txt
python -m bot.main
pause
