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
CANDIDATE_GAPS_NAME = "candidate_gaps.json"


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


class EvidenceRecord(_ReadModel):
  """A verbatim Evidence passage, as the dashboard reads it from a gap card."""

  passage: str = ""
  section: str = ""


class GapEvidenceRecord(_ReadModel):
  """
  One Evidence link on a Candidate Gap card, over both gap-card shapes.

  Cell gaps carry a role and the Paper's original term; Unanswered Limitation
  gaps carry the source statement. Both carry a citation key and a verbatim
  passage, and the read model keeps every field either shape may set.
  """

  citation_key: str
  evidence: EvidenceRecord
  role: str = ""
  original_term: str = ""
  statement: str = ""


class CandidateGapRecord(_ReadModel):
  """One Candidate Gap card, projected onto what the Gap Cards view shows."""

  gap_id: str
  gap_type: str
  title: str
  explanation: str
  confidence: str
  confidence_reason: str
  corpus_paper_count: int
  cell_count: int | None = None
  source_citation_keys: list[str] = []
  evidence: list[GapEvidenceRecord] = []


class CandidateGapsArtifact(_ReadModel):
  """The CandidateGaps artifact, projected onto what the Gap Cards view shows."""

  corpus_root: Path
  corpus_paper_count: int = 0
  gaps: list[CandidateGapRecord] = []


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


def _candidate_gaps_path(corpus_root: Path) -> Path:
  """Return where a corpus directory keeps its CandidateGaps artifact."""
  return corpus_root / ARTIFACTS_DIR / CANDIDATE_GAPS_NAME


def has_candidate_gaps(corpus_root: Path) -> bool:
  """Report whether a corpus directory has a CandidateGaps artifact to read."""
  return _candidate_gaps_path(corpus_root).is_file()


def load_candidate_gaps(corpus_root: Path) -> CandidateGapsArtifact:
  """Read the CandidateGaps artifact the detect stage left on disk."""
  path = _candidate_gaps_path(corpus_root)
  if not path.is_file():
    raise ArtifactNotFoundError(
      f"No CandidateGaps at '{path}'. Run `detect` on '{corpus_root}' first."
    )
  raw = json.loads(path.read_text(encoding="utf-8"))
  return CandidateGapsArtifact.model_validate(raw)


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
