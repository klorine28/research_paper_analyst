"""
Suggested Papers in the dashboard: the one Grow path for both routes.

A Retrieval Gap candidate and a saturation-check hit are out-of-Corpus works
the researcher may add to the Corpus (ADR 0009 §11-12). Both go through the
same pipeline commands, run by `pipeline_runner` (ADR 0007): `add-suggested`
fetches the open-access PDF or takes an upload, and `mark-unavailable` records
why a paper can't be had. The dashboard never calls a scholarly API itself
(ADR 0009 §5) and never writes the paper list or the record of suggestions;
it only reads `suggested_papers.json` back.

A work suggested by both routes is the same suggestion: it is matched by any
shared DOI, PMID or OpenAlex id, so the double signal is shown in both places
and a second "Add" is refused by the pipeline. After an add the stages are
re-run through the runner; each is cache-keyed, so the re-run only does new
work for the new Paper.

Plain functions, no Streamlit; the layout is in `views.suggestions`.
"""

import logging
import re
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from research_gap_dashboard.dashboard import pipeline_runner
from research_gap_dashboard.dashboard.artifacts import (
  RetrievalGapsArtifact,
  SaturationChecksArtifact,
)
from research_gap_dashboard.dashboard.pipeline_runner import RunOutcome, RunResult

logger = logging.getLogger(__name__)

# Restated from the pipeline's Grow path (`grow.py`), which the dashboard does
# not import (ADR 0002).
SUGGESTED_PAPERS_NAME = "suggested_papers.json"
ADD_COMMAND = "add-suggested"
UNAVAILABLE_COMMAND = "mark-unavailable"
# The incremental re-run after a Paper is added: every stage the new Paper
# flows through to reach the gap cards.
RERUN_STAGES = ("ingest", "parse", "extract", "aggregate", "detect")

Route = Literal["retrieval", "saturation"]


class _ReadModel(BaseModel):
  model_config = ConfigDict(extra="ignore")


class SuggestedPaperRecord(_ReadModel):
  """One suggested work as the pipeline recorded it, and its status."""

  doi: str = ""
  pmid: str = ""
  openalex_id: str = ""
  title: str = ""
  year: int | None = None
  routes: list[str] = []
  status: str
  reason: str = ""
  citation_key: str = ""
  pdf_source: str = ""
  updated_at: datetime

  def ids(self) -> set[str]:
    """Return comparable ids of the recorded work."""
    return work_ids(self.doi, self.pmid, self.openalex_id)


class SuggestedPapersRecord(_ReadModel):
  """The Corpus's record of suggested Papers."""

  papers: list[SuggestedPaperRecord] = []

  def find(self, ids: set[str]) -> SuggestedPaperRecord | None:
    """Return the record of the work with any of these ids, if there is one."""
    return next((paper for paper in self.papers if paper.ids() & ids), None)

  @property
  def unavailable(self) -> list[SuggestedPaperRecord]:
    """The works marked unavailable, disclosed in the Corpus's scope."""
    return [paper for paper in self.papers if paper.status == "unavailable"]


class Suggestion(BaseModel):
  """One out-of-Corpus work offered for the Corpus, from either route."""

  route: Route
  doi: str = ""
  pmid: str = ""
  openalex_id: str = ""
  title: str = ""
  year: int | None = None

  def ids(self) -> set[str]:
    """Return comparable ids, matched against the other route and the record."""
    return work_ids(self.doi, self.pmid, self.openalex_id)

  @property
  def key(self) -> str:
    """A stable, widget-safe key for this work."""
    return re.sub(r"[^a-z0-9]+", "-", min(self.ids(), default=self.title).lower())


class AddResult(BaseModel):
  """One `add-suggested` run, and the outcome it printed when it got that far."""

  run: RunResult
  status: str = ""
  message: str = ""


def work_ids(doi: str, pmid: str, openalex_id: str) -> set[str]:
  """Return comparable `doi:`, `pmid:` and `openalex:` ids for one work."""
  ids: set[str] = set()
  if doi:
    ids.add("doi:" + re.sub(r"^https?://(dx\.)?doi\.org/", "", doi.strip().lower()))
  if pmid:
    ids.add(f"pmid:{pmid.strip()}")
  if openalex_id:
    ids.add("openalex:" + openalex_id.strip().rsplit("/", 1)[-1].upper())
  return ids


def load_suggested_papers(corpus_root: Path) -> SuggestedPapersRecord:
  """Read the record of suggested Papers, or an empty one."""
  path = corpus_root / SUGGESTED_PAPERS_NAME
  if not path.is_file():
    return SuggestedPapersRecord()
  try:
    return SuggestedPapersRecord.model_validate_json(path.read_text(encoding="utf-8"))
  except ValidationError:
    logger.error("Could not read %s", path)
    return SuggestedPapersRecord()


def _identity_args(suggestion: Suggestion) -> list[str]:
  args = ["--route", suggestion.route]
  for flag, value in (
    ("--doi", suggestion.doi),
    ("--pmid", suggestion.pmid),
    ("--openalex-id", suggestion.openalex_id),
    ("--title", suggestion.title),
  ):
    if value:
      args += [flag, value]
  if suggestion.year is not None:
    args += ["--year", str(suggestion.year)]
  return args


def add_suggestion(
  corpus_root: Path, suggestion: Suggestion, upload: bytes | None = None
) -> AddResult:
  """
  Add a suggested work through the pipeline: fetch its PDF, or use the upload.

  The upload is handed over as a temporary file, so nothing lands in `papers/`
  unless the pipeline pairs it with a paper-list entry.
  """
  args = _identity_args(suggestion)
  if upload is None:
    run = pipeline_runner.run_stage(corpus_root, ADD_COMMAND, args=args)
  else:
    with tempfile.TemporaryDirectory() as scratch:
      path = Path(scratch) / "upload.pdf"
      path.write_bytes(upload)
      run = pipeline_runner.run_stage(
        corpus_root, ADD_COMMAND, args=[*args, "--upload", str(path)]
      )
  return _add_result(run)


class _Printed(_ReadModel):
  status: str
  message: str = ""


def _add_result(run: RunResult) -> AddResult:
  """Read the outcome `add-suggested` printed, falling back on why it failed."""
  if run.stdout:
    try:
      printed = _Printed.model_validate_json(run.stdout)
    except ValidationError:
      logger.error("add-suggested printed no readable outcome")
    else:
      return AddResult(run=run, status=printed.status, message=printed.message)
  reason = pipeline_runner.failure_reason(run) or run.outcome.value
  return AddResult(run=run, message=reason)


def mark_suggestion_unavailable(
  corpus_root: Path, suggestion: Suggestion, reason: str
) -> RunResult:
  """Record through the pipeline that a suggested work can't be obtained."""
  return pipeline_runner.run_stage(
    corpus_root,
    UNAVAILABLE_COMMAND,
    args=[*_identity_args(suggestion), "--reason", reason],
  )


def was_added(result: AddResult) -> bool:
  """Whether the add placed a new Paper, so the stages should re-run."""
  return result.run.outcome == RunOutcome.SUCCEEDED and result.status == "added"


def retrieval_overlaps(artifact: RetrievalGapsArtifact | None) -> dict[str, int]:
  """Map each Retrieval Gap candidate's ids to how many Corpus Papers cite it."""
  if artifact is None:
    return {}
  overlaps: dict[str, int] = {}
  for candidate in artifact.candidates:
    for work_id in work_ids(candidate.doi, "", candidate.openalex_id):
      overlaps[work_id] = candidate.citation_overlap
  return overlaps


def saturation_hit_gaps(checks: SaturationChecksArtifact) -> dict[str, list[str]]:
  """Map each hit's ids to the gaps whose latest check returned it."""
  latest = {check.gap_id: check for check in checks.checks}
  gaps: dict[str, list[str]] = {}
  for gap_id, check in latest.items():
    for hit in check.hits:
      for work_id in work_ids(hit.doi, hit.pmid, hit.openalex_id):
        listed = gaps.setdefault(work_id, [])
        if gap_id not in listed:
          listed.append(gap_id)
  return gaps


def lookup_overlap(ids: set[str], overlaps: dict[str, int]) -> int:
  """How many Corpus Papers cite this work, if it is a Retrieval Gap candidate."""
  return max((overlaps[work_id] for work_id in ids if work_id in overlaps), default=0)


def lookup_gaps(ids: set[str], gaps: dict[str, list[str]]) -> list[str]:
  """Return the gaps whose latest saturation check returned this work."""
  found: list[str] = []
  for work_id in sorted(ids):
    for gap_id in gaps.get(work_id, []):
      if gap_id not in found:
        found.append(gap_id)
  return found


class SuggestionState(BaseModel):
  """Where a suggested work stands: not acted on, pending, added or unavailable."""

  status: str = ""
  reason: str = ""
  updated_on: str = ""
  citation_key: str = ""

  @property
  def unavailable(self) -> bool:
    """Marked unavailable: no longer offered, kept in a collapsed list."""
    return self.status == "unavailable"


def suggestion_state(ledger: SuggestedPapersRecord, ids: set[str]) -> SuggestionState:
  """Return the recorded state of the work with these ids."""
  record = ledger.find(ids)
  if record is None:
    return SuggestionState()
  return SuggestionState(
    status=record.status,
    reason=record.reason,
    updated_on=record.updated_at.strftime("%Y-%m-%d"),
    citation_key=record.citation_key,
  )
