"""
The pipeline status read model: whether each stage is done, not run, or stale.

Status comes only from which files exist and when they were written (ADR 0007):
a stage is *done* when its artifact exists, *not run* when it doesn't, and
*stale* when one of its inputs is newer than that artifact. The read model never
blocks anything; a re-run is always offered, because the pipeline is cache-keyed
by its inputs and a needless re-run is cheap.

Each stage's artifact and inputs are listed here by path pattern, restating the
pipeline's on-disk names rather than importing the stages (ADR 0002). Only the
stages a researcher runs as a step are listed; `diagnose`, `baseline` and the
seam commands are reports or corrections, not steps with an artifact to go stale.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel

from research_gap_dashboard.dashboard.artifacts import (
  ARTIFACTS_DIR,
  CANDIDATE_GAPS_NAME,
  EXPLANATION_SUFFIX,
  EXTRACTIONS_NAME,
  MANIFEST_NAME,
  NORMALIZED_FACTS_NAME,
  PAPER_DATA_DIR,
  PARSE_REPORT_NAME,
  RETRIEVAL_GAPS_NAME,
)

_MANIFEST = f"{ARTIFACTS_DIR}/{MANIFEST_NAME}"
_PARSE_REPORT = f"{ARTIFACTS_DIR}/{PARSE_REPORT_NAME}"
_PARSED = f"{PAPER_DATA_DIR}/*.parsed.json"
_EXTRACTIONS = f"{ARTIFACTS_DIR}/{EXTRACTIONS_NAME}"
_NORMALIZED = f"{ARTIFACTS_DIR}/{NORMALIZED_FACTS_NAME}"


class StageState(StrEnum):
  """Where a stage stands, judged from its artifact and inputs on disk."""

  DONE = "done"
  NOT_RUN = "not_run"
  STALE = "stale"


@dataclass(frozen=True)
class _StageSpec:
  """A stage's artifact and the inputs that make it stale, as glob patterns."""

  stage: str
  artifact: str
  inputs: tuple[str, ...]


# Pipeline order. A pattern matching several files (explain writes one file per
# Paper) is judged by its oldest file, so any input newer than any part of the
# artifact makes the stage stale.
STAGES: tuple[_StageSpec, ...] = (
  _StageSpec("ingest", _MANIFEST, ("*.bib", "dois.txt", "papers/*.pdf")),
  _StageSpec("parse", _PARSE_REPORT, (_MANIFEST,)),
  _StageSpec("extract", _EXTRACTIONS, (_PARSE_REPORT, _PARSED)),
  _StageSpec("explain", f"{PAPER_DATA_DIR}/*{EXPLANATION_SUFFIX}", (_PARSED,)),
  _StageSpec("aggregate", _NORMALIZED, (_EXTRACTIONS,)),
  _StageSpec(
    "detect", f"{ARTIFACTS_DIR}/{CANDIDATE_GAPS_NAME}", (_NORMALIZED, _EXTRACTIONS)
  ),
  _StageSpec("retrieve", f"{ARTIFACTS_DIR}/{RETRIEVAL_GAPS_NAME}", (_MANIFEST,)),
)


class StageStatus(BaseModel):
  """One stage's row on the status panel."""

  stage: str
  state: StageState
  artifact: str
  last_run: datetime | None = None
  newer_inputs: list[str] = []


def build_pipeline_status(corpus_root: Path) -> list[StageStatus]:
  """Return every stage's status for a Corpus, in pipeline order."""
  return [_status(corpus_root, spec) for spec in STAGES]


def _status(corpus_root: Path, spec: _StageSpec) -> StageStatus:
  artifacts = _matches(corpus_root, spec.artifact)
  if not artifacts:
    return StageStatus(
      stage=spec.stage, state=StageState.NOT_RUN, artifact=spec.artifact
    )
  written = min(path.stat().st_mtime for path in artifacts)
  newer = sorted(
    path.relative_to(corpus_root).as_posix()
    for pattern in spec.inputs
    for path in _matches(corpus_root, pattern)
    if path.stat().st_mtime > written
  )
  return StageStatus(
    stage=spec.stage,
    state=StageState.STALE if newer else StageState.DONE,
    artifact=spec.artifact,
    last_run=datetime.fromtimestamp(written),
    newer_inputs=newer,
  )


def _matches(corpus_root: Path, pattern: str) -> list[Path]:
  return [path for path in corpus_root.glob(pattern) if path.is_file()]
