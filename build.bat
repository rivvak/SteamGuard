@echo off
:: SteamGuard — Nuitka build script
:: Compiles Python → native x86-64 exe (no .pyc files)
:: Run from the steamapp\ directory with your venv active.
::
:: Prerequisites:
::   pip install nuitka ordered-set zstandard
::   (Nuitka requires a C compiler — install Visual Studio Build Tools or MinGW)
::
:: Output: steamapp\dist\SteamGuard.exe

echo [SteamGuard Build] Starting Nuitka compilation...
echo.

set DIST=dist
if not exist %DIST% mkdir %DIST%

python -m nuitka ^
    --onefile ^
    --windows-console-mode=disable ^
    --output-filename=SteamGuard.exe ^
    --output-dir=%DIST% ^
    --include-package=auth ^
    --include-data-dir=auth=auth ^
    --windows-icon-from-ico=icon.ico ^
    --company-name="SteamGuard" ^
    --product-name="SteamGuard" ^
    --file-description="SteamGuard - Family Sharing Bypass" ^
    --copyright="SteamGuard" ^
    --assume-yes-for-downloads ^
    --remove-output ^
    steamguard.py

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
