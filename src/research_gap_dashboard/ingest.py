"""
Ingest: turn a corpus directory into a CorpusManifest artifact.

Reads the paper list (BibTeX or DOI list) and the PDFs in `papers/`, pairs
them up, and writes `artifacts/corpus-manifest.json`. Nothing here touches
the network: DOI resolution against a scholarly API is a later stage.
"""

import json
import logging
import re
from pathlib import Path

from bibtexparser.entrypoint import parse_string
from bibtexparser.model import Entry
from pydantic import BaseModel

from research_gap_dashboard.corpus_layout import inspect_corpus_layout
from research_gap_dashboard.sources import SourceAdapter

logger = logging.getLogger(__name__)

MANIFEST_NAME = "corpus-manifest.json"
MIN_PAPERS = 10
MAX_PAPERS = 75


class CorpusSizeError(Exception):
  """Raised when a Corpus falls outside the supported 10-75 Paper envelope."""


class CorpusLayoutError(Exception):
  """Raised when the corpus directory does not follow the layout convention."""


class PaperListEntry(BaseModel):
  """One entry of the paper list, before it is paired with a PDF."""

  citation_key: str
  doi: str
  title: str = ""
  authors: list[str] = []
  year: int | None = None
  journal: str = ""


class UnmatchedEntry(BaseModel):
  """A paper-list entry that did not become a Paper, and why."""

  entry: PaperListEntry
  reason: str


class Paper(BaseModel):
  """One Paper in the Corpus: a bibliographic record plus its full text."""

  citation_key: str
  doi: str
  title: str = ""
  authors: list[str] = []
  year: int | None = None
  journal: str = ""
  pdf_path: Path
  openalex_id: str = ""
  referenced_works: list[str] = []
  cited_by_count: int = 0
  resolution_error: str | None = None


class CorpusManifest(BaseModel):
  """What ingest found: the Corpus, and everything it could not place in it."""

  corpus_root: Path
  papers: list[Paper]
  unmatched_entries: list[UnmatchedEntry]
  orphan_pdfs: list[Path]


def ingest_corpus(root: Path, adapter: SourceAdapter | None = None) -> CorpusManifest:
  """
  Build the CorpusManifest for a corpus directory and write it to artifacts/.

  When an `adapter` is given, each Paper's DOI is resolved to canonical
  metadata; without one, Papers keep the metadata read from the paper list.
  """
  report = inspect_corpus_layout(root)
  if report.paper_list_path is None:
    raise CorpusLayoutError(
      "\n".join(problem.message for problem in report.problems)
      or f"Corpus directory '{root}' has no paper list."
    )

  entries = _read_paper_list(report.paper_list_path)
  manifest = _match(root, entries, report.pdf_paths)
  if adapter is not None:
    _resolve(manifest, adapter)
  _check_envelope(manifest)

  artifact_path = report.layout.artifacts_dir / MANIFEST_NAME
  artifact_path.parent.mkdir(parents=True, exist_ok=True)
  artifact_path.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
  logger.info(
    "Ingested %d Papers (%d unmatched entries, %d orphan PDFs) into %s",
    len(manifest.papers),
    len(manifest.unmatched_entries),
    len(manifest.orphan_pdfs),
    artifact_path,
  )
  return manifest


def _resolve(manifest: CorpusManifest, adapter: SourceAdapter) -> None:
  """Enrich each Paper with canonical metadata, flagging DOIs that don't resolve."""
  for paper in manifest.papers:
    record = adapter.resolve(paper.doi)
    if record is None:
      paper.resolution_error = f"{adapter.name} could not resolve DOI {paper.doi}"
      continue
    paper.title = record.title or paper.title
    paper.year = record.year if record.year is not None else paper.year
    paper.journal = record.venue or paper.journal
    paper.authors = record.authors or paper.authors
    paper.openalex_id = record.openalex_id
    paper.referenced_works = record.referenced_works
    paper.cited_by_count = record.cited_by_count
    paper.resolution_error = None


def read_manifest(root: Path) -> CorpusManifest:
  """Read the CorpusManifest a previous ingest run wrote."""
  path = inspect_corpus_layout(root).layout.artifacts_dir / MANIFEST_NAME
  return CorpusManifest.model_validate(json.loads(path.read_text(encoding="utf-8")))


def _read_paper_list(path: Path) -> list[PaperListEntry]:
  """Parse a BibTeX file or a DOI list into paper-list entries."""
  text = path.read_text(encoding="utf-8")
  if path.suffix.lower() == ".bib":
    return [_entry_from_bibtex(block) for block in parse_string(text).entries]
  return [
    PaperListEntry(citation_key=line.strip(), doi=line.strip())
    for line in text.splitlines()
    if line.strip() and not line.startswith("#")
  ]


def _entry_from_bibtex(block: Entry) -> PaperListEntry:
  """Convert one parsed BibTeX entry into a paper-list entry."""
  fields = {name: field.value for name, field in block.fields_dict.items()}
  year = fields.get("year", "")
  authors = [
    author.strip()
    for author in fields.get("author", "").split(" and ")
    if author.strip()
  ]
  return PaperListEntry(
    citation_key=block.key,
    doi=fields.get("doi", "").strip(),
    title=fields.get("title", "").strip(),
    authors=authors,
    year=int(year) if year.strip().isdigit() else None,
    journal=fields.get("journal", "").strip(),
  )


def _match(
  root: Path, entries: list[PaperListEntry], pdf_paths: list[Path]
) -> CorpusManifest:
  """Pair entries with PDFs by DOI, then by citation key, reporting the leftovers."""
  remaining = {path: _normalize(path.stem) for path in pdf_paths}
  papers: list[Paper] = []
  unmatched: list[UnmatchedEntry] = []

  for entry in entries:
    if not entry.doi:
      unmatched.append(UnmatchedEntry(entry=entry, reason="no DOI in the paper list"))
      continue
    match = _find_pdf(entry, remaining)
    if match is None:
      unmatched.append(UnmatchedEntry(entry=entry, reason="no matching PDF"))
      continue
    del remaining[match]
    papers.append(Paper(**entry.model_dump(), pdf_path=match))

  return CorpusManifest(
    corpus_root=root,
    papers=papers,
    unmatched_entries=unmatched,
    orphan_pdfs=sorted(remaining),
  )


def _find_pdf(entry: PaperListEntry, candidates: dict[Path, str]) -> Path | None:
  """Return the PDF whose file name carries the entry's DOI, or else its key."""
  for needle in (_normalize(entry.doi), _normalize(entry.citation_key)):
    if not needle:
      continue
    hits = [path for path, name in candidates.items() if needle in name]
    if len(hits) == 1:
      return hits[0]
  return None


def _normalize(value: str) -> str:
  """Reduce a DOI, key, or file name to comparable alphanumerics."""
  return re.sub(r"[^a-z0-9]", "", value.lower())


def _check_envelope(manifest: CorpusManifest) -> None:
  """Refuse a Corpus outside the supported size envelope, naming the count."""
  count = len(manifest.papers)
  if MIN_PAPERS <= count <= MAX_PAPERS:
    return
  raise CorpusSizeError(
    f"This Corpus has {count} Papers with both a paper-list entry and a PDF; "
    f"v1 supports {MIN_PAPERS}-{MAX_PAPERS}. "
    f"Unmatched entries: {len(manifest.unmatched_entries)}; "
    f"orphan PDFs: {len(manifest.orphan_pdfs)}."
  )
