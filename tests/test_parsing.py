"""Behavior of the parse stage: a Corpus of PDFs in, sectioned text out."""

import shutil
from pathlib import Path

import pytest

from research_gap_dashboard import parsing
from research_gap_dashboard.cli import main
from research_gap_dashboard.ingest import ingest_corpus
from research_gap_dashboard.parsing import (
  DoclingParser,
  ParsedPaper,
  parse_corpus,
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


def test_cli_parse_writes_sectioned_text(corpus: Path, monkeypatch):
  """`parse <corpus>` runs the default parser and leaves paper-data/ files."""
  monkeypatch.setattr(parsing, "_docling_to_markdown", lambda _: _SAMPLE_MARKDOWN)

  assert main(["parse", str(corpus)]) == 0
  assert sorted((corpus / "paper-data").glob("*.parsed.json"))
