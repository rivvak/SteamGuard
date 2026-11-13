@echo off
cd /d "%~dp0"
start "usermode" x64\Release\usermode.exe
exit