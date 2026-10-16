"""
Persisting the researcher's accept/reject judgments on Candidate Gaps.

A judgment is the human verdict on a Candidate Gap: a Corpus is analysed once
but reviewed over months, so the accept/reject state must outlive the Streamlit
session and any restart. This module is the read/write seam for that state; it
is plain functions with no Streamlit dependency, so the layout layer stays thin
and the persistence can be tested directly.

Judgments live in one JSON file under the Corpus's `judgments/` directory. The
dashboard owns this schema the way it owns its artifact read models (ADR 0002):
it never imports a pipeline stage. A gap that has never been judged simply has
no entry, so removing a judgment is undoing it, not asserting the gap is
undecided.

How a verdict finds its gap again after a re-run (ADR 0010 §5):

- A Knowledge or Coverage Gap is keyed by its `gap_id`, which names its cell
  and is stable across runs.
- An Unanswered Limitation is keyed by its **verbatim source statements** (each
  source Paper's citation key and quoted passage), because its `gap_id` names
  an LLM-made group that a re-run may renumber, split or merge. A verdict
  follows any card sharing one of its statements, so regrouping never orphans
  it. When several verdicts reach one card, the latest wins.

Verdicts saved before that keying carry only a `limitation:<group>` id. Reading
the log migrates them once: each is matched against the CandidateGaps artifact
that was on screen when it was made (the newest one written before the verdict,
current or previous run), and gains that card's statements. A verdict no
artifact dates stays as it was and is reported as orphaned, never guessed onto
whatever group now has its old id.
"""

from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from research_gap_dashboard.dashboard.artifacts import (
  CandidateGapRecord,
  CandidateGapsArtifact,
  candidate_gaps_versions,
)

JUDGMENTS_DIR = "judgments"
JUDGMENTS_NAME = "gap_judgments.json"

LIMITATION_GAP_TYPE = "unanswered_limitation"
LIMITATION_ID_PREFIX = "limitation:"

Decision = Literal["accepted", "rejected"]


class SourceStatement(BaseModel, frozen=True):
  """One verbatim source statement behind an Unanswered Limitation."""

  citation_key: str
  passage: str


class GapKey(BaseModel, frozen=True):
  """
  What identifies a Candidate Gap for its verdict across re-runs.

  `source_statements` is set for Unanswered Limitations only; a cell gap is
  identified by its `gap_id` alone.
  """

  gap_id: str
  source_statements: frozenset[SourceStatement] = frozenset()

  def same_gap(self, other: "GapKey") -> bool:
    """Whether two keys name the same gap: shared statements, else the same id."""
    if self.source_statements or other.source_statements:
      return bool(self.source_statements & other.source_statements)
    return self.gap_id == other.gap_id


def gap_key(record: CandidateGapRecord) -> GapKey:
  """Key a Candidate Gap from the artifact for matching verdicts against it."""
  if record.gap_type != LIMITATION_GAP_TYPE:
    return GapKey(gap_id=record.gap_id)
  return GapKey(
    gap_id=record.gap_id,
    source_statements=frozenset(
      # The passage is the Paper's own words; the statement is only the
      # fallback for a record that quoted nothing.
      SourceStatement(
        citation_key=link.citation_key,
        passage=link.evidence.passage or link.statement,
      )
      for link in record.evidence
    ),
  )


class GapJudgment(BaseModel):
  """One researcher verdict on a Candidate Gap, with when it was last set."""

  gap_id: str
  decision: Decision
  decided_at: datetime
  title: str = ""
  # Unanswered Limitations only; empty on cell gaps and on pre-migration verdicts.
  source_statements: list[SourceStatement] = []

  @property
  def key(self) -> GapKey:
    """The key this verdict matches cards by."""
    return GapKey(
      gap_id=self.gap_id, source_statements=frozenset(self.source_statements)
    )

  @property
  def is_legacy_limitation(self) -> bool:
    """A limitation verdict saved by group id only, before statement keying."""
    return self.gap_id.startswith(LIMITATION_ID_PREFIX) and not self.source_statements

  def judges(self, gap: GapKey) -> bool:
    """Whether this verdict applies to the gap; a legacy limitation one never does."""
    return not self.is_legacy_limitation and self.key.same_gap(gap)


class JudgmentLog(BaseModel):
  """Every accept/reject judgment recorded for one Corpus, on disk as JSON."""

  judgments: list[GapJudgment] = []

  def by_gap_id(self) -> dict[str, GapJudgment]:
    """Index the judgments by the gap id they were recorded under, latest winning."""
    return {judgment.gap_id: judgment for judgment in self.judgments}

  def verdict_for(self, gap: GapKey) -> GapJudgment | None:
    """Return the latest verdict that applies to this gap, if any."""
    matching = [judgment for judgment in self.judgments if judgment.judges(gap)]
    if not matching:
      return None
    return max(matching, key=_decided_at)

  def orphaned(self, gaps: Iterable[GapKey]) -> list[GapJudgment]:
    """List the verdicts that apply to none of these gaps."""
    keys = list(gaps)
    return [
      judgment
      for judgment in self.judgments
      if not any(judgment.judges(key) for key in keys)
    ]


def _judgments_path(corpus_root: Path) -> Path:
  """Return where a corpus directory keeps its accept/reject judgments."""
  return corpus_root / JUDGMENTS_DIR / JUDGMENTS_NAME


def _read_log(corpus_root: Path) -> JudgmentLog:
  """Read the log exactly as saved, or an empty one when none was."""
  path = _judgments_path(corpus_root)
  if not path.is_file():
    return JudgmentLog()
  return JudgmentLog.model_validate_json(path.read_text(encoding="utf-8"))


def load_judgments(corpus_root: Path) -> JudgmentLog:
  """
  Read a Corpus's judgments, returning an empty log when none were saved.

  Legacy limitation verdicts are migrated to statement keys first, and the
  migrated log is written back, so the migration runs once per verdict.
  """
  log = _read_log(corpus_root)
  if not any(judgment.is_legacy_limitation for judgment in log.judgments):
    return log
  migrated = _migrate(log, candidate_gaps_versions(corpus_root))
  if migrated != log:
    _write_log(corpus_root, migrated)
  return migrated


def _migrate(
  log: JudgmentLog, versions: list[tuple[datetime, CandidateGapsArtifact]]
) -> JudgmentLog:
  """Give each legacy limitation verdict the statements of the card it judged."""
  newest_first = sorted(versions, key=lambda version: version[0], reverse=True)
  judgments: list[GapJudgment] = []
  for judgment in log.judgments:
    record = (
      _judged_record(judgment, newest_first) if judgment.is_legacy_limitation else None
    )
    if record is None:
      judgments.append(judgment)
      continue
    key = gap_key(record)
    judgments.append(
      judgment.model_copy(
        update={
          "title": judgment.title or record.title,
          "source_statements": _sorted_statements(key.source_statements),
        }
      )
    )
  return JudgmentLog(judgments=judgments)


def _judged_record(
  judgment: GapJudgment,
  newest_first: list[tuple[datetime, CandidateGapsArtifact]],
) -> CandidateGapRecord | None:
  """Find the card a verdict judged, in the artifact on screen when it was made."""
  decided_at = judgment.decided_at
  if decided_at.tzinfo is None:
    decided_at = decided_at.replace(tzinfo=timezone.utc)
  on_screen = next(
    (artifact for written_at, artifact in newest_first if written_at <= decided_at),
    None,
  )
  if on_screen is None:
    return None
  return next(
    (
      record
      for record in on_screen.gaps
      if record.gap_id == judgment.gap_id and record.gap_type == LIMITATION_GAP_TYPE
    ),
    None,
  )


def _sorted_statements(statements: Iterable[SourceStatement]) -> list[SourceStatement]:
  """Order statements so the saved file is stable to diff."""
  return sorted(statements, key=lambda s: (s.citation_key, s.passage))


def _decided_at(judgment: GapJudgment) -> datetime:
  """Order verdicts by when they were made."""
  return judgment.decided_at


def _write_order(judgment: GapJudgment) -> tuple[str, list[tuple[str, str]]]:
  """Order verdicts by gap id, then by statements, for a file stable to diff."""
  statements = [(s.citation_key, s.passage) for s in judgment.source_statements]
  return judgment.gap_id, statements


def _write_log(corpus_root: Path, log: JudgmentLog) -> None:
  """Write the judgment log, sorted by gap id so the file is stable to diff."""
  path = _judgments_path(corpus_root)
  path.parent.mkdir(parents=True, exist_ok=True)
  ordered = JudgmentLog(judgments=sorted(log.judgments, key=_write_order))
  path.write_text(ordered.model_dump_json(indent=2), encoding="utf-8")


def _as_key(gap: GapKey | str) -> GapKey:
  """Accept a bare gap id for a cell gap, whose id is its key."""
  return gap if isinstance(gap, GapKey) else GapKey(gap_id=gap)


def record_judgment(
  corpus_root: Path, gap: GapKey | str, decision: Decision, title: str = ""
) -> GapJudgment:
  """
  Accept or reject a gap, overwriting every verdict that applied to it.

  The title is kept so a verdict whose gap later disappears can still be named.
  """
  key = _as_key(gap)
  log = load_judgments(corpus_root)
  updated = GapJudgment(
    gap_id=key.gap_id,
    decision=decision,
    decided_at=datetime.now(timezone.utc),
    title=title,
    source_statements=_sorted_statements(key.source_statements),
  )
  kept = [judgment for judgment in log.judgments if not judgment.judges(key)]
  _write_log(corpus_root, JudgmentLog(judgments=[*kept, updated]))
  return updated


def clear_judgment(corpus_root: Path, gap: GapKey | str) -> None:
  """Undo every verdict on a gap, returning it to undecided, and persist it."""
  key = _as_key(gap)
  log = load_judgments(corpus_root)
  kept = [judgment for judgment in log.judgments if not judgment.judges(key)]
  if len(kept) != len(log.judgments):
    _write_log(corpus_root, JudgmentLog(judgments=kept))
