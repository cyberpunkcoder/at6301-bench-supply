@echo off
setlocal
set "APP=AT6301 Bench Supply"
set "DST=%LOCALAPPDATA%\Programs\at6301-bench-supply"

echo Removing shortcuts ...
powershell -NoProfile -Command ^
  "foreach ($dir in @([Environment]::GetFolderPath('Programs'), [Environment]::GetFolderPath('Desktop'))) {" ^
  "  Remove-Item -LiteralPath (Join-Path $dir '%APP%.lnk') -ErrorAction SilentlyContinue }"

echo Removing %DST% ...
cd /d "%TEMP%"
rmdir /s /q "%DST%" 2>nul
if exist "%DST%" (
  echo Could not remove everything - app still running? Close it and re-run.
) else (
  echo Uninstalled.
)
pause
