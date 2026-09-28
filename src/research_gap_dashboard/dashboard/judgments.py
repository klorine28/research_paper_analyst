"""
Persisting the researcher's accept/reject judgments on Candidate Gaps.

A judgment is the human verdict on a Candidate Gap: a Corpus is analysed once
but reviewed over months, so the accept/reject state must outlive the Streamlit
session and any restart. This module is the read/write seam for that state; it
is plain functions with no Streamlit dependency, so the layout layer stays thin
and the persistence can be tested directly.

Judgments live in one JSON file under the Corpus's `judgments/` directory,
keyed by the stable `gap_id` the detect stage assigns each card. The dashboard
owns this schema the way it owns its artifact read models (ADR 0002): it never
imports a pipeline stage. A gap that has never been judged simply has no entry,
so removing a judgment is undoing it, not asserting the gap is undecided.
"""

from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

JUDGMENTS_DIR = "judgments"
JUDGMENTS_NAME = "gap_judgments.json"

Decision = Literal["accepted", "rejected"]


class GapJudgment(BaseModel):
  """One researcher verdict on a Candidate Gap, with when it was last set."""

  gap_id: str
  decision: Decision
  decided_at: datetime


class JudgmentLog(BaseModel):
  """Every accept/reject judgment recorded for one Corpus, on disk as JSON."""

  judgments: list[GapJudgment] = []

  def by_gap_id(self) -> dict[str, GapJudgment]:
    """Index the judgments by the gap they judge, latest write winning."""
    return {judgment.gap_id: judgment for judgment in self.judgments}


def _judgments_path(corpus_root: Path) -> Path:
  """Return where a corpus directory keeps its accept/reject judgments."""
  return corpus_root / JUDGMENTS_DIR / JUDGMENTS_NAME


def load_judgments(corpus_root: Path) -> JudgmentLog:
  """Read a Corpus's judgments, returning an empty log when none were saved."""
  path = _judgments_path(corpus_root)
  if not path.is_file():
    return JudgmentLog()
  return JudgmentLog.model_validate_json(path.read_text(encoding="utf-8"))


def _write_log(corpus_root: Path, log: JudgmentLog) -> None:
  """Write the judgment log, sorted by gap id so the file is stable to diff."""
  path = _judgments_path(corpus_root)
  path.parent.mkdir(parents=True, exist_ok=True)
  by_gap_id = log.by_gap_id()
  ordered = JudgmentLog(judgments=[by_gap_id[gap_id] for gap_id in sorted(by_gap_id)])
  path.write_text(ordered.model_dump_json(indent=2), encoding="utf-8")


def record_judgment(corpus_root: Path, gap_id: str, decision: Decision) -> GapJudgment:
  """Accept or reject a gap, overwriting any earlier verdict, and persist it."""
  log = load_judgments(corpus_root)
  updated = GapJudgment(
    gap_id=gap_id, decision=decision, decided_at=datetime.now(timezone.utc)
  )
  kept = [judgment for judgment in log.judgments if judgment.gap_id != gap_id]
  _write_log(corpus_root, JudgmentLog(judgments=[*kept, updated]))
  return updated


def clear_judgment(corpus_root: Path, gap_id: str) -> None:
  """Undo a gap's judgment, returning it to undecided, and persist the change."""
  log = load_judgments(corpus_root)
  kept = [judgment for judgment in log.judgments if judgment.gap_id != gap_id]
  if len(kept) != len(log.judgments):
    _write_log(corpus_root, JudgmentLog(judgments=kept))
