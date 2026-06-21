@echo off
echo Installing SteamGuard dependencies...
echo.
echo [1/2] Core (required)...
pip install psutil pywin32

echo.
echo [2/2] Optional extras...
echo   wmi     - faster network adapter detection (~1s vs 2s)
echo   pystray - minimize to system tray
echo   Pillow  - tray icon image (needed with pystray)
pip install wmi pystray Pillow

echo.
echo All done! Run SteamGuard.bat to start.
pause
