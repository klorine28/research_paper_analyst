"""
Creating a Corpus from the dashboard, and its scope statement.

The scope statement records what a Corpus was built from: the research question,
the field, and what was searched. The dashboard shows it on every page so nobody
mistakes the Corpus for the whole literature (CODING_STANDARDS.md > Show the
denominator).

ADR 0007 lets the dashboard place a Corpus's *inputs* on disk; the scope record
is one of them, kept at the Corpus root as `scope.json`. Creating a Corpus makes
its empty directory layout (`docs/corpus-layout.md`) and writes that record, and
nothing else: the manifest and every other artifact still come only from
pipeline commands. Like the other dashboard seams this is plain functions with
no Streamlit dependency, and it imports no pipeline stage (ADR 0002).
"""

import logging
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel

from research_gap_dashboard.dashboard.artifacts import SCOPE_NAME, CorpusChoice

logger = logging.getLogger(__name__)

# Restated from `corpus_layout` rather than imported: the dashboard imports no
# pipeline module.
CORPUS_DIRS = ("papers", "paper-data", "artifacts", "judgments")


class CorpusCreationError(ValueError):
  """A Corpus or its scope could not be recorded, with a reason to show."""


class CorpusScope(BaseModel):
  """What a Corpus was built from: its question, field, and what was searched."""

  question: str
  field: str
  searched: str
  recorded_at: datetime


def scope_path(corpus_root: Path) -> Path:
  """Return where a corpus directory keeps its scope statement."""
  return corpus_root / SCOPE_NAME


def has_scope(corpus_root: Path) -> bool:
  """Whether a corpus directory has a scope statement on disk."""
  return scope_path(corpus_root).is_file()


def load_scope(corpus_root: Path) -> CorpusScope | None:
  """Read a Corpus's scope statement, or None when it has never been stated."""
  path = scope_path(corpus_root)
  if not path.is_file():
    return None
  return CorpusScope.model_validate_json(path.read_text(encoding="utf-8"))


def _build_scope(question: str, field: str, searched: str) -> CorpusScope:
  """Validate the three parts of a scope; a blank part would state nothing."""
  parts = {"question": question, "field": field, "searched": searched}
  trimmed = {key: value.strip() for key, value in parts.items()}
  blank = [key for key, value in trimmed.items() if not value]
  if blank:
    raise CorpusCreationError(
      f"The scope needs a {', '.join(blank)}: every part must be filled in."
    )
  return CorpusScope(**trimmed, recorded_at=datetime.now(timezone.utc))


def save_scope(
  corpus_root: Path, *, question: str, field: str, searched: str
) -> CorpusScope:
  """Record (or replace) the scope statement of an existing Corpus."""
  scope = _build_scope(question, field, searched)
  scope_path(corpus_root).write_text(scope.model_dump_json(indent=2), encoding="utf-8")
  return scope


def _validated_name(name: str) -> str:
  """
  Return the trimmed Corpus name, refusing anything but one plain path segment.

  The name becomes a directory under the corpora folder, so a separator, a
  parent reference, or a leading dot could place it elsewhere or hide it.
  """
  trimmed = name.strip()
  if not trimmed:
    raise CorpusCreationError("The Corpus needs a name.")
  if any(sep in trimmed for sep in ("/", "\\")) or trimmed.startswith("."):
    raise CorpusCreationError(
      f"'{trimmed}' cannot be a Corpus name: use letters, digits, '-' or '_', "
      "with no slashes and no leading dot."
    )
  return trimmed


def create_corpus(
  corpora_root: Path, *, name: str, question: str, field: str, searched: str
) -> CorpusChoice:
  """
  Create a new, empty Corpus directory under `corpora_root` with its scope.

  Everything is validated before anything is written, and an existing directory
  is never reused, so a failed creation leaves the corpora folder unchanged.
  """
  corpus_name = _validated_name(name)
  scope = _build_scope(question, field, searched)
  root = corpora_root / corpus_name
  if root.exists():
    raise CorpusCreationError(
      f"A Corpus or folder named '{corpus_name}' already exists; choose another name."
    )
  for directory in CORPUS_DIRS:
    (root / directory).mkdir(parents=True)
  scope_path(root).write_text(scope.model_dump_json(indent=2), encoding="utf-8")
  logger.info("Created Corpus %s", root)
  return CorpusChoice(name=corpus_name, root=root)
