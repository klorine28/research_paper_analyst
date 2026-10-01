"""
Loading configuration from the project-root `.env` at every entry point.

Settings are read once from the environment (`CODING_STANDARDS.md` >
Configuration). `just` recipes load `.env` via `set dotenv-load`, but a bare
`uv run`, the installed console script, and `streamlit run` do not, so the one
place a user keeps their keys would otherwise work only under `just`. Calling
`load_env()` at each entry point (CLI `main`, the dashboard app) makes a single
`.env` the only thing a user edits to set or change a key, however they launch.

A real environment variable always wins over the file (`override=False`), so an
exported key or a CI secret is never shadowed by a stale `.env`.
"""

from dotenv import load_dotenv


def load_env() -> None:
  """Populate the environment from the project-root `.env`, if one exists."""
  load_dotenv(override=False)
