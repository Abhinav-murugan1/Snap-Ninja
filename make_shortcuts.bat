@echo off
rem Adds Snap Ninja to the Start menu and the desktop, as an app with its own window and icon.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\make_shortcuts.ps1"
pause
