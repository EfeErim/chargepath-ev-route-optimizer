@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\open_chargepath.ps1"
if errorlevel 1 pause
endlocal
