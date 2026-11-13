@echo off
taskkill /F /IM usermode.exe
ping -n 3 127.0.0.1 >nul
cd /d "%~dp0"
start "usermode" x64\Release\usermode.exe
exit