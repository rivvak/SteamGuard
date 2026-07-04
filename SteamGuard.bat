@echo off
:: Launch SteamGuard as Administrator (required for firewall rules)
PowerShell -Command "Start-Process python -ArgumentList '\""%~dp0steamguard.py\""' -Verb RunAs -WorkingDirectory '%~dp0'"
