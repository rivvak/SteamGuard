@echo off
:: SteamGuard — Upgraded Nuitka build script

:: Compiles Python → native C++ → x86-64 binary with admin auto-elevation and hardened protection.

cd /d "%~dp0"

echo [SteamGuard Build] Starting native hardened compilation...
echo.

set DIST=dist
if not exist %DIST% mkdir %DIST%

python -m nuitka ^
    --onefile ^
    --windows-console-mode=disable ^
    --windows-uac-admin ^
    --windows-icon-from-ico=icon.ico ^
    --enable-plugin=pyside6 ^
    --include-qt-plugins=sensible,styles ^
    --include-package-data=qdarktheme ^
    --output-filename=SteamGuard.exe ^
    --output-dir=%DIST% ^
    --include-package=auth ^
    --include-data-dir=auth=auth ^
    --include-data-files=icon.png=icon.png ^
    --company-name="SteamGuard" ^
    --product-name="SteamGuard" ^
    --file-description="SteamGuard" ^
    --copyright="SteamGuard" ^
    --file-version=1.0.0.0 ^
    --product-version=1.0.0.0 ^
    --assume-yes-for-downloads ^
    --remove-output ^
    --no-pyi-file ^
    --experimental=disable-all-tracebacks ^
    steamguard_qt.py

if %ERRORLEVEL% EQU 0 (
    echo.
    echo [SteamGuard Build] SUCCESS: dist\SteamGuard.exe
    echo.
    echo If Cloud Run rotated the certificate:
    echo   1. Run: python auth\get_cert_hash.py https://YOUR-CLOUD-RUN-URL
    echo   2. Add the new hash to _SERVER_CERT_HASHES in auth\client.py
    echo   3. Rebuild
) else (
    echo.
    echo [SteamGuard Build] FAILED — check errors above
)

echo.
echo Building Loader...
python -m nuitka ^
    --onefile ^
    --windows-console-mode=disable ^
    --windows-uac-admin ^
    --windows-icon-from-ico=icon.ico ^
    --enable-plugin=pyqt5 ^
    --include-package=PyQt5 ^
    --assume-yes-for-downloads ^
    --output-filename=Loader.exe ^
    --output-dir=%DIST% ^
    --include-package=auth ^
    loader.py
echo Loader.exe built successfully!

