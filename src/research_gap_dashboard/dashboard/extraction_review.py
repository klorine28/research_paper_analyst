"""
Persisting the researcher's per-field verdicts on a Paper's Extraction.

Extraction Review is annotation-only (ADR 0003): the reviewer's verdicts —
approve, edit, flag as wrong, remove a hallucinated fact, add a missing fact —
are written to a researcher-review overlay under the Corpus's `judgments/`
directory, exactly like gap judgments. The overlay never modifies
`extractions.json`; `aggregate`/`detect` keep running off the raw Extraction,
so the pipeline stays deterministic.

Like `judgments`, this is the read/write seam: plain functions with no Streamlit
dependency, so the app layer stays thin and the persistence can be tested
directly. Existing facts are keyed by their field and their index in that field's
list in `extractions.json`; because the overlay never rewrites the artifact, those
indices are stable. Added facts carry their own id so they can be removed again.

Edited and added Evidence is held to the extract stage's grounding rule: the
passage must be verbatim in the Paper's parsed text, re-verified before the write
(CODING_STANDARDS > Research integrity). Grounding uses the dashboard's own
matcher (`dashboard.grounding`), never a pipeline import (ADR 0002).
"""

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from research_gap_dashboard.dashboard.grounding import (
  read_parsed_paper,
  verify_grounded,
)

JUDGMENTS_DIR = "judgments"
EXTRACTION_REVIEW_NAME = "extraction_review.json"

# The seven Extraction fields, the same set and order the extract stage uses.
FIELD_NAMES: tuple[str, ...] = (
  "research_question",
  "methods",
  "populations",
  "datasets",
  "key_findings",
  "limitations",
  "future_work",
)

# The verdicts a reviewer can leave on an extracted fact. `edited` records a
# correction to the fact text and/or its Evidence; `flagged` marks it wrong;
# `removed` marks it a hallucination. A fact with no verdict is simply unreviewed.
Verdict = Literal["approved", "edited", "flagged", "removed"]


class UnknownFieldError(ValueError):
  """Raised when a verdict names a field that is not one of the seven."""


class FactVerdict(BaseModel):
  """One reviewer verdict on one extracted fact, keyed by field and index."""

  field: str
  fact_index: int
  verdict: Verdict
  edited_statement: str | None = None
  edited_passage: str | None = None
  edited_section: str | None = None
  decided_at: datetime

  @property
  def key(self) -> tuple[str, int]:
    """The (field, index) pair that identifies the fact this verdict is on."""
    return (self.field, self.fact_index)


class AddedFact(BaseModel):
  """A missing fact a reviewer added, grounded like any extracted fact."""

  fact_id: str
  field: str
  statement: str
  passage: str
  section: str
  added_at: datetime


class PaperReview(BaseModel):
  """One Paper's Extraction Review: verdicts on its facts and any added facts."""

  citation_key: str
  verdicts: list[FactVerdict] = []
  added_facts: list[AddedFact] = []

  def verdict_for(self, field: str, fact_index: int) -> FactVerdict | None:
    """Return the verdict on a given fact, or None when it is unreviewed."""
    return next(
      (v for v in self.verdicts if v.field == field and v.fact_index == fact_index),
      None,
    )


class ExtractionReviewLog(BaseModel):
  """Every Paper's Extraction Review for one Corpus, on disk as JSON."""

  reviews: list[PaperReview] = []

  def by_citation_key(self) -> dict[str, PaperReview]:
    """Index the reviews by the Paper they review, latest write winning."""
    return {review.citation_key: review for review in self.reviews}

  def review_for(self, citation_key: str) -> PaperReview:
    """Return a Paper's review, or an empty one when it was never reviewed."""
    return self.by_citation_key().get(
      citation_key, PaperReview(citation_key=citation_key)
    )


def _review_path(corpus_root: Path) -> Path:
  """Return where a corpus directory keeps its Extraction Review overlay."""
  return corpus_root / JUDGMENTS_DIR / EXTRACTION_REVIEW_NAME


def load_extraction_review(corpus_root: Path) -> ExtractionReviewLog:
  """Read a Corpus's Extraction Review, empty when none was ever saved."""
  path = _review_path(corpus_root)
  if not path.is_file():
    return ExtractionReviewLog()
  return ExtractionReviewLog.model_validate_json(path.read_text(encoding="utf-8"))


def _require_field(field: str) -> None:
  """Refuse a verdict on anything but one of the seven Extraction fields."""
  if field not in FIELD_NAMES:
    raise UnknownFieldError(
      f"Unknown Extraction field '{field}'; expected one of {', '.join(FIELD_NAMES)}."
    )


def _now() -> datetime:
  """Return the current instant in UTC, factored out so writes stay easy to read."""
  return datetime.now(timezone.utc)


def _write_log(corpus_root: Path, log: ExtractionReviewLog) -> None:
  """Write the review overlay, papers and verdicts sorted so the file diffs cleanly."""
  path = _review_path(corpus_root)
  path.parent.mkdir(parents=True, exist_ok=True)
  ordered = ExtractionReviewLog(
    reviews=[_sorted_review(review) for review in _nonempty_reviews(log)]
  )
  path.write_text(ordered.model_dump_json(indent=2), encoding="utf-8")


def _nonempty_reviews(log: ExtractionReviewLog) -> list[PaperReview]:
  """Drop Papers whose review holds no verdicts and no added facts, sorted by key."""
  kept = [r for r in log.reviews if r.verdicts or r.added_facts]
  return sorted(kept, key=lambda review: review.citation_key)


def _verdict_sort_key(verdict: FactVerdict) -> tuple[str, int]:
  """Sort verdicts by their field then index, for a stable on-disk order."""
  return (verdict.field, verdict.fact_index)


def _added_fact_sort_key(fact: AddedFact) -> str:
  """Sort added facts by their id, for a stable on-disk order."""
  return fact.fact_id


def _sorted_review(review: PaperReview) -> PaperReview:
  """Return a review with its verdicts and added facts in a stable order."""
  return PaperReview(
    citation_key=review.citation_key,
    verdicts=sorted(review.verdicts, key=_verdict_sort_key),
    added_facts=sorted(review.added_facts, key=_added_fact_sort_key),
  )


def _replace_review(
  log: ExtractionReviewLog, review: PaperReview
) -> ExtractionReviewLog:
  """Return the log with one Paper's review swapped in for any earlier one."""
  kept = [r for r in log.reviews if r.citation_key != review.citation_key]
  return ExtractionReviewLog(reviews=[*kept, review])


def record_fact_verdict(
  corpus_root: Path,
  citation_key: str,
  field: str,
  fact_index: int,
  verdict: Verdict,
  *,
  edited_statement: str | None = None,
  edited_passage: str | None = None,
  edited_section: str | None = None,
) -> FactVerdict:
  """
  Record a verdict on one extracted fact, overwriting any earlier one, and persist.

  An edited Evidence passage is re-verified verbatim against the Paper's parsed
  text before the write; an ungrounded correction raises and nothing is saved.
  """
  _require_field(field)
  if edited_passage is not None:
    verify_grounded(edited_passage, read_parsed_paper(corpus_root, citation_key))

  log = load_extraction_review(corpus_root)
  review = log.review_for(citation_key)
  entry = FactVerdict(
    field=field,
    fact_index=fact_index,
    verdict=verdict,
    edited_statement=edited_statement,
    edited_passage=edited_passage,
    edited_section=edited_section,
    decided_at=_now(),
  )
  kept = [v for v in review.verdicts if v.key != (field, fact_index)]
  updated = PaperReview(
    citation_key=citation_key,
    verdicts=[*kept, entry],
    added_facts=review.added_facts,
  )
  _write_log(corpus_root, _replace_review(log, updated))
  return entry


def clear_fact_verdict(
  corpus_root: Path, citation_key: str, field: str, fact_index: int
) -> None:
  """Undo a fact's verdict, returning it to unreviewed, and persist the change."""
  log = load_extraction_review(corpus_root)
  review = log.review_for(citation_key)
  kept = [v for v in review.verdicts if v.key != (field, fact_index)]
  if len(kept) == len(review.verdicts):
    return
  updated = PaperReview(
    citation_key=citation_key, verdicts=kept, added_facts=review.added_facts
  )
  _write_log(corpus_root, _replace_review(log, updated))


def add_fact(
  corpus_root: Path,
  citation_key: str,
  field: str,
  statement: str,
  passage: str,
  section: str,
) -> AddedFact:
  """
  Add a missing fact to a Paper's review and persist it.

  The Evidence passage is re-verified verbatim against the Paper's parsed text
  before the write; an ungrounded addition raises and nothing is saved.
  """
  _require_field(field)
  verify_grounded(passage, read_parsed_paper(corpus_root, citation_key))

  log = load_extraction_review(corpus_root)
  review = log.review_for(citation_key)
  fact = AddedFact(
    fact_id=uuid.uuid4().hex,
    field=field,
    statement=statement,
    passage=passage,
    section=section,
    added_at=_now(),
  )
  updated = PaperReview(
    citation_key=citation_key,
    verdicts=review.verdicts,
    added_facts=[*review.added_facts, fact],
  )
  _write_log(corpus_root, _replace_review(log, updated))
  return fact


def remove_added_fact(corpus_root: Path, citation_key: str, fact_id: str) -> None:
  """Remove a previously added fact from a Paper's review, and persist the change."""
  log = load_extraction_review(corpus_root)
  review = log.review_for(citation_key)
  kept = [f for f in review.added_facts if f.fact_id != fact_id]
  if len(kept) == len(review.added_facts):
    return
  updated = PaperReview(
    citation_key=citation_key, verdicts=review.verdicts, added_facts=kept
  )
  _write_log(corpus_root, _replace_review(log, updated))
