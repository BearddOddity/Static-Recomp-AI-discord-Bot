@echo off
rem Starts the bot automatically whenever you sign in to Windows, minimised.
rem Remove it again with:  schtasks /delete /tn "AI Discord Bot" /f
for %%I in ("%~dp0..") do set "DIR=%%~fI"
if not exist "%DIR%\.env" (
  echo Run start.bat once first, and fill in .env.
  pause
  exit /b 1
)
schtasks /create /tn "AI Discord Bot" /sc onlogon /rl limited /f ^
  /tr "cmd /c start \"AI Discord Bot\" /min \"%DIR%\start.bat\""
if errorlevel 1 (
  echo Couldn't create the task. Try running this file as administrator.
) else (
  echo Done. The bot will start each time you sign in. To start it now, run start.bat.
)
pause
