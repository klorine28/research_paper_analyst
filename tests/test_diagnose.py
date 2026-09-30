"""Behavior of the parse/extract funnel diagnostic (#46 Step 1)."""

import shutil
from pathlib import Path

import pytest

from research_gap_dashboard.cli import main
from research_gap_dashboard.diagnose import (
  NEAR_EMPTY_CHARS,
  FunnelReport,
  diagnose_corpus,
)
from research_gap_dashboard.ingest import ingest_corpus
from research_gap_dashboard.parsing import DoclingParser, ParsedPaper, parse_corpus

_GOOD_MARKDOWN = """\
Heart Failure in the Elderly

## Abstract

We studied outcomes in older patients across several hospitals over a decade,
following survival, readmission, and quality-of-life endpoints in detail.

## Materials and Methods

A retrospective cohort of 200 patients was assembled from the registry, and
baseline characteristics were compared between the treated and control arms.

## Results

Mortality was 12% in the treated arm and 19% in the control arm, a difference
that persisted after adjusting for age, sex, and comorbidity burden.

## Discussion

The findings echo prior work and suggest that earlier intervention improves
outcomes in this older, higher-risk population.
"""

# Docling exporting a scanned/image-only PDF: a title block and nothing else,
# collapsed into one unlabeled section that is also near-empty.
_EMPTY_MARKDOWN = "Scan\n"


class _ScriptedParser:  # pylint: disable=too-few-public-methods
  """Return per-PDF Markdown so a corpus can mix healthy and broken papers."""

  name = "scripted"

  def __init__(self, by_name: dict[str, str], default: str):
    self._by_name = by_name
    self._default = default

  def parse(self, pdf: Path) -> ParsedPaper:
    """Parse the canned Markdown chosen for this PDF's filename."""
    markdown = next(
      (md for prefix, md in self._by_name.items() if pdf.name.startswith(prefix)),
      self._default,
    )
    return DoclingParser(convert=lambda _: markdown).parse(pdf)


@pytest.fixture(name="corpus")
def corpus_fixture(cardiology_corpus: Path, tmp_path: Path) -> Path:
  """Copy the fixture Corpus and ingest it so a manifest is on disk."""
  target = tmp_path / "corpus"
  shutil.copytree(cardiology_corpus, target)
  ingest_corpus(target)
  return target


def test_funnel_counts_a_fully_parsed_corpus(corpus: Path):
  """Every Paper parses into labeled sections: parse and sectioned stages are full."""
  parse_corpus(corpus, parser=_ScriptedParser({}, default=_GOOD_MARKDOWN))

  report = diagnose_corpus(corpus)

  assert isinstance(report, FunnelReport)
  assert report.total_papers == 10
  assert report.parsed_count == 10
  assert report.sectioned_count == 10


def test_funnel_surfaces_missing_parsed_text(corpus: Path):
  """A PDF with no .parsed.json is counted as unparsed and flagged per-Paper."""
  report_before = parse_corpus(
    corpus, parser=_ScriptedParser({}, default=_GOOD_MARKDOWN)
  )
  # Delete one parsed file to simulate a parser that dropped a Paper.
  report_before.parsed_paths[0].unlink()

  report = diagnose_corpus(corpus)

  assert report.parsed_count == 9
  missing = [p for p in report.papers if not p.parsed]
  assert len(missing) == 1
  assert any("parsed" in issue for issue in missing[0].issues)


def test_funnel_flags_near_empty_and_collapsed_papers(corpus: Path):
  """A paper collapsed into one near-empty unlabeled section is not 'sectioned'."""
  parser = _ScriptedParser({"dubrock2018": _EMPTY_MARKDOWN}, default=_GOOD_MARKDOWN)
  parse_corpus(corpus, parser=parser)

  report = diagnose_corpus(corpus)

  assert report.parsed_count == 10
  assert report.sectioned_count == 9
  broken = next(p for p in report.papers if p.citation_key == "dubrock2018")
  assert broken.near_empty
  assert broken.char_count < NEAR_EMPTY_CHARS
  assert broken.issues


def test_cli_diagnose_gates_on_min_rate(corpus: Path, caplog):
  """`diagnose --min-rate` exits non-zero when extraction never ran (0% end-to-end)."""
  parse_corpus(corpus, parser=_ScriptedParser({}, default=_GOOD_MARKDOWN))

  exit_ok = main(["diagnose", str(corpus)])
  exit_gated = main(["diagnose", str(corpus), "--min-rate", "0.9"])

  assert exit_ok == 0
  assert exit_gated == 1
