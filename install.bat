@echo off
setlocal
REM AT6301 Bench Supply - installer
REM Creates a private venv with pyserial + matplotlib, a launcher, and Start Menu / Desktop shortcuts.

set "APP=AT6301 Bench Supply"
set "SRC=%~dp0"
set "DST=%LOCALAPPDATA%\Programs\at6301-bench-supply"

where uv >nul 2>&1
if errorlevel 1 (
  echo uv not found. Install it first:  powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
  pause & exit /b 1
)

echo Installing to %DST% ...
if not exist "%DST%" mkdir "%DST%"
copy /y "%SRC%at6301_gui.py" "%DST%\" >nul
copy /y "%SRC%at6301_log.py" "%DST%\" >nul
copy /y "%SRC%uninstall.bat" "%DST%\" >nul

echo Creating virtual environment ...
uv venv "%DST%\.venv" --python 3.12 -q || (echo venv failed & pause & exit /b 1)
uv pip install --python "%DST%\.venv\Scripts\python.exe" -q pyserial matplotlib || (echo pip failed & pause & exit /b 1)

> "%DST%\%APP%.bat" (
  echo @echo off
  echo start "" "%DST%\.venv\Scripts\pythonw.exe" "%DST%\at6301_gui.py"
)

echo Creating shortcuts ...
powershell -NoProfile -Command ^
  "$ws = New-Object -ComObject WScript.Shell;" ^
  "foreach ($dir in @([Environment]::GetFolderPath('Programs'), [Environment]::GetFolderPath('Desktop'))) {" ^
  "  $s = $ws.CreateShortcut((Join-Path $dir '%APP%.lnk'));" ^
  "  $s.TargetPath = '%DST%\.venv\Scripts\pythonw.exe';" ^
  "  $s.Arguments = '\"%DST%\at6301_gui.py\"';" ^
  "  $s.WorkingDirectory = '%DST%';" ^
  "  $s.IconLocation = '%%SystemRoot%%\System32\shell32.dll,165';" ^
  "  $s.Description = 'Abestop AT6301 control, live plot and CSV logging';" ^
  "  $s.Save() }"

echo.
echo Installed. Launch from Start Menu / Desktop: "%APP%"
echo Uninstall: "%DST%\uninstall.bat"
pause
