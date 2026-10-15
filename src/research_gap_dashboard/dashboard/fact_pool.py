"""
The chat's fact pool: a Corpus's Extractions mapped for per-question retrieval.

Conversational Analytics retrieves the facts relevant to each question (issue
#97); this module gathers what it retrieves from. Every Extraction fact becomes
one pool entry carrying its verbatim Evidence and the taxonomy categories (plus
the Paper's original phrases) the Aggregate stage normalized it onto, linked by
the assignment's `fact_ref`. It only maps artifact read models onto the analytics
value model; the retrieval itself lives with the analytics machinery (ADR 0002).
"""

from collections import defaultdict
from pathlib import Path

from research_gap_dashboard.dashboard.artifacts import (
  ExtractionsArtifact,
  NormalizedFactsArtifact,
  has_extractions,
  has_normalized_facts,
  load_extractions,
  load_normalized_facts,
)
from research_gap_dashboard.fact_retrieval import (
  FIELD_ORDER,
  ExtractedFact,
  parse_fact_ref,
)


def _terms_by_fact(
  normalized: NormalizedFactsArtifact | None,
) -> dict[tuple[str, str, int], list[str]]:
  """Index each fact's category labels and original phrases by (key, field, index)."""
  terms: dict[tuple[str, str, int], list[str]] = defaultdict(list)
  if normalized is None:
    return terms
  for record in normalized.normalized:
    for assignment in record.assignments:
      ref = parse_fact_ref(assignment.fact_ref)
      if ref is None:
        continue
      slot = terms[(record.citation_key, *ref)]
      for term in (assignment.category_label, assignment.original_term):
        if term and term not in slot:
          slot.append(term)
  return terms


def build_fact_pool(
  extractions: ExtractionsArtifact, normalized: NormalizedFactsArtifact | None
) -> list[ExtractedFact]:
  """Map every Extraction fact, in Corpus then field order, onto a pool entry."""
  terms = _terms_by_fact(normalized)
  pool: list[ExtractedFact] = []
  for extraction in extractions.extractions:
    for field in FIELD_ORDER:
      for index, fact in enumerate(getattr(extraction.fields, field)):
        if not fact.statement.strip():
          continue
        pool.append(
          ExtractedFact(
            citation_key=extraction.citation_key,
            field=field,
            statement=fact.statement,
            passage=fact.evidence.passage,
            section=fact.evidence.section,
            terms=terms.get((extraction.citation_key, field, index), []),
          )
        )
  return pool


def load_fact_pool(corpus_root: Path) -> list[ExtractedFact]:
  """Read a Corpus's fact pool from disk; empty before the extract stage has run."""
  if not has_extractions(corpus_root):
    return []
  normalized = (
    load_normalized_facts(corpus_root) if has_normalized_facts(corpus_root) else None
  )
  return build_fact_pool(load_extractions(corpus_root), normalized)
