@echo off
taskkill /F /IM usermode.exe
ping -n 3 127.0.0.1 >nul
copy /Y "C:\Users\mason\Downloads\yanny\usermode\x64\RelGprobe\usermode.exe" "C:\Users\mason\Downloads\yanny\usermode\x64\Release\usermode.exe"
cd /d C:\Users\mason\Downloads\yanny\usermode
start "usermode" x64\Release\usermode.exe
exit