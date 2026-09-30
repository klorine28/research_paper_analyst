"""
Assembling the Verify Extractions page from a Paper's Extraction and its review.

These are plain functions with no Streamlit dependency: the layout layer stays
thin by asking here for finished data (ADR 0003). The view pairs each of the
seven Extraction fields with its facts, each fact's Evidence passage and section,
and any reviewer verdict overlaid on it, plus the facts a reviewer added. The
app layer renders this and wires the verbs to `dashboard.extraction_review`.

The overlay never changes `extractions.json`; this view merely reads the raw
Extraction and layers the researcher's verdicts on top for display.
"""

from pathlib import Path

from pydantic import BaseModel

from research_gap_dashboard.dashboard.artifacts import (
  ExtractedFactRecord,
  ExtractionRecord,
  PaperRecord,
)
from research_gap_dashboard.dashboard.evidence_context import (
  EvidenceExpander,
  ExpandedEvidence,
)
from research_gap_dashboard.dashboard.extraction_review import (
  FIELD_NAMES,
  PaperReview,
)

# Human labels for the seven Extraction fields, in the fixed display order.
FIELD_LABELS: dict[str, str] = {
  "research_question": "Research question",
  "methods": "Methods",
  "populations": "Populations",
  "datasets": "Datasets",
  "key_findings": "Key findings",
  "limitations": "Limitations",
  "future_work": "Future work",
}


class ReviewFactView(BaseModel):
  """One extracted (or added) fact, with any reviewer verdict overlaid on it."""

  field: str
  fact_index: int
  statement: str
  passage: str
  section: str
  verdict: str | None = None
  edited_statement: str | None = None
  edited_passage: str | None = None
  edited_section: str | None = None
  is_added: bool = False
  fact_id: str = ""
  context: ExpandedEvidence | None = None

  @property
  def effective_statement(self) -> str:
    """The fact text to show: the reviewer's edit when present, else the original."""
    return (
      self.edited_statement if self.edited_statement is not None else self.statement
    )

  @property
  def effective_passage(self) -> str:
    """The Evidence to show: the reviewer's edit when present, else the original."""
    return self.edited_passage if self.edited_passage is not None else self.passage

  @property
  def effective_section(self) -> str:
    """The section to show: the reviewer's edit when present, else the original."""
    return self.edited_section if self.edited_section is not None else self.section


class ReviewFieldView(BaseModel):
  """One Extraction field on the Verify page: its label and reviewed facts."""

  field: str
  label: str
  facts: list[ReviewFactView] = []

  @property
  def fact_count(self) -> int:
    """How many facts this field holds, extracted and added together."""
    return len(self.facts)


class PaperReviewView(BaseModel):
  """One Paper's Verify Extractions page, already assembled for rendering."""

  citation_key: str
  title: str
  doi: str
  pdf_path: Path | None
  fields: list[ReviewFieldView]

  @property
  def reviewed_count(self) -> int:
    """How many extracted facts carry a reviewer verdict."""
    return sum(
      1
      for field in self.fields
      for fact in field.facts
      if not fact.is_added and fact.verdict is not None
    )

  @property
  def extracted_count(self) -> int:
    """How many extracted (not added) facts the Paper's Extraction holds."""
    return sum(1 for field in self.fields for fact in field.facts if not fact.is_added)

  @property
  def added_count(self) -> int:
    """How many missing facts the reviewer added across all fields."""
    return sum(1 for field in self.fields for fact in field.facts if fact.is_added)


def _extracted_fact_view(
  field: str,
  index: int,
  fact: ExtractedFactRecord,
  review: PaperReview,
  expander: EvidenceExpander,
  citation_key: str,
) -> ReviewFactView:
  """Build the view for one extracted fact, overlaying any verdict on it."""
  verdict = review.verdict_for(field, index)
  view = ReviewFactView(
    field=field,
    fact_index=index,
    statement=fact.statement,
    passage=fact.evidence.passage,
    section=fact.evidence.section,
    verdict=verdict.verdict if verdict is not None else None,
    edited_statement=verdict.edited_statement if verdict is not None else None,
    edited_passage=verdict.edited_passage if verdict is not None else None,
    edited_section=verdict.edited_section if verdict is not None else None,
  )
  view.context = expander.context_for(citation_key, view.effective_passage)
  return view


def _field_view(
  field: str,
  facts: list[ExtractedFactRecord],
  review: PaperReview,
  expander: EvidenceExpander,
  citation_key: str,
) -> ReviewFieldView:
  """Build the view for one Extraction field: its extracted then added facts."""
  views = [
    _extracted_fact_view(field, index, fact, review, expander, citation_key)
    for index, fact in enumerate(facts)
  ]
  for added in review.added_facts:
    if added.field != field:
      continue
    views.append(
      ReviewFactView(
        field=field,
        fact_index=-1,
        statement=added.statement,
        passage=added.passage,
        section=added.section,
        is_added=True,
        fact_id=added.fact_id,
        context=expander.context_for(citation_key, added.passage),
      )
    )
  return ReviewFieldView(field=field, label=FIELD_LABELS[field], facts=views)


def build_extraction_review(
  extraction: ExtractionRecord,
  review: PaperReview,
  paper: PaperRecord,
  corpus_root: Path | None = None,
) -> PaperReviewView:
  """
  Assemble a Paper's Verify Extractions page from its Extraction and its review.

  A corpus root lets each fact's Evidence widen to its containing paragraph
  (`evidence_context`), the same deterministic expansion the gap cards use;
  without one the passages stay bare so unit tests need no parsed text on disk.
  """
  expander = EvidenceExpander(corpus_root)
  fields = [
    _field_view(
      name, getattr(extraction.fields, name), review, expander, paper.citation_key
    )
    for name in FIELD_NAMES
  ]
  return PaperReviewView(
    citation_key=paper.citation_key,
    title=paper.title,
    doi=paper.doi,
    pdf_path=paper.pdf_path,
    fields=fields,
  )
