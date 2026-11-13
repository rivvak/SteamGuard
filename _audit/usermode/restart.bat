@echo off
taskkill /F /PID 28104
:waitlink
if not exist "C:\Users\mason\Downloads\yanny\usermode\x64\Release\usermode.exe.new" (
  ping -n 3 127.0.0.1 >nul
  goto waitlink
)
ping -n 3 127.0.0.1 >nul
move /Y "C:\Users\mason\Downloads\yanny\usermode\x64\Release\usermode.exe.new" "C:\Users\mason\Downloads\yanny\usermode\x64\Release\usermode.exe"
cd /d C:\Users\mason\Downloads\yanny\usermode
start "" /B x64\Release\usermode.exe > test_out.log 2>&1
exit