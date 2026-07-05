@echo off
cd /d "%~dp0"
if exist RobloxCopyTool.exe (
  start "Rivvak Roblox Copy Helper" RobloxCopyTool.exe
) else (
  python roblox_copy_tool.py
)
