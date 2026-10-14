@echo off
rem Double-click to start the SMP controller (wake-on-join, Discord bot, panel). Keep this window open.
title SMP Controller - keep this window open
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run-controller.ps1"
echo.
echo The controller has stopped. Press any key to close this window.
pause >nul
