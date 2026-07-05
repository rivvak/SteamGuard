# Rivvak Roblox Copier

Local-only Python port of the Roblox animation copier flow. It starts an HTTP bridge on `127.0.0.1:6969`, receives animation IDs from `LuaScript.lua`, downloads each animation from Roblox with your cookie, re-uploads it to your account or group, and returns an `{oldId: newId}` mapping to Studio.

**Disclaimer:** For use only with games you own or have explicit permission to copy. Your `.ROBLOSECURITY` cookie is used locally and never transmitted anywhere except roblox.com.

## Run from source

```bash
python -m pip install PyQt6 requests
python -m roblox_copier
```

## Usage

1. Paste your `.ROBLOSECURITY` cookie into the app. Optionally enter a Roblox group ID.
2. Click **Start server**.
3. In Roblox Studio, run `roblox_copier/LuaScript.lua` in the command bar/plugin context for an experience you own or are authorized to edit.

The Studio script posts animation IDs to `127.0.0.1:6969`, polls until the copy flow finishes, then rewrites matching `AnimationId` references to the new uploaded IDs. The server returns `null` while work is in progress and returns the final mapping once complete.

## Security changes from the original Node.js source

- Removed KeyAuth/license checks.
- Removed Discord webhook cookie transmission.
- Removed Windows registry cookie discovery.
- No telemetry, webhooks, third-party auth, or non-Roblox network destinations.

## Build standalone executable

From the repository root:

```bash
python build_roblox_copier.py
```

The build writes `dist/roblox_copier.exe`.
