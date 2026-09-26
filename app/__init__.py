"""Copy Editor: a workshop tool for prose. FastAPI + HTMX + SQLite."""

import tomllib
from pathlib import Path

# pyproject.toml is the one place the version is written; scripts/release.py bumps it.
with open(Path(__file__).resolve().parent.parent / "pyproject.toml", "rb") as _f:
    __version__: str = tomllib.load(_f)["project"]["version"]
