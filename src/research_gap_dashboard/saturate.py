"""
The saturate stage: try to disprove one Candidate Gap against the wider literature.

ADR 0009's saturation check searches OpenAlex and PubMed for work that would
close a Knowledge or Coverage Gap, and records what it tried. It can only weaken
a gap: a check ends "possibly addressed" (some search hit mentions both of the
cell's concepts in its title or abstract), "no closing work found" (a dated list
of the sources and verbatim queries that found nothing), or "not run" (every
source failed, e.g. offline). It never confirms a gap and never changes a gap's
confidence (ADR 0009 §6).

Queries come from controlled vocabulary: each concept's taxonomy label and
aliases (Corpus-approved aliases included, ADR 0010), plus the Corpus's own
terms that were mapped onto the category; PubMed also gets the category's MeSH
heading. Screening is deterministic: a hit counts only when its title or
abstract contains a term of each concept.

Hits are out-of-Corpus works. They are labelled as such, never cited and never
Evidence; a work already in the Corpus is never a hit. Each run appends one
dated snapshot to `artifacts/saturation_checks.json` and never rewrites an
earlier one, so the history of checks stays inspectable.

Not here yet (later tickets of the spec): automatic and "accepted" modes, the
30-day cache, LLM query variants and LLM screening, Unanswered Limitations.
"""

import logging
import re
import xml.etree.ElementTree as ET
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import httpx
from pydantic import BaseModel, Field

from research_gap_dashboard.corpus_layout import inspect_corpus_layout
from research_gap_dashboard.detect import CellGap, read_candidate_gaps
from research_gap_dashboard.ingest import read_manifest
from research_gap_dashboard.sources import (
  DEFAULT_SEARCH_LIMIT,
  TopicSearchSource,
  WorkRecord,
)
from research_gap_dashboard.taxonomy import Axis, Taxonomy

logger = logging.getLogger(__name__)

SATURATION_CHECKS_NAME = "saturation_checks.json"

# Terms per concept in one query: enough to bridge wording differences, few
# enough that a query stays readable on the card and within API limits.
MAX_TERMS_PER_CONCEPT = 6
# A Corpus term longer than this is a description, not a search term.
MAX_CORPUS_TERM_WORDS = 4

SCREENING_METHOD = "title/abstract co-presence of a term from each concept"

SaturationStatus = Literal["possibly_addressed", "no_closing_work_found", "not_run"]
ScreeningVerdict = Literal["mentions_both", "missing_concept"]

# Every consumer must show this, so no reader mistakes a hit for Evidence or a
# Corpus Paper (CODING_STANDARDS > Research integrity).
OUT_OF_CORPUS_LABEL = (
  "Out-of-corpus works: found by searching the wider literature, not Papers in "
  "the Corpus. They are never cited and never count as Evidence; a check can "
  "only suggest a gap is possibly addressed, never confirm it."
)


# Every sentence this stage writes into the artifact, in one place so it can be
# translated later (CODING_STANDARDS > Language and vocabulary). None of them
# may claim a gap is confirmed (ADR 0009 §1).
_TEXT: dict[str, str] = {
  "not_run": "Not run: no source could be searched ({errors}).",
  "possibly_addressed": (
    "Possibly addressed: {count} screened hit(s) in {sources} mention both "
    "concepts. Review them before relying on this gap."
  ),
  "no_closing_work_found": (
    "No closing work found in {sources} for the recorded queries. This does not "
    "confirm the gap."
  ),
  "mentions_both": "Title/abstract mentions {terms}.",
  "missing_concept": "Title/abstract does not mention {labels}.",
}


class SaturationError(Exception):
  """Raised when a gap cannot be checked (unknown id, unsupported Gap Type)."""


class ConceptTerms(BaseModel):
  """One side of a cell gap, and the terms the check searched it by."""

  axis: Axis
  category_id: str
  label: str
  terms: list[str] = Field(description="Free-text terms, label first.")
  mesh_heading: str = Field(default="", description="PubMed MeSH heading, if any.")


class SourceSearch(BaseModel):
  """One search against one source: the verbatim query and what came back."""

  source: str
  query: str
  returned_work_ids: list[str] = []
  in_corpus_work_ids: list[str] = Field(
    default=[], description="Returned works dropped because they are in the Corpus."
  )
  error: str = Field(default="", description="Why the search did not run, if so.")


class SaturationHit(BaseModel):
  """One out-of-Corpus work a search returned, with its screening verdict."""

  work_id: str
  sources: list[str]
  title: str = ""
  year: int | None = None
  venue: str = ""
  doi: str = ""
  pmid: str = ""
  openalex_id: str = ""
  verdict: ScreeningVerdict
  reason: str


class SaturationCheck(BaseModel):
  """One dated snapshot of a saturation check on one gap."""

  check_id: str
  gap_id: str
  gap_type: str
  gap_title: str
  checked_at: datetime
  trigger: Literal["manual"] = "manual"
  status: SaturationStatus
  status_reason: str
  screening_method: str = SCREENING_METHOD
  concepts: list[ConceptTerms]
  searches: list[SourceSearch]
  hits: list[SaturationHit]

  @property
  def screened_hit_count(self) -> int:
    """How many hits passed screening (mention both concepts)."""
    return sum(1 for hit in self.hits if hit.verdict == "mentions_both")


class SaturationChecksReport(BaseModel):
  """The append-only saturation-check log for one Corpus."""

  corpus_root: Path
  label: str = OUT_OF_CORPUS_LABEL
  checks: list[SaturationCheck] = []


def _utc_now() -> datetime:
  """Return the current time, in UTC, to the second."""
  return datetime.now(UTC).replace(microsecond=0)


def saturate_gap(  # pylint: disable=too-many-arguments
  root: Path,
  gap_id: str,
  sources: Sequence[TopicSearchSource],
  taxonomies: Sequence[Taxonomy],
  *,
  limit: int = DEFAULT_SEARCH_LIMIT,
  now: Callable[[], datetime] = _utc_now,
) -> SaturationCheck:
  """
  Search every source for work closing one cell gap; append the dated result.

  Raises `SaturationError` for an unknown gap id or a Gap Type this stage does
  not check yet. A source that fails (offline, rate-limited) is recorded with
  its error; when every source fails the check is "not run", never an error.
  """
  gap = _find_cell_gap(root, gap_id)
  corpus_ids = _corpus_work_ids(
    [(p.doi, "", p.openalex_id) for p in read_manifest(root).papers]
  )
  concepts = [
    _concept(gap, gap.row_axis, gap.row_id, gap.row_label, taxonomies),
    _concept(gap, gap.column_axis, gap.column_id, gap.column_label, taxonomies),
  ]

  searches, hits = _search_all(sources, concepts, limit, corpus_ids)
  status, reason = _status(searches, hits)
  report = read_saturation_checks(root)
  check = SaturationCheck(
    check_id=f"check-{len(report.checks) + 1:04d}",
    gap_id=gap.gap_id,
    gap_type=gap.gap_type,
    gap_title=gap.title,
    checked_at=now(),
    status=status,
    status_reason=reason,
    concepts=concepts,
    searches=searches,
    hits=hits,
  )
  report.corpus_root = root
  report.checks.append(check)
  _write_report(root, report)
  logger.info("Saturation check on %s: %s. %s", gap.gap_id, status, reason)
  return check


def read_saturation_checks(root: Path) -> SaturationChecksReport:
  """Read the saturation-check log, or an empty one if no check has run."""
  path = inspect_corpus_layout(root).layout.artifacts_dir / SATURATION_CHECKS_NAME
  if not path.is_file():
    return SaturationChecksReport(corpus_root=root)
  return SaturationChecksReport.model_validate_json(path.read_text(encoding="utf-8"))


def _find_cell_gap(root: Path, gap_id: str) -> CellGap:
  """Return the Knowledge or Coverage Gap with this id, or explain why not."""
  report = read_candidate_gaps(root)
  gap = next((g for g in report.gaps if g.gap_id == gap_id), None)
  if gap is None:
    raise SaturationError(f"No Candidate Gap '{gap_id}' in this Corpus.")
  if not isinstance(gap, CellGap):
    raise SaturationError(
      f"'{gap_id}' is a {gap.gap_type}; only Knowledge and Coverage Gaps can be "
      "checked against the wider literature so far."
    )
  return gap


def _concept(  # pylint: disable=too-many-arguments,too-many-positional-arguments
  gap: CellGap,
  axis: Axis,
  category_id: str,
  label: str,
  taxonomies: Sequence[Taxonomy],
) -> ConceptTerms:
  """Gather a concept's search terms: label, aliases, then the Corpus's own terms."""
  topic = next(
    (
      t.topic_by_id(category_id)
      for t in taxonomies
      if t.meta.axis == axis and t.topic_by_id(category_id) is not None
    ),
    None,
  )
  candidates = [label]
  mesh_heading = ""
  if topic is not None:
    candidates = [topic.label, *topic.aliases]
    mesh_heading = topic.label if topic.mesh_id else ""
  candidates.extend(
    link.original_term
    for link in gap.evidence
    if link.axis == axis
    and link.category_id == category_id
    and len(link.original_term.split()) <= MAX_CORPUS_TERM_WORDS
  )
  terms: list[str] = []
  seen: set[str] = set()
  for candidate in candidates:
    term = _search_form(candidate)
    if term and term.lower() not in seen and len(terms) < MAX_TERMS_PER_CONCEPT:
      seen.add(term.lower())
      terms.append(term)
  return ConceptTerms(
    axis=axis,
    category_id=category_id,
    label=label,
    terms=terms,
    mesh_heading=mesh_heading,
  )


def _search_form(phrase: str) -> str:
  """
  Turn a vocabulary phrase into a term people write in titles and abstracts.

  MeSH inverts many headings ("Heart Failure, Congestive"); a two-part inverted
  phrase is turned back ("Congestive Heart Failure"). A phrase with digits or
  more commas is not a searchable term and is dropped.
  """
  phrase = " ".join(phrase.replace('"', " ").split())
  if "," not in phrase:
    return phrase
  parts = [part.strip() for part in phrase.split(",")]
  if len(parts) != 2 or any(ch.isdigit() for ch in phrase) or not all(parts):
    return ""
  return f"{parts[1]} {parts[0]}"


def build_query(source_name: str, concepts: Sequence[ConceptTerms]) -> str:
  """
  Build the boolean query one source is sent: each concept's terms ORed, ANDed.

  PubMed restricts free-text terms to title/abstract and adds the MeSH heading;
  OpenAlex (and any other source) gets quoted phrases.
  """
  groups = []
  for concept in concepts:
    if source_name == "pubmed":
      parts = [f'"{concept.mesh_heading}"[MeSH Terms]'] if concept.mesh_heading else []
      parts.extend(f'"{term}"[tiab]' for term in concept.terms)
    else:
      parts = [f'"{term}"' for term in concept.terms]
    groups.append("(" + " OR ".join(parts) + ")")
  return " AND ".join(groups)


def _search_all(
  sources: Sequence[TopicSearchSource],
  concepts: Sequence[ConceptTerms],
  limit: int,
  corpus_ids: set[str],
) -> tuple[list[SourceSearch], list[SaturationHit]]:
  """Search every source; return the searches and the screened hits, best first."""
  searches: list[SourceSearch] = []
  hits: dict[str, SaturationHit] = {}
  for source in sources:
    search, records = _search(source, concepts, limit, corpus_ids)
    searches.append(search)
    for record in records:
      _add_hit(hits, source.name, record, concepts)
  unique = list({id(hit): hit for hit in hits.values()}.values())
  return searches, sorted(unique, key=lambda hit: hit.verdict != "mentions_both")


def _search(
  source: TopicSearchSource,
  concepts: Sequence[ConceptTerms],
  limit: int,
  corpus_ids: set[str],
) -> tuple[SourceSearch, list[WorkRecord]]:
  """Run one source's query; record what came back, minus Corpus Papers."""
  query = build_query(source.name, concepts)
  try:
    records = source.search(query, limit=limit)
  except (httpx.HTTPError, ET.ParseError, ValueError) as error:
    logger.warning("%s search failed: %s", source.name, error)
    return (
      SourceSearch(
        source=source.name, query=query, error=f"{type(error).__name__}: {error}"
      ),
      [],
    )
  kept: list[WorkRecord] = []
  in_corpus: list[str] = []
  for record in records:
    if _corpus_work_ids([(record.doi, record.pmid, record.openalex_id)]) & corpus_ids:
      in_corpus.append(_work_id(record))
    else:
      kept.append(record)
  search = SourceSearch(
    source=source.name,
    query=query,
    returned_work_ids=[_work_id(record) for record in records],
    in_corpus_work_ids=in_corpus,
  )
  return search, kept


def _add_hit(
  hits: dict[str, SaturationHit],
  source_name: str,
  record: WorkRecord,
  concepts: Sequence[ConceptTerms],
) -> None:
  """
  Screen a returned work and add it, merging a work two sources returned.

  `hits` is indexed by every id a work carries (DOI, PMID, OpenAlex id), so a
  PubMed record and an OpenAlex record of the same work become one hit.
  """
  ids = _corpus_work_ids([(record.doi, record.pmid, record.openalex_id)])
  existing = next((hits[i] for i in ids if i in hits), None)
  if existing is not None:
    if source_name not in existing.sources:
      existing.sources.append(source_name)
    existing.doi = existing.doi or record.doi
    existing.pmid = existing.pmid or record.pmid
    existing.openalex_id = existing.openalex_id or record.openalex_id
    for work_id in ids:
      hits[work_id] = existing
    return
  verdict, reason = screen(record, concepts)
  hit = SaturationHit(
    work_id=_work_id(record),
    sources=[source_name],
    title=record.title,
    year=record.year,
    venue=record.venue,
    doi=record.doi,
    pmid=record.pmid,
    openalex_id=record.openalex_id,
    verdict=verdict,
    reason=reason,
  )
  for work_id in ids or {hit.work_id}:
    hits[work_id] = hit


def screen(
  record: WorkRecord, concepts: Sequence[ConceptTerms]
) -> tuple[ScreeningVerdict, str]:
  """
  Decide whether a work's title or abstract mentions every concept.

  A concept counts as mentioned when any of its terms appears as whole words,
  case-insensitively, allowing a plural "s" and hyphen/space variants.
  """
  text = _normalize(f"{record.title} {record.abstract}")
  found: list[str] = []
  missing: list[str] = []
  for concept in concepts:
    term = next((t for t in concept.terms if _mentions(text, t)), None)
    if term is None:
      missing.append(concept.label)
    else:
      found.append(term)
  if missing:
    labels = " or ".join(f"'{label}'" for label in missing)
    return "missing_concept", _TEXT["missing_concept"].format(labels=labels)
  terms = " and ".join(f"'{term}'" for term in found)
  return "mentions_both", _TEXT["mentions_both"].format(terms=terms)


def _normalize(value: str) -> str:
  """Lower-case and collapse hyphens and whitespace so variants compare equal."""
  return " ".join(value.lower().replace("-", " ").split())


def _mentions(text: str, term: str) -> bool:
  """Say whether a normalized text contains a term as whole words (plural ok)."""
  pattern = r"\b" + re.escape(_normalize(term)) + r"s?\b"
  return re.search(pattern, text) is not None


def _status(
  searches: Sequence[SourceSearch], hits: Sequence[SaturationHit]
) -> tuple[SaturationStatus, str]:
  """Summarize a check; it can weaken a gap, never confirm it."""
  ran = [search.source for search in searches if not search.error]
  if not ran:
    errors = "; ".join(f"{s.source}: {s.error}" for s in searches) or "no source"
    return "not_run", _TEXT["not_run"].format(errors=errors)
  screened = sum(1 for hit in hits if hit.verdict == "mentions_both")
  status: SaturationStatus = (
    "possibly_addressed" if screened else "no_closing_work_found"
  )
  return status, _TEXT[status].format(count=screened, sources=", ".join(ran))


def _work_id(record: WorkRecord) -> str:
  """Identify a returned work by DOI, else PMID, else OpenAlex id."""
  if record.doi:
    return record.doi.lower()
  if record.pmid:
    return f"pmid:{record.pmid}"
  return record.openalex_id


def _corpus_work_ids(works: Sequence[tuple[str, str, str]]) -> set[str]:
  """Return comparable DOI, PMID and OpenAlex ids for (doi, pmid, openalex) triples."""
  ids: set[str] = set()
  for doi, pmid, openalex_id in works:
    if doi:
      ids.add("doi:" + re.sub(r"^https?://doi\.org/", "", doi.strip().lower()))
    if pmid:
      ids.add(f"pmid:{pmid}")
    if openalex_id:
      ids.add("openalex:" + openalex_id.rsplit("/", 1)[-1])
  return ids


def _write_report(root: Path, report: SaturationChecksReport) -> None:
  """Write the saturation-check log under artifacts/."""
  artifacts_dir = inspect_corpus_layout(root).layout.artifacts_dir
  artifacts_dir.mkdir(parents=True, exist_ok=True)
  path = artifacts_dir / SATURATION_CHECKS_NAME
  path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
