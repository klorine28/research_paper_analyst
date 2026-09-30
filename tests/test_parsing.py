"""Behavior of the parse stage: a Corpus of PDFs in, sectioned text out."""

import shutil
from pathlib import Path

import pytest

from research_gap_dashboard import parsing
from research_gap_dashboard.cli import main
from research_gap_dashboard.ingest import ingest_corpus
from research_gap_dashboard.cli import EXIT_INCOMPLETE
from research_gap_dashboard.parsing import (
  MIN_USABLE_CHARS,
  DoclingParser,
  FallbackParser,
  ParsedPaper,
  ParserChainError,
  default_parser,
  parse_corpus,
  read_parse_report,
  read_parsed_paper,
)

_SAMPLE_MARKDOWN = """\
Heart Failure in the Elderly

## Abstract

We studied outcomes in older patients.

## Materials and Methods

A retrospective cohort of 200 patients.

## Results

Mortality was 12%.

## Discussion

The findings echo prior work.

## Limitations

The cohort was single-center.

## Future Directions

A multi-center trial is warranted.

## References

1. Someone et al.
"""


@pytest.fixture(name="corpus")
def corpus_fixture(cardiology_corpus: Path, tmp_path: Path) -> Path:
  """Copy the fixture Corpus and ingest it so a manifest is on disk."""
  target = tmp_path / "corpus"
  shutil.copytree(cardiology_corpus, target)
  ingest_corpus(target)
  return target


class _StubParser:  # pylint: disable=too-few-public-methods
  """A parser returning canned Markdown, or raising for named PDFs."""

  name = "stub"

  def __init__(self, markdown: str = _SAMPLE_MARKDOWN, fail: set[str] | None = None):
    self._parser = DoclingParser(convert=lambda _: markdown)
    self._fail = fail or set()

  def parse(self, pdf: Path) -> ParsedPaper:
    """Parse the PDF, or raise when its name is on the fail list."""
    if pdf.name in self._fail:
      raise RuntimeError(f"docling choked on {pdf.name}")
    return self._parser.parse(pdf)


def test_docling_types_do_not_leak_through_the_seam():
  """The parser returns a ParsedPaper of plain sections, not docling objects."""
  parser = DoclingParser(convert=lambda _: _SAMPLE_MARKDOWN)

  parsed = parser.parse(Path("some.pdf"))

  assert isinstance(parsed, ParsedPaper)
  assert all(isinstance(section.text, str) for section in parsed.sections)


def test_headings_become_labeled_sections():
  """Common paper headings map onto the canonical section labels."""
  parser = DoclingParser(convert=lambda _: _SAMPLE_MARKDOWN)

  parsed = parser.parse(Path("some.pdf"))

  labels = [section.label for section in parsed.sections]
  assert "methods" in labels
  assert "limitations" in labels
  assert "future_work" in labels
  methods = parsed.section("methods")
  assert methods is not None
  assert methods.text == "A retrospective cohort of 200 patients."
  assert parsed.section("future_work") is not None


def test_docling_markdown_escapes_are_stripped_from_text():
  """Docling's HTML entities, backslash escapes, and comment placeholders go away."""
  markdown = (
    "## Methods\n\n"
    "Emotional trigger (P&lt;0.01) in cel\\_miR-39 <!-- image --> from R&amp;D."
  )
  parser = DoclingParser(convert=lambda _: markdown)

  methods = parser.parse(Path("some.pdf")).section("methods")

  assert methods is not None
  assert methods.text == "Emotional trigger (P<0.01) in cel_miR-39  from R&D."


def test_text_before_the_first_heading_is_kept_as_front_matter():
  """The title block ahead of any heading is preserved, not dropped."""
  parser = DoclingParser(convert=lambda _: _SAMPLE_MARKDOWN)

  parsed = parser.parse(Path("some.pdf"))

  front = parsed.sections[0]
  assert front.label == "front_matter"
  assert "Heart Failure in the Elderly" in front.text


def test_parse_corpus_writes_one_sectioned_file_per_paper(corpus: Path):
  """Every Paper's sections land under paper-data/ and can be read back."""
  report = parse_corpus(corpus, parser=_StubParser())

  assert len(report.parsed_paths) == 10
  assert report.failures == []
  for path in report.parsed_paths:
    assert path.parent == corpus / "paper-data"
    assert path.is_file()

  reread = read_parsed_paper(corpus, "hanna2019")
  assert reread.section("methods") is not None


def test_a_failing_pdf_is_reported_without_aborting_the_corpus(corpus: Path):
  """One unparseable PDF is surfaced per Paper; the rest still parse."""
  doomed = sorted((corpus / "papers").glob("*.pdf"))[0]

  report = parse_corpus(corpus, parser=_StubParser(fail={doomed.name}))

  assert len(report.parsed_paths) == 9
  assert [failure.pdf_path.name for failure in report.failures] == [doomed.name]
  assert "docling choked" in report.failures[0].error


class _FixedParser:  # pylint: disable=too-few-public-methods
  """A parser returning fixed Markdown under a given name, or raising."""

  def __init__(self, name: str, markdown: str = "", *, boom: bool = False):
    self.name = name
    self._markdown = markdown
    self._boom = boom

  def parse(self, pdf: Path) -> ParsedPaper:
    """Return the canned sections, or raise when configured to."""
    if self._boom:
      raise RuntimeError(f"{self.name} crashed")
    return DoclingParser(convert=lambda _: self._markdown, name=self.name).parse(pdf)


def test_fallback_returns_the_first_usable_parser():
  """An empty first parser is skipped; the next usable one wins and is recorded."""
  chain = FallbackParser(
    [_FixedParser("empty", ""), _FixedParser("docling-ocr", _SAMPLE_MARKDOWN)]
  )

  parsed = chain.parse(Path("scan.pdf"))

  assert parsed.section("methods") is not None
  assert parsed.parser == "docling-ocr"


def test_fallback_skips_a_parser_that_raises():
  """A parser that crashes does not abort the chain; the next one is tried."""
  chain = FallbackParser(
    [_FixedParser("docling", boom=True), _FixedParser("pdfplumber", _SAMPLE_MARKDOWN)]
  )

  parsed = chain.parse(Path("weird.pdf"))

  assert parsed.parser == "pdfplumber"


def test_fallback_treats_near_empty_output_as_unusable():
  """A parser that yields only a stray title line is skipped as near-empty."""
  chain = FallbackParser(
    [_FixedParser("docling", "Scan"), _FixedParser("docling-ocr", _SAMPLE_MARKDOWN)]
  )

  parsed = chain.parse(Path("scan.pdf"))

  assert len(_SAMPLE_MARKDOWN) > MIN_USABLE_CHARS  # guards the fixture
  assert parsed.parser == "docling-ocr"


def test_fallback_raises_when_every_parser_fails():
  """When no parser yields usable text, the chain fails loudly, naming each try."""
  chain = FallbackParser(
    [_FixedParser("docling", ""), _FixedParser("docling-ocr", boom=True)]
  )

  with pytest.raises(ParserChainError) as excinfo:
    chain.parse(Path("imageonly.pdf"))

  assert "docling" in str(excinfo.value)
  assert "docling-ocr" in str(excinfo.value)


def test_default_parser_chains_docling_then_ocr_then_pdfplumber():
  """The shipped chain tries plain docling, then docling+OCR, then pdfplumber."""
  chain = default_parser()

  assert isinstance(chain, FallbackParser)
  assert [p.name for p in chain.parsers] == ["docling", "docling-ocr", "pdfplumber"]


def test_parse_persists_an_inspectable_report_with_failures(corpus: Path):
  """The parse stage writes its failures to disk for the dashboard to surface."""
  doomed = sorted((corpus / "papers").glob("*.pdf"))[0]

  parse_corpus(corpus, parser=_StubParser(fail={doomed.name}))
  report = read_parse_report(corpus)

  assert len(report.parsed_paths) == 9
  assert [f.pdf_path.name for f in report.failures] == [doomed.name]


def test_cli_parse_writes_sectioned_text(corpus: Path, monkeypatch):
  """`parse <corpus>` runs the default parser and leaves paper-data/ files."""
  monkeypatch.setattr(parsing, "_docling_to_markdown", lambda _: _SAMPLE_MARKDOWN)

  assert main(["parse", str(corpus)]) == 0
  assert sorted((corpus / "paper-data").glob("*.parsed.json"))


def test_cli_parse_signals_incompleteness_without_aborting(corpus: Path, monkeypatch):
  """A bad PDF still lets the rest parse, but the CLI exits with the incomplete code."""
  doomed = sorted((corpus / "papers").glob("*.pdf"))[0]
  monkeypatch.setattr(
    parsing, "default_parser", lambda: _StubParser(fail={doomed.name})
  )

  exit_code = main(["parse", str(corpus)])

  assert exit_code == EXIT_INCOMPLETE
  assert len(sorted((corpus / "paper-data").glob("*.parsed.json"))) == 9
