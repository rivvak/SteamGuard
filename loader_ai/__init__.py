# This file makes loader_ai a Python package. Named `loader_ai` (not
# `loader.ai`) because a top-level `loader/` package would be shadowed by the
# existing `loader.py` entry-point module — Python's FileFinder resolves the
# `loader.py` file before the `loader/` package directory, so `loader` ends up
# bound to the module, not the package, and `from loader.ai import ...` fails
# with "'loader' is not a package".
