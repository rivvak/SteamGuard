# Contributing

Thanks for contributing to SteamGuard. Keep changes focused and easy to review.

## Workflow

1. **Branch** off `main`: `git checkout -b feat/short-description`.
2. Make your change. Keep commits small and scoped.
3. **Lint before committing** (see below).
4. Push and **open a pull request** against `main` with a clear summary and
   testing notes. Do not commit directly to `main`.

## Before you commit

- Install pre-commit hooks once: `pip install pre-commit && pre-commit install`.
  This runs `gitleaks` (secret scanning), trailing-whitespace, end-of-file, and
  YAML checks automatically.
- Ensure Python files compile:
  `python -m py_compile loader.py steamguard_qt.py server/main.py server/bot.py`.
- Never commit secrets — see [SECURITY.md](SECURITY.md). Use `.env` (gitignored).

## Style

- Match the existing style of the file you are editing.
- Prefer editing existing files over adding new ones.
- Keep UI colors/constants consistent with the palette in `loader.py`.

## Pull requests

- Describe what changed and why, plus how you tested it.
- One logical change per PR where practical.
