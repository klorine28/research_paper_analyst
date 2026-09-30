"""
The PDF-parsing seam: turn a Paper's PDF into sectioned text.

One internal seam, `PdfParser.parse(pdf) -> ParsedPaper`, stands between the
pipeline and whichever library reads PDFs. The shipped default is a
`FallbackParser` chain (`default_parser`): plain docling, then docling with OCR
for scanned/image-only PDFs, then pdfplumber as a last-resort text extractor
(see docs/research/pdf-parsing-library.md and ADR 0005). Each adapter owns its
library's types; the rest of the pipeline only ever sees `ParsedPaper`s, so the
suite parses offline by injecting fake converters. The chain falls through any
parser that raises or returns near-empty text, rescuing a PDF the primary parser
could not read, and records which parser won on the `ParsedPaper`.

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

# The inspectable per-run summary the parse stage writes under `artifacts/`, so
# the parse failures (which never abort the run) are on disk for the dashboard's
# "needs manual attention" queue and incompleteness banner (issue #46 Step 3).
PARSE_REPORT_NAME = "parse-report.json"

# A ParsedPaper with fewer real characters than this is treated as unusable: a
# scanned or image-only PDF with no OCR layer typically yields only a stray
# title line. The fallback chain falls through to the next parser below it, and
# the extraction funnel diagnostic flags it (`diagnose.NEAR_EMPTY_CHARS`).
MIN_USABLE_CHARS = 200

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
  # Which parser in the fallback chain produced this text, for reproducibility
  # and for surfacing when an OCR/fallback stage had to rescue a PDF.
  parser: str = "docling"

  @property
  def is_usable(self) -> bool:
    """True when the parse yielded enough real text to extract facts from."""
    return len(self.full_text.strip()) >= MIN_USABLE_CHARS

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


class ParserChainError(Exception):
  """Raised when every parser in a FallbackParser failed to yield usable text."""


class DoclingParser:  # pylint: disable=too-few-public-methods
  """Parses PDFs with docling; the primary parser, optionally with OCR."""

  def __init__(
    self,
    convert: PdfToMarkdown | None = None,
    *,
    name: str = "docling",
    ocr: bool = False,
  ):
    """Wrap docling (optionally OCR), or a fake `convert` so tests parse offline."""
    self.name = name
    self._convert = convert or (
      _docling_ocr_to_markdown if ocr else _docling_to_markdown
    )

  def parse(self, pdf: Path) -> ParsedPaper:
    """Convert a PDF to Markdown and split it into labeled sections."""
    markdown = self._convert(pdf)
    return ParsedPaper(
      source_pdf=pdf, sections=_sections_from_markdown(markdown), parser=self.name
    )


class PlainTextPdfParser:  # pylint: disable=too-few-public-methods
  """
  Last-resort text extraction with pdfplumber when docling itself fails.

  It recovers no section structure (all text lands in one `other` section), but
  a Paper with readable full text can still be extracted; a scanned PDF with no
  text layer still yields nothing and is surfaced as a failure.
  """

  name = "pdfplumber"

  def __init__(self, extract_text: Callable[[Path], str] | None = None):
    """Wrap pdfplumber, or a fake text extractor so tests parse offline."""
    self._extract_text = extract_text or _pdfplumber_text

  def parse(self, pdf: Path) -> ParsedPaper:
    """Extract the PDF's raw text as a single unlabeled section."""
    text = _unescape_markdown(self._extract_text(pdf)).strip()
    sections = [ParsedSection(label="other", heading="", text=text)] if text else []
    return ParsedPaper(source_pdf=pdf, sections=sections, parser=self.name)


class FallbackParser:  # pylint: disable=too-few-public-methods
  """
  Tries several parsers in order, returning the first that yields usable text.

  The chain owns the concrete parsers (docling, docling+OCR, pdfplumber); the
  pipeline still only ever sees a `ParsedPaper`. A parser that raises or returns
  near-empty text is skipped and the next is tried, so an image-only PDF that
  docling cannot read is rescued by the OCR stage. When every parser fails the
  chain raises `ParserChainError`, which `parse_corpus` records as a per-Paper
  failure (it never aborts the run).
  """

  def __init__(
    self,
    parsers: list[PdfParser],
    *,
    name: str = "fallback",
    min_chars: int = MIN_USABLE_CHARS,
  ):
    """Compose an ordered chain of parsers with a usability threshold."""
    if not parsers:
      raise ValueError("FallbackParser needs at least one parser")
    self.parsers = parsers
    self.name = name
    self._min_chars = min_chars

  def parse(self, pdf: Path) -> ParsedPaper:
    """Return the first parser's usable ParsedPaper, or raise if none succeed."""
    attempts: list[str] = []
    for parser in self.parsers:
      try:
        parsed = parser.parse(pdf)
      except Exception as error:  # noqa: BLE001  # pylint: disable=broad-exception-caught
        attempts.append(f"{parser.name} raised: {error}")
        logger.warning("Parser %s failed on %s: %s", parser.name, pdf.name, error)
        continue
      if len(parsed.full_text.strip()) >= self._min_chars:
        if attempts:
          logger.info(
            "Parser %s rescued %s after %d attempt(s).",
            parser.name,
            pdf.name,
            len(attempts),
          )
        return parsed
      attempts.append(f"{parser.name} yielded near-empty text")
    raise ParserChainError(
      f"no parser produced usable text for {pdf.name}: " + "; ".join(attempts)
    )


def default_parser() -> FallbackParser:
  """Build the shipped chain: plain docling, then docling+OCR, then pdfplumber."""
  return FallbackParser(
    [
      DoclingParser(),
      DoclingParser(name="docling-ocr", ocr=True),
      PlainTextPdfParser(),
    ]
  )


def parse_corpus(root: Path, parser: PdfParser | None = None) -> ParseReport:
  """
  Parse every Paper's PDF into sectioned text stored under `paper-data/`.

  Each Paper is parsed independently: a PDF that fails is recorded in the
  report's failures and the run carries on with the rest of the Corpus.
  """
  parser = parser or default_parser()
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
  report = ParseReport(corpus_root=root, parsed_paths=parsed_paths, failures=failures)
  _write_parse_report(root, report)
  return report


def _write_parse_report(root: Path, report: ParseReport) -> None:
  """Write the parse stage's inspectable summary under `artifacts/`."""
  artifacts_dir = inspect_corpus_layout(root).layout.artifacts_dir
  artifacts_dir.mkdir(parents=True, exist_ok=True)
  path = artifacts_dir / PARSE_REPORT_NAME
  path.write_text(report.model_dump_json(indent=2), encoding="utf-8")


def read_parse_report(root: Path) -> ParseReport:
  """Read the parse stage's summary from a previous run."""
  path = inspect_corpus_layout(root).layout.artifacts_dir / PARSE_REPORT_NAME
  return ParseReport.model_validate_json(path.read_text(encoding="utf-8"))


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


def _docling_ocr_to_markdown(pdf: Path) -> str:
  """Convert a scanned PDF to Markdown with docling's OCR pipeline enabled."""
  # Lazy import (the `pdf` extra), like `_docling_to_markdown`. OCR is what lets
  # docling read image-only PDFs that the default text pipeline returns empty.
  from docling.document_converter import (  # noqa: PLC0415  # pylint: disable=import-outside-toplevel
    DocumentConverter,
    PdfFormatOption,
  )
  from docling.datamodel.base_models import (  # noqa: PLC0415  # pylint: disable=import-outside-toplevel
    InputFormat,
  )
  from docling.datamodel.pipeline_options import (  # noqa: PLC0415  # pylint: disable=import-outside-toplevel
    PdfPipelineOptions,
  )

  options = PdfPipelineOptions()
  options.do_ocr = True
  converter = DocumentConverter(
    format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)}
  )
  result = converter.convert(str(pdf))
  return result.document.export_to_markdown()


def _pdfplumber_text(pdf: Path) -> str:
  """Extract a PDF's raw text with pdfplumber; the only pdfplumber contact."""
  # Lazy import so pdfplumber, like docling, is only needed for the `pdf` extra.
  import pdfplumber  # noqa: PLC0415  # pylint: disable=import-outside-toplevel

  with pdfplumber.open(pdf) as doc:
    return "\n\n".join(page.extract_text() or "" for page in doc.pages)
