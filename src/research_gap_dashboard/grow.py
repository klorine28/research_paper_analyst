"""
The Grow path: add a suggested Paper to a Corpus, or record why it can't be.

A Retrieval Gap candidate and a saturation-check hit are both out-of-Corpus
works the researcher may want in the Corpus (ADR 0009 §11-12). They grow it
through this one path, so a work suggested by both routes is one record and is
never added twice:

1. its open-access PDF is fetched when OpenAlex knows one;
2. otherwise the researcher uploads the PDF;
3. otherwise the researcher marks it **unavailable**, with a reason and date,
   which an upload later reverses.

How a Paper entered the Corpus, and which suggested papers could not be had,
is recorded in the Corpus entry log (ADR 0010 §4) through the dashboard's
`entry_log` functions: an add logs the Paper with its route and date, and
"unavailable" lives only there.

A Paper is never added metadata-only: without full text it cannot carry
Evidence. The paper-list entry is written only together with a PDF that is
really a PDF, and only if ingest's own pairing check then pairs them; anything
else is rolled back. Until its PDF arrives a suggestion is **pending**: it is
in neither the paper list nor `papers/`, so it is outside every denominator.
The 10-75 Paper envelope is checked before anything is placed (ADR 0010 §5).

`suggested_papers.json` at the Corpus root keeps what the Grow path itself
still needs: pending suggestions and the added ones (so a work is never added
twice), with route(s) and dated history. Papers an older file marks
unavailable are carried into the entry log when it is read and dropped from
it on the next write.
Re-running the stages after an add is the caller's job (the dashboard chains
them through its runner, ADR 0007).
"""

import logging
import re
import unicodedata
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import httpx
from pydantic import BaseModel, Field

from research_gap_dashboard.corpus_layout import inspect_corpus_layout
from research_gap_dashboard.dashboard import entry_log
from research_gap_dashboard.dashboard.entry_log import (
  PaperIdentity,
  UnavailablePaper,
  adopt_legacy_unavailable,
  record_entries,
  suggestion_route,
)
from research_gap_dashboard.ingest import (
  MAX_PAPERS,
  CorpusLayoutError,
  check_corpus,
  read_manifest,
  read_paper_list_entries,
)
from research_gap_dashboard.sources import (
  BytesFetch,
  OpenAccessWork,
  OpenAlexAdapter,
  WorkRecord,
)

logger = logging.getLogger(__name__)

SUGGESTED_PAPERS_NAME = "suggested_papers.json"
BIB_NAME = "corpus.bib"
_PDF_MAGIC = b"%PDF-"

Route = Literal["retrieval", "saturation"]
# "unavailable" is only read from older files: the entry log owns that status.
SuggestionStatus = Literal["pending", "added", "unavailable"]
AddStatus = Literal["added", "pending", "already_in_corpus"]
PdfSource = Literal["", "open_access", "upload"]


class GrowError(Exception):
  """A suggested Paper could not be added or recorded, with a reason to show."""


class WorkRef(BaseModel):
  """The identifiers a suggestion carries: any of DOI, PMID and OpenAlex id."""

  doi: str = ""
  pmid: str = ""
  openalex_id: str = ""

  def ids(self) -> set[str]:
    """Return comparable ids, so either route's ids match the same work."""
    return work_ids(self.doi, self.pmid, self.openalex_id)

  def lookup_ref(self) -> str:
    """Return the OpenAlex work reference: DOI, else PMID, else OpenAlex id."""
    if self.doi:
      return f"doi:{_bare_doi(self.doi)}"
    if self.pmid:
      return f"pmid:{self.pmid}"
    return self.openalex_id.rsplit("/", 1)[-1]


class SuggestionEvent(BaseModel):
  """One dated change of a suggestion's status."""

  at: datetime
  status: SuggestionStatus
  route: Route
  note: str = ""


# The Grow path updates a record field by field as it learns more about the work.
class SuggestedPaper(BaseModel):  # pylint: disable=too-many-instance-attributes
  """One out-of-Corpus work offered for the Corpus, and what became of it."""

  doi: str = ""
  pmid: str = ""
  openalex_id: str = ""
  title: str = ""
  authors: list[str] = []
  year: int | None = None
  venue: str = ""
  routes: list[Route] = Field(description="Every route that suggested it, first first.")
  status: SuggestionStatus
  reason: str = Field(default="", description="Why it is pending or unavailable.")
  citation_key: str = ""
  pdf_name: str = ""
  pdf_source: PdfSource = ""
  pdf_url: str = ""
  updated_at: datetime
  history: list[SuggestionEvent] = []

  def ids(self) -> set[str]:
    """Return comparable ids of this suggestion."""
    return work_ids(self.doi, self.pmid, self.openalex_id)


class SuggestedPapers(BaseModel):
  """A Corpus's record of suggested Papers: added and pending."""

  label: str = (
    "Papers suggested from outside the Corpus (Retrieval Gaps and saturation "
    "hits). Only 'added' ones are Papers; pending ones are in no denominator. "
    "Papers marked unavailable are in entry_log.json."
  )
  papers: list[SuggestedPaper] = []


class AddOutcome(BaseModel):
  """What one add attempt did, as the CLI prints it for the dashboard."""

  status: AddStatus
  message: str
  paper: SuggestedPaper | None = None


def work_ids(doi: str, pmid: str, openalex_id: str) -> set[str]:
  """Return comparable `doi:`, `pmid:` and `openalex:` ids for one work."""
  ids: set[str] = set()
  if doi:
    ids.add("doi:" + _bare_doi(doi))
  if pmid:
    ids.add(f"pmid:{pmid.strip()}")
  if openalex_id:
    ids.add("openalex:" + openalex_id.strip().rsplit("/", 1)[-1].upper())
  return ids


def _bare_doi(doi: str) -> str:
  return re.sub(r"^https?://(dx\.)?doi\.org/", "", doi.strip().lower())


def suggested_papers_path(root: Path) -> Path:
  """Where a Corpus keeps its suggested-Papers record."""
  return root / SUGGESTED_PAPERS_NAME


def read_suggested_papers(root: Path) -> SuggestedPapers:
  """
  Read the Corpus's suggested-Papers record, or an empty one.

  Records an older file marks unavailable are carried into the entry log and
  left out, so the next write drops them.
  """
  path = suggested_papers_path(root)
  if not path.is_file():
    return SuggestedPapers()
  ledger = SuggestedPapers.model_validate_json(path.read_text(encoding="utf-8"))
  if any(paper.status == "unavailable" for paper in ledger.papers):
    adopt_legacy_unavailable(root)
    ledger.papers = [paper for paper in ledger.papers if paper.status != "unavailable"]
  return ledger


def _write(root: Path, ledger: SuggestedPapers) -> None:
  suggested_papers_path(root).write_text(
    ledger.model_dump_json(indent=2) + "\n", encoding="utf-8"
  )


def add_suggested_paper(  # pylint: disable=too-many-arguments,too-many-locals
  root: Path,
  ref: WorkRef,
  route: Route,
  *,
  openalex: OpenAlexAdapter,
  download: BytesFetch,
  upload: bytes | None = None,
  title: str = "",
  year: int | None = None,
  now: datetime | None = None,
) -> AddOutcome:
  """
  Add a suggested work to the Corpus from its open-access PDF or an upload.

  Returns "added" once the PDF and its paper-list entry pair up, "pending" when
  no PDF could be had (the researcher can upload one or mark it unavailable),
  and "already_in_corpus" when the work is in the Corpus already. Raises
  `GrowError` when the upload is not a PDF, the Corpus is full, or the new
  Paper would not pair; nothing is left placed then.
  """
  now = now or datetime.now(UTC)
  if not ref.ids():
    raise GrowError("A suggested paper needs a DOI, PMID or OpenAlex id.")
  if upload is not None and not upload.startswith(_PDF_MAGIC):
    raise GrowError("The uploaded file is not a PDF; nothing was added.")
  ledger = read_suggested_papers(root)
  record = _find(ledger, ref.ids())
  if (record is not None and record.status == "added") or ref.ids() & _corpus_ids(root):
    return AddOutcome(
      status="already_in_corpus",
      message="This paper is already in the Corpus; it was not added again.",
      paper=record,
    )
  paired = _paired_count(root)
  if paired >= MAX_PAPERS:
    raise GrowError(
      f"This Corpus already has {paired} Papers; v1 supports at most "
      f"{MAX_PAPERS}, so no more can be added."
    )

  work, lookup_note = _look_up(openalex, ref)
  record = _merge(ledger, record, ref, route, work, title=title, year=year, now=now)
  if upload is not None:
    pdf, source, url, note = upload, "upload", "", ""
  else:
    pdf, url, note = _fetch_open_access(work, download)
    source = "open_access"
  if pdf is None:
    reason = lookup_note or note
    record.status = "pending"
    record.reason = reason
    record.updated_at = now
    record.history.append(
      SuggestionEvent(at=now, status=record.status, route=route, note=reason)
    )
    _write(root, ledger)
    logger.info("No PDF for %s yet: %s", ref.lookup_ref(), reason)
    return AddOutcome(
      status="pending",
      message=f"No open-access PDF could be fetched ({reason}). Upload the PDF, "
      "or mark the paper unavailable.",
      paper=record,
    )

  _place(root, record, pdf, before=paired)
  record.status = "added"
  record.reason = ""
  record.pdf_source = source
  record.pdf_url = url
  record.updated_at = now
  record.history.append(
    SuggestionEvent(
      at=now, status="added", route=route, note=f"PDF from {source.replace('_', ' ')}"
    )
  )
  _write(root, ledger)
  record_entries(
    root,
    [
      PaperIdentity(
        citation_key=record.citation_key,
        doi=record.doi,
        pmid=record.pmid,
        openalex_id=record.openalex_id,
      )
    ],
    suggestion_route(route),
    at=now,
  )
  logger.info("Added %s to the Corpus as %s", ref.lookup_ref(), record.citation_key)
  return AddOutcome(
    status="added",
    message=f"Added as '{record.citation_key}'. Re-run the pipeline to include it.",
    paper=record,
  )


def mark_unavailable(  # pylint: disable=too-many-arguments
  root: Path,
  ref: WorkRef,
  route: Route,
  reason: str,
  *,
  title: str = "",
  year: int | None = None,
  now: datetime | None = None,
) -> UnavailablePaper:
  """
  Record in the entry log that a suggested work can't be obtained, and why.

  It stops being offered as a suggestion and leaves the suggested-Papers
  record, where it was pending; uploading its PDF later adds it.
  """
  now = now or datetime.now(UTC)
  reason = reason.strip()
  if not reason:
    raise GrowError("Say why the paper is unavailable: the reason is disclosed.")
  if not ref.ids():
    raise GrowError("A suggested paper needs a DOI, PMID or OpenAlex id.")
  ledger = read_suggested_papers(root)
  record = _find(ledger, ref.ids())
  if (record is not None and record.status == "added") or ref.ids() & _corpus_ids(root):
    raise GrowError("This paper is already in the Corpus.")
  record = _merge(ledger, record, ref, route, None, title=title, year=year, now=now)
  ledger.papers.remove(record)
  marked = entry_log.mark_unavailable(
    root,
    doi=record.doi,
    pmid=record.pmid,
    openalex_id=record.openalex_id,
    title=record.title,
    route=suggestion_route(route),
    reason=reason,
    at=now,
  )
  _write(root, ledger)
  logger.info("Marked %s unavailable: %s", ref.lookup_ref(), reason)
  return marked


def _find(ledger: SuggestedPapers, ids: set[str]) -> SuggestedPaper | None:
  return next((paper for paper in ledger.papers if paper.ids() & ids), None)


def _corpus_ids(root: Path) -> set[str]:
  """Ids of the works the Corpus lists or holds, from the paper list and manifest."""
  ids: set[str] = set()
  try:
    entries = read_paper_list_entries(root)
  except CorpusLayoutError:
    entries = []
  for entry in entries:
    ids |= work_ids(entry.doi, "", "")
  try:
    manifest = read_manifest(root)
  except FileNotFoundError:
    return ids
  for paper in manifest.papers:
    ids |= work_ids(paper.doi, "", paper.openalex_id)
  return ids


def _paired_count(root: Path) -> int:
  try:
    return len(check_corpus(root).paired)
  except CorpusLayoutError:
    return 0


def _look_up(
  openalex: OpenAlexAdapter, ref: WorkRef
) -> tuple[OpenAccessWork | None, str]:
  """Look the work up on OpenAlex; a dead network is a note, not a failure."""
  try:
    work = openalex.open_access(ref.lookup_ref())
  except (httpx.HTTPError, OSError, ValueError) as error:
    logger.warning("Could not look %s up on OpenAlex: %s", ref.lookup_ref(), error)
    return None, f"could not reach OpenAlex: {error}"
  if work is None:
    return None, "OpenAlex does not know this work"
  return work, ""


def _fetch_open_access(
  work: OpenAccessWork | None, download: BytesFetch
) -> tuple[bytes | None, str, str]:
  """Download the first open-access URL that really serves a PDF."""
  if work is None:
    return None, "", ""
  if not work.pdf_urls:
    return None, "", "no open-access copy is known"
  problems: list[str] = []
  for url in work.pdf_urls:
    try:
      body = download(url)
    except (httpx.HTTPError, OSError) as error:
      problems.append(f"{url}: {error}")
      continue
    if body is not None and body.startswith(_PDF_MAGIC):
      return body, url, ""
    problems.append(f"{url}: not a PDF")
  logger.info("No open-access URL served a PDF: %s", "; ".join(problems))
  return None, "", "no open-access location served a PDF"


def _merge(  # pylint: disable=too-many-arguments
  ledger: SuggestedPapers,
  record: SuggestedPaper | None,
  ref: WorkRef,
  route: Route,
  work: OpenAccessWork | None,
  *,
  title: str,
  year: int | None,
  now: datetime,
) -> SuggestedPaper:
  """Fold what is known about the work into its record, creating it if new."""
  if record is None:
    record = SuggestedPaper(routes=[route], status="pending", updated_at=now)
    ledger.papers.append(record)
  elif route not in record.routes:
    record.routes.append(route)
  found = work.record if work is not None else WorkRecord(doi="")
  record.doi = _bare_doi(found.doi or ref.doi or record.doi)
  record.pmid = found.pmid or ref.pmid or record.pmid
  record.openalex_id = found.openalex_id or ref.openalex_id or record.openalex_id
  record.title = found.title or record.title or title
  record.authors = found.authors or record.authors
  record.year = found.year or record.year or year
  record.venue = found.venue or record.venue
  return record


def _place(  # pylint: disable=too-many-locals
  root: Path, record: SuggestedPaper, pdf: bytes, *, before: int
) -> None:
  """
  Write the PDF and its paper-list entry, keeping them only if they pair.

  Ingest pairs by DOI in the file name, then by citation key; a new name could
  in principle steal another entry's PDF, so the pairing check is the judge and
  a pairing that did not grow by exactly this Paper is rolled back.
  """
  report = inspect_corpus_layout(root)
  paper_list = report.paper_list_path or root / BIB_NAME
  is_bibtex = paper_list.suffix.lower() == ".bib"
  if not is_bibtex and not record.doi:
    raise GrowError(
      f"This Corpus's paper list ({paper_list.name}) lists DOIs only, and this "
      "paper has no DOI; nothing was added."
    )
  taken = [path.stem for path in report.pdf_paths]
  if is_bibtex:
    key = _citation_key(record, root, taken)
    pdf_name = f"{key}.pdf"
    line = _bibtex_entry(key, record)
  else:
    key = record.doi
    pdf_name = f"{re.sub(r'[^A-Za-z0-9._-]', '_', record.doi)}.pdf"
    line = record.doi
  pdf_path = report.layout.papers_dir / pdf_name
  if pdf_path.exists():
    raise GrowError(f"papers/{pdf_name} already exists; nothing was added.")

  original = paper_list.read_text(encoding="utf-8") if paper_list.is_file() else None
  report.layout.papers_dir.mkdir(parents=True, exist_ok=True)
  pdf_path.write_bytes(pdf)
  current = (original or "").rstrip("\n")
  separator = ("\n\n" if is_bibtex else "\n") if current else ""
  paper_list.write_text(current + separator + line + "\n", encoding="utf-8")

  check = check_corpus(root)
  if len(check.paired) == before + 1 and key in check.paired:
    record.citation_key = key
    record.pdf_name = pdf_name
    return
  pdf_path.unlink()
  if original is None:
    paper_list.unlink()
  else:
    paper_list.write_text(original, encoding="utf-8")
  raise GrowError(
    "The new PDF did not pair with its paper-list entry, so nothing was added."
  )


def _citation_key(record: SuggestedPaper, root: Path, pdf_stems: list[str]) -> str:
  """
  Make a citation key (surname, year, first title word) no other name contains.

  Ingest matches a PDF to an entry when the entry's key appears in the file
  name, so the key must not appear in any other PDF name and no existing key
  may appear in it.
  """
  surname = _slug(record.authors[0].split()[-1]) if record.authors else ""
  word = next(
    (w for w in (_slug(w) for w in record.title.split()) if len(w) >= 4), "paper"
  )
  base = f"{surname or 'suggested'}{record.year or ''}{word}"
  existing = [_slug(key) for key in _listed_keys(root)]
  stems = [_slug(stem) for stem in pdf_stems]
  for suffix in ("", *"abcdefghijklmnopqrstuvwxyz"):
    key = base + suffix
    if any(key in stem for stem in stems) or any(k and k in key for k in existing):
      continue
    return key
  raise GrowError("Could not make a citation key that pairs uniquely.")


def _listed_keys(root: Path) -> list[str]:
  try:
    return [entry.citation_key for entry in read_paper_list_entries(root)]
  except CorpusLayoutError:
    return []


def _slug(value: str) -> str:
  """Lower-case ASCII letters and digits only."""
  ascii_value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
  return re.sub(r"[^a-z0-9]", "", ascii_value.lower())


def _bibtex_entry(key: str, record: SuggestedPaper) -> str:
  """Write a BibTeX entry, dropping braces and backslashes that would break it."""
  fields = [
    ("title", record.title),
    ("author", " and ".join(record.authors)),
    ("year", str(record.year) if record.year else ""),
    ("journal", record.venue),
    ("doi", record.doi),
  ]
  lines = [
    f"  {name} = {{{_bibtex_value(value)}}}" for name, value in fields if value.strip()
  ]
  return f"@article{{{key},\n" + ",\n".join(lines) + "\n}"


def _bibtex_value(value: str) -> str:
  return " ".join(re.sub(r"[{}\\]", "", value).split())
