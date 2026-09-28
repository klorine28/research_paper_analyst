"""
Reading the on-disk artifacts the dashboard depends on.

The dashboard owns its own read models of the artifact schema so it never
imports a pipeline stage (ADR 0002). The models keep only the fields the
dashboard reads and ignore the rest, so a pipeline change that adds fields
does not break the front end.
"""

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict

ARTIFACTS_DIR = "artifacts"
MANIFEST_NAME = "corpus-manifest.json"


class ArtifactNotFoundError(FileNotFoundError):
  """Raised when a corpus directory has no CorpusManifest artifact to read."""


class _ReadModel(BaseModel):
  """A read model that tolerates artifact fields the dashboard does not use."""

  model_config = ConfigDict(extra="ignore")


class PaperRecord(_ReadModel):
  """One Paper, as the dashboard reads it from the CorpusManifest artifact."""

  citation_key: str
  doi: str
  title: str = ""
  year: int | None = None
  journal: str = ""


class UnmatchedEntryRecord(_ReadModel):
  """A paper-list entry that never became a Paper, and why it was left out."""

  citation_key: str = ""
  title: str = ""
  reason: str = ""


class ManifestArtifact(_ReadModel):
  """The CorpusManifest artifact, projected onto what the dashboard shows."""

  corpus_root: Path
  papers: list[PaperRecord] = []
  unmatched_entries: list[UnmatchedEntryRecord] = []
  orphan_pdfs: list[Path] = []


class CorpusChoice(BaseModel):
  """A corpus the dashboard can open: its directory and a label to show."""

  name: str
  root: Path


def _manifest_path(corpus_root: Path) -> Path:
  """Return where a corpus directory keeps its CorpusManifest artifact."""
  return corpus_root / ARTIFACTS_DIR / MANIFEST_NAME


def has_manifest(corpus_root: Path) -> bool:
  """Report whether a corpus directory has a CorpusManifest artifact to read."""
  return _manifest_path(corpus_root).is_file()


def load_manifest(corpus_root: Path) -> ManifestArtifact:
  """Read the CorpusManifest artifact for a corpus directory."""
  path = _manifest_path(corpus_root)
  if not path.is_file():
    raise ArtifactNotFoundError(
      f"No CorpusManifest at '{path}'. Run `ingest` on '{corpus_root}' first."
    )
  raw = json.loads(path.read_text(encoding="utf-8"))
  return ManifestArtifact.model_validate(raw)


def discover_corpora(corpora_root: Path) -> list[CorpusChoice]:
  """
  List the corpora under a directory that have a manifest to read.

  A corpus is any immediate subdirectory whose artifacts hold a CorpusManifest,
  plus the directory itself when it is a corpus. The list is sorted by name so
  the picker order is stable.
  """
  choices: list[CorpusChoice] = []
  if not corpora_root.is_dir():
    return choices
  if has_manifest(corpora_root):
    choices.append(CorpusChoice(name=corpora_root.name, root=corpora_root))
  for child in sorted(corpora_root.iterdir()):
    if child.is_dir() and has_manifest(child):
      choices.append(CorpusChoice(name=child.name, root=child))
  return choices
