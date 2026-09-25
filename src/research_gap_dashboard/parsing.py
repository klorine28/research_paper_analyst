"""
The PDF-parsing seam: turn a Paper's PDF into sectioned text.

One internal seam, `PdfParser.parse(pdf) -> ParsedPaper`, stands between the
pipeline and whichever library reads PDFs. v1 ships the docling adapter here;
GROBID or marker can join it by implementing the same protocol (see
docs/research/pdf-parsing-library.md). The adapter owns every docling type: the
rest of the pipeline only ever sees `ParsedPaper`s, so the suite parses offline
by injecting a fake Markdown converter.

The `parse_corpus` stage runs the parser over an ingested Corpus, storing one
sectioned-text file per Paper under `paper-data/` and surfacing a per-Paper
failure without aborting the run.
"""

import html
import logging
import re
from collections.abc import Callable
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel

from research_gap_dashboard.corpus_layout import inspect_corpus_layout
from research_gap_dashboard.ingest import read_manifest

logger = logging.getLogger(__name__)

PARSED_SUFFIX = ".parsed.json"

# A PDF-to-Markdown conversion: given a PDF path, return the document as
# Markdown with ATX (`#`) headings. Adapters depend on this, not on any PDF
# library, so tests inject canned Markdown.
PdfToMarkdown = Callable[[Path], str]

# The canonical section kinds Extraction reads. Every heading is mapped to one
# of these; anything unrecognized is "other" so no text is ever dropped.
SectionLabel = Literal[
  "front_matter",
  "abstract",
  "introduction",
  "methods",
  "results",
  "discussion",
  "conclusion",
  "limitations",
  "future_work",
  "acknowledgements",
  "references",
  "other",
]

# Heading keywords mapped to a label, tried in order so specific labels
# ("future directions") win over broad ones ("discussion").
_LABEL_KEYWORDS: tuple[tuple[SectionLabel, tuple[str, ...]], ...] = (
  ("future_work", ("future work", "future direction", "future research")),
  ("limitations", ("limitation",)),
  ("abstract", ("abstract",)),
  ("introduction", ("introduction", "background")),
  ("methods", ("method", "materials and methods", "methodology", "experimental")),
  ("results", ("result", "findings")),
  ("discussion", ("discussion",)),
  ("conclusion", ("conclusion", "concluding")),
  ("acknowledgements", ("acknowledg",)),
  ("references", ("reference", "bibliography", "works cited")),
)

_HEADING = re.compile(r"^#{1,6}\s+(?P<heading>.+?)\s*#*$")

# Docling exports Markdown, which encodes punctuation two ways that are artifacts
# of the export, not the paper's prose: HTML entities (`P&lt;0.01`, `R&amp;D`) and
# backslash escapes (`cel\_miR-39`). Both break verbatim Evidence matching (the LLM
# quotes the decoded prose), so section text is decoded and un-escaped. This regex
# matches a backslash before one ASCII-punctuation character it can escape.
_MD_ESCAPE = re.compile(r"\\([\\`*_{}\[\]()#+.!|&~<>-])")

# Docling injects HTML comments as placeholders (e.g. `<!-- image -->`) mid-text;
# they are pure noise, so they are removed from section text.
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)


class ParsedSection(BaseModel):
  """One labeled section of a Paper's text."""

  label: SectionLabel
  heading: str
  text: str


class ParsedPaper(BaseModel):
  """A Paper's PDF as labeled sections, with the docling types stripped away."""

  source_pdf: Path
  sections: list[ParsedSection]

  @property
  def full_text(self) -> str:
    """The whole Paper as plain text, in reading order."""
    return "\n\n".join(section.text for section in self.sections if section.text)

  def section(self, label: SectionLabel) -> ParsedSection | None:
    """Return the first section carrying a label, or None when none does."""
    return next((s for s in self.sections if s.label == label), None)


class ParseFailure(BaseModel):
  """A Paper whose PDF could not be parsed, and why."""

  citation_key: str
  pdf_path: Path
  error: str


class ParseReport(BaseModel):
  """What the parse stage produced: the paper-data files it wrote, and failures."""

  corpus_root: Path
  parsed_paths: list[Path]
  failures: list[ParseFailure]


class PdfParser(Protocol):  # pylint: disable=too-few-public-methods
  """A library that turns one PDF into a ParsedPaper."""

  name: str

  def parse(self, pdf: Path) -> ParsedPaper:
    """Parse a PDF into labeled sections."""
    raise NotImplementedError


class DoclingParser:  # pylint: disable=too-few-public-methods
  """Parses PDFs with docling, the primary parser for v1."""

  name = "docling"

  def __init__(self, convert: PdfToMarkdown | None = None):
    """Wrap docling, or a fake `convert` so the suite parses offline."""
    self._convert = convert or _docling_to_markdown

  def parse(self, pdf: Path) -> ParsedPaper:
    """Convert a PDF to Markdown and split it into labeled sections."""
    markdown = self._convert(pdf)
    return ParsedPaper(source_pdf=pdf, sections=_sections_from_markdown(markdown))


def parse_corpus(root: Path, parser: PdfParser | None = None) -> ParseReport:
  """
  Parse every Paper's PDF into sectioned text stored under `paper-data/`.

  Each Paper is parsed independently: a PDF that fails is recorded in the
  report's failures and the run carries on with the rest of the Corpus.
  """
  parser = parser or DoclingParser()
  manifest = read_manifest(root)
  paper_data_dir = inspect_corpus_layout(root).layout.paper_data_dir
  paper_data_dir.mkdir(parents=True, exist_ok=True)

  parsed_paths: list[Path] = []
  failures: list[ParseFailure] = []
  for paper in manifest.papers:
    try:
      parsed = parser.parse(paper.pdf_path)
    # One bad PDF must not abort the run; every parser failure is per-Paper.
    except Exception as error:  # noqa: BLE001  # pylint: disable=broad-exception-caught
      logger.warning("Could not parse %s: %s", paper.pdf_path.name, error)
      failures.append(
        ParseFailure(
          citation_key=paper.citation_key, pdf_path=paper.pdf_path, error=str(error)
        )
      )
      continue
    out_path = paper_data_dir / f"{paper.citation_key}{PARSED_SUFFIX}"
    out_path.write_text(parsed.model_dump_json(indent=2), encoding="utf-8")
    parsed_paths.append(out_path)

  logger.info(
    "Parsed %d of %d Papers into %s (%d failed).",
    len(parsed_paths),
    len(manifest.papers),
    paper_data_dir,
    len(failures),
  )
  return ParseReport(corpus_root=root, parsed_paths=parsed_paths, failures=failures)


def read_parsed_paper(root: Path, citation_key: str) -> ParsedPaper:
  """Read the sectioned text a previous parse run wrote for a Paper."""
  path = (
    inspect_corpus_layout(root).layout.paper_data_dir / f"{citation_key}{PARSED_SUFFIX}"
  )
  return ParsedPaper.model_validate_json(path.read_text(encoding="utf-8"))


def _sections_from_markdown(markdown: str) -> list[ParsedSection]:
  """Split Markdown into labeled sections at its ATX (`#`) headings."""
  sections: list[ParsedSection] = []
  heading = ""
  body: list[str] = []

  def flush() -> None:
    text = _unescape_markdown("\n".join(body)).strip()
    if not heading and not text:
      return
    sections.append(
      ParsedSection(
        label=_label_for(heading, first=not sections),
        heading=heading,
        text=text,
      )
    )

  for line in markdown.splitlines():
    match = _HEADING.match(line)
    if match:
      flush()
      heading = _unescape_markdown(match.group("heading").strip())
      body = []
    else:
      body.append(line)
  flush()

  return sections


def _unescape_markdown(text: str) -> str:
  """Clean docling's Markdown: drop comment placeholders, decode entities/escapes."""
  text = _HTML_COMMENT.sub("", text)
  return _MD_ESCAPE.sub(r"\1", html.unescape(text))


def _label_for(heading: str, first: bool) -> SectionLabel:
  """Map a heading to a canonical section label."""
  lowered = heading.lower()
  for label, keywords in _LABEL_KEYWORDS:
    if any(keyword in lowered for keyword in keywords):
      return label
  if not heading:
    return "front_matter" if first else "other"
  return "other"


def _docling_to_markdown(pdf: Path) -> str:
  """Convert a PDF to Markdown with docling; this is the only docling contact."""
  # Imported lazily so the module stays light and docling is only needed for
  # the `pdf` optional extra, not for the test suite.
  from docling.document_converter import (  # noqa: PLC0415  # pylint: disable=import-outside-toplevel
    DocumentConverter,
  )

  result = DocumentConverter().convert(str(pdf))
  return result.document.export_to_markdown()
