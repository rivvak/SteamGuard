@echo off
setlocal
cd /d "%~dp0\..\.."
python -m pip install --upgrade nuitka ordered-set zstandard
python -m nuitka ^
  --onefile ^
  --windows-console-mode=force ^
  --output-filename=RobloxCopyTool.exe ^
  --output-dir=tools/roblox_copy_tool ^
  --include-data-files=tools/roblox_copy_tool/LuaScript.lua=LuaScript.lua ^
  --assume-yes-for-downloads ^
  tools/roblox_copy_tool/roblox_copy_tool.py
if %ERRORLEVEL% EQU 0 (
  echo Built tools\roblox_copy_tool\RobloxCopyTool.exe
) else (
  echo Build failed
)
endlocal
