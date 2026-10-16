"""
Adding Papers to a Corpus from the dashboard: place the inputs, check the pairing.

ADR 0007 lets the dashboard place a Corpus's *inputs* on disk: uploaded PDFs go
into `papers/`, and an uploaded paper list (BibTeX or DOI list) becomes the
Corpus's paper list or is appended to it. Nothing here writes the manifest or
any other artifact; that is still `ingest`'s job, run through `pipeline_runner`.

Before ingesting, the dashboard asks the pipeline how the paper list and the
PDFs pair up (`ingest --check`, which writes nothing) and reads back its JSON:
the Papers that paired, the entries with no PDF, the PDFs with no entry, and
whether the Corpus is within the 10-75 Paper envelope. Pairing stays the
pipeline's logic; this module only restates the read model (ADR 0002).

Like the other dashboard seams this is plain functions with no Streamlit
dependency. An upload is taken as `(file name, content)` pairs.
"""

import logging
from collections.abc import Sequence
from pathlib import Path

from bibtexparser.entrypoint import parse_string
from bibtexparser.model import Field
from pydantic import BaseModel, ConfigDict, ValidationError

from research_gap_dashboard.dashboard import pipeline_runner
from research_gap_dashboard.dashboard.artifacts import PAPERS_DIR, has_manifest
from research_gap_dashboard.dashboard.entry_log import (
  EntryRoute,
  PaperIdentity,
  record_entries,
)
from research_gap_dashboard.dashboard.pipeline_runner import RunOutcome, RunResult

logger = logging.getLogger(__name__)

# Restated from the Corpus layout convention (`docs/corpus-layout.md`): the
# first `*.bib` at the root wins, otherwise `dois.txt`. A first BibTeX upload is
# saved under BIB_NAME.
BIB_NAME = "corpus.bib"
DOI_LIST_NAME = "dois.txt"
_PDF_MAGIC = b"%PDF-"

CHECK_ARGS = ("--check",)


class UploadError(ValueError):
  """An uploaded paper list could not be placed, with a reason to show."""


class RefusedFile(BaseModel):
  """An uploaded file that was not placed, and why."""

  name: str
  reason: str


class PdfPlacement(BaseModel):
  """What became of a batch of uploaded PDFs."""

  added: list[str] = []
  replaced: list[str] = []
  refused: list[RefusedFile] = []


class PaperListPlacement(BaseModel):
  """What an uploaded paper list added to the Corpus's paper list."""

  path: Path
  added: int
  already_listed: list[str] = []


class _CheckModel(BaseModel):
  model_config = ConfigDict(extra="ignore")


class PairingEntry(_CheckModel):
  """A paper-list entry, as the pairing check reports it."""

  citation_key: str
  doi: str = ""
  title: str = ""


class UnpairedEntry(_CheckModel):
  """A paper-list entry with no PDF."""

  entry: PairingEntry


class PairingCheck(_CheckModel):
  """The dashboard's read model of `ingest --check`'s JSON."""

  paired: list[str]
  unmatched_entries: list[UnpairedEntry]
  orphan_pdfs: list[Path]
  min_papers: int
  max_papers: int
  within_envelope: bool


class PairingResult(BaseModel):
  """A pairing check's run, and the check it printed when it got that far."""

  run: RunResult
  check: PairingCheck | None = None


def place_pdfs(corpus_root: Path, files: Sequence[tuple[str, bytes]]) -> PdfPlacement:
  """
  Write uploaded PDFs into the Corpus's `papers/`, refusing anything else.

  Only the file name is kept, so an upload can't land outside `papers/`. A PDF
  with the same name as one already there replaces it (a corrected copy).
  """
  papers_dir = corpus_root / PAPERS_DIR
  papers_dir.mkdir(parents=True, exist_ok=True)
  placement = PdfPlacement()
  for raw_name, content in files:
    name = Path(raw_name.replace("\\", "/")).name
    reason = _pdf_refusal(name, content)
    if reason is not None:
      placement.refused.append(RefusedFile(name=raw_name, reason=reason))
      continue
    target = papers_dir / name
    (placement.replaced if target.exists() else placement.added).append(name)
    target.write_bytes(content)
  logger.info(
    "Placed %d PDFs (%d replaced, %d refused) in %s",
    len(placement.added) + len(placement.replaced),
    len(placement.replaced),
    len(placement.refused),
    papers_dir,
  )
  return placement


def _pdf_refusal(name: str, content: bytes) -> str | None:
  """Say why an upload is not a PDF that may go in `papers/`, or None if it is."""
  if not name or name.startswith("."):
    return "it has no usable file name"
  if not name.lower().endswith(".pdf"):
    return "only PDF files go in papers/"
  if not content.startswith(_PDF_MAGIC):
    return "its content is not a PDF"
  return None


def current_paper_list(corpus_root: Path) -> Path | None:
  """Return the Corpus's paper list as ingest would pick it, or None."""
  bibs = sorted(corpus_root.glob("*.bib"))
  if bibs:
    return bibs[0]
  dois = corpus_root / DOI_LIST_NAME
  return dois if dois.is_file() else None


def place_paper_list(
  corpus_root: Path, name: str, content: bytes
) -> PaperListPlacement:
  """
  Make an uploaded BibTeX file or DOI list the Corpus's paper list, or add to it.

  Entries already listed (same citation key or DOI) are skipped, not repeated.
  An upload of the other kind than the existing list is refused: a `.bib` next
  to `dois.txt` would silently replace it, as BibTeX wins.

  The newly listed entries are written to the Corpus entry log: as the initial
  import while the Corpus has never been ingested, as added by upload after.
  """
  try:
    text = content.decode("utf-8")
  except UnicodeDecodeError as error:
    raise UploadError(f"'{name}' is not a UTF-8 text file.") from error
  suffix = Path(name).suffix.lower()
  if suffix not in (".bib", ".txt"):
    raise UploadError(
      f"'{name}' is not a paper list: upload a BibTeX file (.bib) or a DOI list "
      "(.txt, one DOI per line)."
    )
  existing = current_paper_list(corpus_root)
  if existing is not None and (existing.suffix.lower() == ".bib") != (suffix == ".bib"):
    kind = "a BibTeX file" if existing.suffix.lower() == ".bib" else "a DOI list"
    raise UploadError(
      f"This Corpus's paper list ({existing.name}) is {kind}; upload the same "
      "kind of list to add to it."
    )
  route = EntryRoute.UPLOADED if has_manifest(corpus_root) else EntryRoute.IMPORTED
  if suffix == ".bib":
    placement, fresh = _place_bibtex(corpus_root, existing, name, text)
  else:
    placement, fresh = _place_doi_list(corpus_root, existing, name, text)
  record_entries(corpus_root, fresh, route)
  return placement


def _place_bibtex(
  corpus_root: Path, existing: Path | None, name: str, text: str
) -> tuple[PaperListPlacement, list[PaperIdentity]]:
  """Append the uploaded BibTeX entries that are not listed yet, and name them."""
  uploaded = parse_string(text)
  if uploaded.failed_blocks:
    first = (uploaded.failed_blocks[0].raw or "").strip().splitlines()[:1]
    raise UploadError(
      f"'{name}' has {len(uploaded.failed_blocks)} BibTeX block(s) that could "
      f"not be read (a repeated key or a syntax error), starting at: "
      f"{first[0] if first else '(empty)'}"
    )
  if not uploaded.entries:
    raise UploadError(f"'{name}' holds no BibTeX entries.")
  target = existing or corpus_root / BIB_NAME
  keys: set[str] = set()
  dois: set[str] = set()
  if existing is not None:
    for entry in parse_string(existing.read_text(encoding="utf-8")).entries:
      keys.add(entry.key)
      dois.add(_doi_of(entry.fields_dict))
  dois.discard("")
  fresh: list[str] = []
  identities: list[PaperIdentity] = []
  already: list[str] = []
  for entry in uploaded.entries:
    doi = _doi_of(entry.fields_dict)
    if entry.key in keys or (doi and doi in dois):
      already.append(entry.key)
      continue
    keys.add(entry.key)
    if doi:
      dois.add(doi)
    fresh.append((entry.raw or "").strip())
    identities.append(PaperIdentity(citation_key=entry.key, doi=doi))
  _append(target, "\n\n".join(fresh), blank_line=True)
  placement = PaperListPlacement(path=target, added=len(fresh), already_listed=already)
  return placement, identities


def _doi_of(fields: dict[str, Field]) -> str:
  field = fields.get("doi")
  return str(field.value).strip().lower() if field is not None else ""


def _place_doi_list(
  corpus_root: Path, existing: Path | None, name: str, text: str
) -> tuple[PaperListPlacement, list[PaperIdentity]]:
  """Append the uploaded DOIs that are not listed yet, and name them."""
  uploaded = _doi_lines(text)
  if not uploaded:
    raise UploadError(f"'{name}' holds no DOIs.")
  target = existing or corpus_root / DOI_LIST_NAME
  listed = (
    {doi.lower() for doi in _doi_lines(existing.read_text(encoding="utf-8"))}
    if existing is not None
    else set()
  )
  fresh: list[str] = []
  already: list[str] = []
  for doi in uploaded:
    if doi.lower() in listed:
      already.append(doi)
      continue
    listed.add(doi.lower())
    fresh.append(doi)
  _append(target, "\n".join(fresh), blank_line=False)
  placement = PaperListPlacement(path=target, added=len(fresh), already_listed=already)
  return placement, [PaperIdentity(doi=doi) for doi in fresh]


def _doi_lines(text: str) -> list[str]:
  """Return the DOIs of a DOI list, read the way ingest reads them."""
  return [
    line.strip()
    for line in text.splitlines()
    if line.strip() and not line.startswith("#")
  ]


def _append(target: Path, block: str, *, blank_line: bool) -> None:
  """Append a block of lines to a paper list, starting on a fresh line."""
  if not block:
    return
  current = target.read_text(encoding="utf-8") if target.is_file() else ""
  current = current.rstrip("\n")
  if current:
    current += "\n\n" if blank_line else "\n"
  target.write_text(current + block + "\n", encoding="utf-8")
  logger.info("Updated paper list %s", target)


def check_pairing(corpus_root: Path) -> PairingResult:
  """
  Ask the pipeline how the Corpus's paper list and PDFs pair up, writing nothing.

  The check fails (exit 1) when the Corpus is outside the envelope but still
  prints its JSON, so the JSON is read whatever the exit code; a run that
  printed none (no paper list yet, a locked Corpus) carries no check.
  """
  run = pipeline_runner.run_stage(corpus_root, "ingest", args=CHECK_ARGS)
  if run.outcome in (RunOutcome.LOCKED, RunOutcome.MISSING_KEY) or not run.stdout:
    return PairingResult(run=run)
  try:
    check = PairingCheck.model_validate_json(run.stdout)
  except ValidationError:
    logger.error("The pairing check printed no readable report")
    return PairingResult(run=run)
  return PairingResult(run=run, check=check)
