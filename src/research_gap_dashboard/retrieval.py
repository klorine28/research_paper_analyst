"""
Retrieval Gap detection: papers the wider literature has but the Corpus lacks.

This is the one Gap Type that looks outside the Corpus (`CONTEXT.md` > Gap
Type). It ranks each work the Corpus's Papers cite by *citation overlap* — how
many Corpus Papers reference it — and surfaces the most-shared works that are
not themselves in the Corpus as candidates for the researcher to consider
adding. A work many Corpus Papers cite, yet nobody added, is a plausible hole.

The citation graph comes from OpenAlex (`referenced_works` on each Paper,
recorded at ingest with `--resolve`), so this stage reads the OpenAlex adapter
specifically; the source-adapter seam's second implementation, PubMed, resolves
DOIs at ingest instead. Ranking is deterministic — sorted by overlap, then by
OpenAlex id — so the same manifest yields the same candidates.

Because these candidates are *out of Corpus*, they carry no in-Corpus Evidence
and are written to their own artifact, clearly labelled, never mixed with the
evidence-linked gap cards (`detect`). A work already in the Corpus never
appears: candidates are filtered by OpenAlex id and, after resolution, by DOI.
"""

import logging
import re
from pathlib import Path

from pydantic import BaseModel, Field

from research_gap_dashboard.corpus_layout import inspect_corpus_layout
from research_gap_dashboard.ingest import Paper, read_manifest
from research_gap_dashboard.sources import OpenAlexAdapter

logger = logging.getLogger(__name__)

RETRIEVAL_GAPS_NAME = "retrieval_gaps.json"

# Cap on candidates written, and the smallest citation overlap that qualifies:
# a work must be cited by at least this many Corpus Papers to count as shared.
DEFAULT_LIMIT = 20
DEFAULT_MIN_OVERLAP = 2

# The banner every consumer must show, so no reader mistakes these for
# evidence-linked, in-Corpus gap cards (CODING_STANDARDS > Research integrity).
OUT_OF_CORPUS_LABEL = (
  "Out-of-corpus candidates: papers likely relevant but missing from the "
  "Corpus, found by citation overlap. They carry no in-Corpus Evidence and are "
  "signals for human judgment, not gaps the tool has confirmed."
)


class RetrievalCandidate(BaseModel):
  """One out-of-corpus paper the Corpus's Papers cite but do not include."""

  openalex_id: str
  doi: str = ""
  title: str = ""
  year: int | None = None
  venue: str = ""
  authors: list[str] = []
  cited_by_count: int = 0
  citation_overlap: int = Field(
    description="How many Corpus Papers reference this work."
  )
  citing_citation_keys: list[str] = Field(
    description="The Corpus Papers that reference it, sorted."
  )


class RetrievalGapsReport(BaseModel):
  """What Retrieval Gap detection produced: out-of-corpus candidates, ranked."""

  corpus_root: Path
  source: str = Field(description="The scholarly source the ranking read.")
  label: str = OUT_OF_CORPUS_LABEL
  corpus_paper_count: int = Field(description="The denominator: Papers in the Corpus.")
  coupled_paper_count: int = Field(
    description="Papers with a resolved citation list to rank over."
  )
  min_overlap: int = Field(description="Least citation overlap a candidate needed.")
  limit: int = Field(description="Cap on candidates written.")
  candidates: list[RetrievalCandidate]


def detect_retrieval_gaps(
  root: Path,
  adapter: OpenAlexAdapter,
  *,
  limit: int = DEFAULT_LIMIT,
  min_overlap: int = DEFAULT_MIN_OVERLAP,
) -> RetrievalGapsReport:
  """
  Rank works the Corpus cites by citation overlap and write the top candidates.

  Reads the manifest, tallies every referenced work across the Corpus's Papers,
  drops works already in the Corpus (by OpenAlex id), keeps those cited by at
  least `min_overlap` Papers, and resolves the top `limit` against OpenAlex,
  dropping any that turn out to be in the Corpus by DOI.
  """
  manifest = read_manifest(root)
  corpus_openalex_ids = {p.openalex_id for p in manifest.papers if p.openalex_id}
  corpus_dois = {_normalize_doi(p.doi) for p in manifest.papers if p.doi}
  coupled = [p for p in manifest.papers if p.referenced_works]
  ranked = _rank_cited_works(coupled, corpus_openalex_ids, min_overlap)

  candidates: list[RetrievalCandidate] = []
  for work_id, keys in ranked:
    if len(candidates) >= limit:
      break
    candidate = _resolve_candidate(adapter, work_id, keys, corpus_dois)
    if candidate is not None:
      candidates.append(candidate)

  report = RetrievalGapsReport(
    corpus_root=root,
    source=adapter.name,
    corpus_paper_count=len(manifest.papers),
    coupled_paper_count=len(coupled),
    min_overlap=min_overlap,
    limit=limit,
    candidates=candidates,
  )
  _write_report(root, report)
  logger.info(
    "Found %d out-of-corpus candidates from %d cited works over %d Papers.",
    len(candidates),
    len(ranked),
    len(coupled),
  )
  return report


def _rank_cited_works(
  coupled: list[Paper], corpus_openalex_ids: set[str], min_overlap: int
) -> list[tuple[str, set[str]]]:
  """Tally each non-Corpus cited work by the Papers citing it, best overlap first."""
  citing: dict[str, set[str]] = {}
  for paper in coupled:
    for work_id in set(paper.referenced_works):
      if work_id in corpus_openalex_ids:
        continue
      citing.setdefault(work_id, set()).add(paper.citation_key)
  return sorted(
    (item for item in citing.items() if len(item[1]) >= min_overlap),
    key=lambda item: (-len(item[1]), item[0]),
  )


def _resolve_candidate(
  adapter: OpenAlexAdapter,
  work_id: str,
  citing_keys: set[str],
  corpus_dois: set[str],
) -> RetrievalCandidate | None:
  """Resolve one cited work, unless it turns out to already be in the Corpus."""
  record = adapter.fetch_work(work_id)
  if record is None:
    logger.warning("Skipping %s: OpenAlex did not resolve it.", work_id)
    return None
  if record.doi and _normalize_doi(record.doi) in corpus_dois:
    logger.info("Skipping %s: already in the Corpus by DOI.", work_id)
    return None
  return RetrievalCandidate(
    openalex_id=record.openalex_id or work_id,
    doi=record.doi,
    title=record.title,
    year=record.year,
    venue=record.venue,
    authors=record.authors,
    cited_by_count=record.cited_by_count,
    citation_overlap=len(citing_keys),
    citing_citation_keys=sorted(citing_keys),
  )


def read_retrieval_gaps(root: Path) -> RetrievalGapsReport:
  """Read the RetrievalGaps artifact a previous run wrote."""
  path = inspect_corpus_layout(root).layout.artifacts_dir / RETRIEVAL_GAPS_NAME
  return RetrievalGapsReport.model_validate_json(path.read_text(encoding="utf-8"))


def _normalize_doi(doi: str) -> str:
  """Reduce a DOI to a comparable form: bare, lower-cased, prefix stripped."""
  return re.sub(r"^https?://doi\.org/", "", doi.strip().lower())


def _write_report(root: Path, report: RetrievalGapsReport) -> None:
  """Write the RetrievalGaps artifact under artifacts/."""
  artifacts_dir = inspect_corpus_layout(root).layout.artifacts_dir
  artifacts_dir.mkdir(parents=True, exist_ok=True)
  path = artifacts_dir / RETRIEVAL_GAPS_NAME
  path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
