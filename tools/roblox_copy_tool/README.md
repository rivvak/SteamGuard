# Rivvak Roblox Copy Helper

Bundled local companion tool for the SteamGuard loader. It starts a localhost bridge on `127.0.0.1:6969`, accepts animation IDs from the included Roblox Studio Lua snippet, writes a local JSON export, and returns an ID mapping to Studio.

Security choices in this Python integration:

- Does **not** read Roblox Studio/browser registry cookies.
- Does **not** accept or store `.ROBLOSECURITY` cookies.
- Does **not** send credentials or exports to Discord/webhooks/third-party services.
- Intended only for experiences/assets the operator owns or is authorized to edit.

## Run from source

```bat
python roblox_copy_tool.py
```

## Build standalone exe

```bat
build_roblox_copy_tool.bat
```

The build writes `tools\roblox_copy_tool\RobloxCopyTool.exe`, which is the path the loader prefers in packaged builds.
