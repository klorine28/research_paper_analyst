set dotenv-load

set export := true

# Directories to lint: uv workspace members if any, otherwise src/ and tests/.
# (The project name is not a directory, so it must not be used here.)
SRC_DIRS := `uv run python3 -c "
import tomllib
with open('pyproject.toml', 'rb') as f:
    data = tomllib.load(f)
workspace = data.get('tool', {}).get('uv', {}).get('workspace', {}).get('members', [])
dirs = workspace if workspace else ['src', 'tests']
print(' '.join(dirs))
"`


# -- Local environment -- #
#
# Initialize and sync the environment
init:
  uv venv
  uv sync

# Check the code format with Ruff
format-check:
  uv run ruff format --check .

# Format the code with Ruff
format:
  uv run ruff format .

# Lint the codebase
lint:
  uv run ruff check .
  uv run --with pylint pylint --recursive=y {{ SRC_DIRS }}

# Type-check the codebase
type-check:
  uv run pyright
  uv run ty check

# Lint the codebase with pylint warnings and convetions suppressed
lint-errors:
  uv run ruff check .
  uv run --with pylint pylint --disable=W,C,D --recursive=y {{ SRC_DIRS }}
  uv run pyright
  uv run ty check

# Run tests with pytest
test:
  uv run pytest -v

# Sync environment
sync:
  uv sync

# Upgrade and Sync environment
upgrade:
  uv lock --upgrade
  uv sync

# -- Dashboard -- #
#
# Streamlit shell that reads the on-disk artifacts (ADR 0002). Set
# RESEARCH_GAP_CORPORA_DIR to the folder holding your ingested corpus
# directories (defaults to ./corpora).
dashboard:
  uv run streamlit run src/research_gap_dashboard/dashboard/app.py
