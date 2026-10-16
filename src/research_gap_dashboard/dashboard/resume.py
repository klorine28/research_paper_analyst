"""
The resume point: where the researcher left a Corpus, so they can pick it up again.

A Corpus is reviewed over months (issue #83). For each Corpus this module keeps
the furthest step of the guided path the researcher reached and when they last
opened it, in one JSON file under the Corpus's `judgments/` directory, next to
the verdicts and the journal it helps them return to. The last Corpus is not
stored separately: it is whichever Corpus was opened most recently, so the
record stays per-Corpus and moving or deleting a Corpus folder cannot leave a
dangling pointer behind.

The furthest step only moves forward: going back to check something never
loses the place reached. Like the other review overlays it is plain functions
with no Streamlit dependency, and it imports no pipeline stage (ADR 0002). It is
not a pipeline input, so writing it never makes a stage stale.
"""

from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel

from research_gap_dashboard.dashboard.guided_flow import STEPS, Step
from research_gap_dashboard.dashboard.judgments import JUDGMENTS_DIR

RESUME_NAME = "resume_point.json"


class ResumePoint(BaseModel):
  """The furthest step reached on a Corpus and when it was last opened."""

  furthest_step: Step | None = None
  last_opened_at: datetime | None = None


def _resume_path(corpus_root: Path) -> Path:
  return corpus_root / JUDGMENTS_DIR / RESUME_NAME


def load_resume_point(corpus_root: Path) -> ResumePoint:
  """Read a Corpus's resume point, empty when it was never opened."""
  path = _resume_path(corpus_root)
  if not path.is_file():
    return ResumePoint()
  return ResumePoint.model_validate_json(path.read_text(encoding="utf-8"))


def record_visit(
  corpus_root: Path, step: Step | None, *, now: datetime | None = None
) -> ResumePoint:
  """
  Record that the Corpus was opened at a step (None: the Home screen).

  The opening time always updates; the furthest step only advances.
  """
  point = load_resume_point(corpus_root)
  furthest = point.furthest_step
  if step is not None and (
    furthest is None or STEPS.index(step) > STEPS.index(furthest)
  ):
    furthest = step
  updated = ResumePoint(
    furthest_step=furthest, last_opened_at=now or datetime.now(timezone.utc)
  )
  path = _resume_path(corpus_root)
  path.parent.mkdir(parents=True, exist_ok=True)
  path.write_text(updated.model_dump_json(indent=2), encoding="utf-8")
  return updated


def last_opened_corpus(corpus_roots: list[Path]) -> Path | None:
  """Return the Corpus opened most recently, or None when none was ever opened."""
  opened = [
    (point.last_opened_at, root)
    for root in corpus_roots
    if (point := load_resume_point(root)).last_opened_at is not None
  ]
  if not opened:
    return None
  return max(opened, key=lambda pair: pair[0])[1]
