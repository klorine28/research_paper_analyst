"""
The promote-gold step: turn a reviewed Extraction into a Gold Extraction fixture.

A Gold Extraction is a human-verified Extraction kept as a committed regression
fixture, so later pipeline changes are checked against what a person confirmed
the Paper says (CONTEXT > Gold Extraction; ADR 0003). This step is pipeline-side
and CLI-only, never the browser: it reads `extractions.json` plus the researcher's
review overlay and writes the gold artifact. Running the in-app review over the
cardiology fixture Corpus and promoting the result is what satisfies issue #9's
human gate.

Applying the overlay is deterministic: an approved fact is kept as-is, an edited
fact keeps the reviewer's corrected text and Evidence, a flagged or removed fact
is dropped, an unreviewed fact is kept unchanged, and every added fact is appended
to its field. Every kept passage is re-verified verbatim against the Paper's parsed
text before the gold is written (CODING_STANDARDS > Research integrity); a Paper
whose parsed text is missing keeps its facts unverified and is noted.
"""

import logging
from pathlib import Path
from typing import cast, get_args

from pydantic import BaseModel

from research_gap_dashboard.corpus_layout import inspect_corpus_layout
from research_gap_dashboard.dashboard.extraction_review import (
  FIELD_NAMES,
  AddedFact,
  FactVerdict,
  PaperReview,
  load_extraction_review,
)
from research_gap_dashboard.extract import (
  Evidence,
  ExtractedFact,
  Extraction,
  ExtractionFields,
  _verify_evidence,
  read_extractions,
)
from research_gap_dashboard.parsing import SectionLabel, read_parsed_paper

logger = logging.getLogger(__name__)

GOLD_EXTRACTION_NAME = "gold_extractions.json"

# The section labels a valid Evidence may carry (the extract stage's SectionLabel).
_SECTION_LABELS: frozenset[str] = frozenset(get_args(SectionLabel))


def _as_section_label(section: str) -> SectionLabel:
  """Coerce a reviewer's section string to a valid label, defaulting to 'other'."""
  return cast(SectionLabel, section) if section in _SECTION_LABELS else "other"


class GoldExtractions(BaseModel):
  """A human-verified Extraction per Paper, the committed regression fixture."""

  corpus_root: Path
  extractions: list[Extraction] = []


def _apply_verdict_to_fact(
  fact: ExtractedFact, verdict: FactVerdict | None
) -> ExtractedFact | None:
  """Apply a reviewer's verdict to one fact, or None when it should be dropped."""
  if verdict is None or verdict.verdict == "approved":
    return fact
  if verdict.verdict in ("flagged", "removed"):
    return None
  # verdict == "edited": keep the reviewer's corrected text and/or Evidence.
  return ExtractedFact(
    statement=(
      verdict.edited_statement
      if verdict.edited_statement is not None
      else fact.statement
    ),
    evidence=Evidence(
      passage=(
        verdict.edited_passage
        if verdict.edited_passage is not None
        else fact.evidence.passage
      ),
      section=(
        _as_section_label(verdict.edited_section)
        if verdict.edited_section is not None
        else fact.evidence.section
      ),
    ),
  )


def _added_fact(added: AddedFact) -> ExtractedFact:
  """Turn a reviewer's added fact into an extracted fact for the gold Extraction."""
  return ExtractedFact(
    statement=added.statement,
    evidence=Evidence(passage=added.passage, section=_as_section_label(added.section)),
  )


def _gold_field(
  field: str, facts: list[ExtractedFact], review: PaperReview
) -> list[ExtractedFact]:
  """Build one field's gold facts: verdicts applied, then added facts appended."""
  kept: list[ExtractedFact] = []
  for index, fact in enumerate(facts):
    resolved = _apply_verdict_to_fact(fact, review.verdict_for(field, index))
    if resolved is not None:
      kept.append(resolved)
  kept.extend(
    _added_fact(added) for added in review.added_facts if added.field == field
  )
  return kept


def _gold_fields(fields: ExtractionFields, review: PaperReview) -> ExtractionFields:
  """Apply a Paper's review to its Extraction fields, yielding the gold fields."""
  return ExtractionFields(
    **{name: _gold_field(name, getattr(fields, name), review) for name in FIELD_NAMES}
  )


def promote_gold(corpus_root: Path) -> GoldExtractions:
  """
  Build the Gold Extraction for a Corpus from its Extraction plus review overlay.

  Every kept or added passage is re-verified verbatim against the Paper's parsed
  text before it enters the gold; a Paper with no parsed text is left unverified
  and logged, so the gate is explicit rather than silent.
  """
  report = read_extractions(corpus_root)
  log = load_extraction_review(corpus_root)

  extractions: list[Extraction] = []
  for extraction in report.extractions:
    review = log.review_for(extraction.citation_key)
    gold_fields = _gold_fields(extraction.fields, review)
    _reverify(corpus_root, extraction.citation_key, gold_fields)
    extractions.append(
      Extraction(citation_key=extraction.citation_key, fields=gold_fields)
    )

  return GoldExtractions(corpus_root=corpus_root, extractions=extractions)


def _reverify(corpus_root: Path, citation_key: str, fields: ExtractionFields) -> None:
  """Re-verify a Paper's gold Evidence against its parsed text, when it exists."""
  try:
    parsed = read_parsed_paper(corpus_root, citation_key)
  except FileNotFoundError:
    logger.warning(
      "No parsed text for %s; gold Evidence left unverified.", citation_key
    )
    return
  _verify_evidence(parsed, fields)


def write_gold(gold: GoldExtractions, output_path: Path) -> None:
  """Write a Gold Extraction fixture to disk, creating parent directories."""
  output_path.parent.mkdir(parents=True, exist_ok=True)
  output_path.write_text(gold.model_dump_json(indent=2), encoding="utf-8")


def read_gold(path: Path) -> GoldExtractions:
  """Read a committed Gold Extraction fixture."""
  return GoldExtractions.model_validate_json(path.read_text(encoding="utf-8"))


def default_gold_path(corpus_root: Path) -> Path:
  """Default output path for a Corpus's gold fixture, under its artifacts/."""
  return inspect_corpus_layout(corpus_root).layout.artifacts_dir / GOLD_EXTRACTION_NAME
