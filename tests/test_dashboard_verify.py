"""
Behavior of the Verify Extractions assembly: Extraction plus overlay in, view out.

The page's data is assembled by plain functions the layout layer calls; these
tests exercise that seam (an acceptance criterion). They confirm all seven fields
are present with facts, Evidence, and section, and that a reviewer's verdicts and
added facts are overlaid on the raw Extraction for display.
"""

from pathlib import Path

from research_gap_dashboard.dashboard.artifacts import (
  EvidenceRecord,
  ExtractedFactRecord,
  ExtractionFieldsRecord,
  ExtractionRecord,
  PaperRecord,
)
from research_gap_dashboard.dashboard.extraction_review import (
  AddedFact,
  FactVerdict,
  PaperReview,
)
from research_gap_dashboard.dashboard.verify import (
  FIELD_LABELS,
  build_extraction_review,
)

from datetime import datetime, timezone


def _fact(
  statement: str, passage: str, section: str = "methods"
) -> ExtractedFactRecord:
  """Build one extracted-fact read model with its Evidence."""
  return ExtractedFactRecord(
    statement=statement, evidence=EvidenceRecord(passage=passage, section=section)
  )


def _extraction() -> ExtractionRecord:
  """Build a Paper's Extraction with a couple of methods facts."""
  return ExtractionRecord(
    citation_key="paper-a",
    fields=ExtractionFieldsRecord(
      methods=[
        _fact("Retrospective cohort.", "A retrospective cohort of 200 patients."),
        _fact("Single center.", "The cohort was single-center."),
      ]
    ),
  )


def _paper() -> PaperRecord:
  """Build the Paper's manifest record with a PDF path."""
  return PaperRecord(
    citation_key="paper-a",
    doi="10.1/x",
    title="A study",
    pdf_path=Path("/corpus/papers/paper-a.pdf"),
  )


def test_all_seven_fields_are_present(tmp_path: Path):
  """The view shows every Extraction field, in the fixed order, even empty ones."""
  view = build_extraction_review(
    _extraction(), PaperReview(citation_key="paper-a"), _paper()
  )

  assert [field.field for field in view.fields] == list(FIELD_LABELS)
  methods = next(f for f in view.fields if f.field == "methods")
  assert methods.facts[0].statement == "Retrospective cohort."
  assert methods.facts[0].passage == "A retrospective cohort of 200 patients."
  assert methods.facts[0].section == "methods"


def test_a_verdict_is_overlaid_on_its_fact():
  """A recorded verdict shows on the matching fact; others stay unreviewed."""
  review = PaperReview(
    citation_key="paper-a",
    verdicts=[
      FactVerdict(field="methods", fact_index=0, verdict="approved", decided_at=_now())
    ],
  )

  view = build_extraction_review(_extraction(), review, _paper())

  methods = next(f for f in view.fields if f.field == "methods")
  assert methods.facts[0].verdict == "approved"
  assert methods.facts[1].verdict is None
  assert view.reviewed_count == 1


def test_an_edit_is_shown_in_place_of_the_original():
  """An edited fact shows the reviewer's corrected text and Evidence."""
  review = PaperReview(
    citation_key="paper-a",
    verdicts=[
      FactVerdict(
        field="methods",
        fact_index=0,
        verdict="edited",
        edited_statement="Corrected statement.",
        edited_passage="The cohort was single-center.",
        edited_section="methods",
        decided_at=_now(),
      )
    ],
  )

  view = build_extraction_review(_extraction(), review, _paper())

  fact = next(f for f in view.fields if f.field == "methods").facts[0]
  assert fact.effective_statement == "Corrected statement."
  assert fact.effective_passage == "The cohort was single-center."


def test_added_facts_appear_after_the_extracted_ones():
  """A reviewer-added fact is shown in its field, flagged as added."""
  review = PaperReview(
    citation_key="paper-a",
    added_facts=[
      AddedFact(
        fact_id="abc",
        field="methods",
        statement="An added method.",
        passage="A retrospective cohort of 200 patients.",
        section="methods",
        added_at=_now(),
      )
    ],
  )

  view = build_extraction_review(_extraction(), review, _paper())

  methods = next(f for f in view.fields if f.field == "methods")
  assert methods.facts[-1].is_added
  assert methods.facts[-1].fact_id == "abc"
  assert view.added_count == 1
  assert view.extracted_count == 2


def test_the_pdf_path_is_carried_onto_the_view():
  """The view keeps the Paper's PDF path so the page can link to it."""
  view = build_extraction_review(
    _extraction(), PaperReview(citation_key="paper-a"), _paper()
  )

  assert view.pdf_path == Path("/corpus/papers/paper-a.pdf")


def _now() -> datetime:
  """Return a timestamp for building verdicts in tests."""
  return datetime.now(timezone.utc)
