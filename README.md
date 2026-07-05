# SteamGuard

Desktop loader and license system for the SteamGuard tool suite: a PyQt5 loader,
a FastAPI license backend on Cloud Run, and a Discord bot for key delivery.

## Quick start

```bash
# Client / loader
pip install -r requirements.txt
python loader.py

# Server (license backend)
pip install -r server/requirements.txt
uvicorn server.main:app --reload
```

Copy `.env.example` to `.env` and fill in real values before running the server
or bot. See `docs/AGENTS.md` for full deployment/environment notes.

## Directory structure

```
loader.py             PyQt5 loader UI (login, product cards, downloads)
steamguard_qt.py      SteamGuard tool (downloaded on demand by the loader)
steamguard.py         Core SteamGuard logic
steam_features.py     Steam game/app detection helpers
requirements.txt      Loader/client dependencies
requirements_client.txt  Extra client dependencies
assets/               SVG/PNG icons used by the UI
auth/                 Client auth (HWID, session cache, cert pinning)
server/               FastAPI license backend + Discord bot + Dockerfile
dashboard/            Static admin dashboard
tools/                Bundled companion tools (Roblox copy tool, etc.)
scripts/              Standalone build/test/diagnostic scripts
docs/                 Contributing, security, and agent/deployment notes
```

## Documentation

See [`docs/`](docs/) — including
[`docs/CONTRIBUTING.md`](docs/CONTRIBUTING.md),
[`docs/SECURITY.md`](docs/SECURITY.md), and
[`docs/AGENTS.md`](docs/AGENTS.md).
