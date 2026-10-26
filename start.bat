@echo off
rem Windows: sets everything up on first run, then keeps the bot running.
rem It restarts the bot after a self-update (exit code 3) or a crash, and stops for
rem good when closed on purpose (exit 0) or a setting needs fixing (exit 2).
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

:run
rem Every start picks up requirements added by an update.
pip install --quiet --upgrade -r requirements.txt
python -m bot.main
set CODE=%ERRORLEVEL%
if "%CODE%"=="0" goto done
if "%CODE%"=="2" (
  echo Fix the setting above in .env, then start the bot again.
  goto done
)
if "%CODE%"=="3" (
  echo Updated. Restarting...
  goto run
)
echo The bot stopped ^(exit code %CODE%^). Restarting in 15 seconds...
timeout /t 15 /nobreak >nul
goto run

:done
pause
