"""
Behavior of the dashboard's verbatim-Evidence matcher for Extraction Review.

A correction the reviewer saves must quote the Paper's parsed text verbatim
(ADR 0003). The dashboard owns its copy of the matcher and must normalise text
identically to the extract stage, or a correction grounded in one could be
refused by the other; these tests exercise the matcher and pin the two
normalisers against each other so drift is caught.
"""

from pathlib import Path

import pytest

from research_gap_dashboard.dashboard.grounding import (
  PARSED_SUFFIX,
  UngroundedEvidenceError,
  is_grounded,
  normalize,
  read_parsed_paper,
  verify_grounded,
)
from research_gap_dashboard.extract import _normalize as extract_normalize
from research_gap_dashboard.parsing import ParsedPaper, ParsedSection


def _write_parsed(corpus_root: Path, citation_key: str, text: str) -> None:
  """Write a minimal parsed-paper file the dashboard can ground against."""
  parsed = ParsedPaper(
    source_pdf=Path(f"{citation_key}.pdf"),
    sections=[ParsedSection(label="abstract", heading="Abstract", text=text)],
  )
  paper_data = corpus_root / "paper-data"
  paper_data.mkdir(parents=True, exist_ok=True)
  (paper_data / f"{citation_key}{PARSED_SUFFIX}").write_text(
    parsed.model_dump_json(indent=2), encoding="utf-8"
  )


def test_dashboard_normalize_matches_the_extract_stage():
  """The two normalisers agree, so a grounded correction is never refused by drift."""
  samples = [
    "We studied  outcomes\n in older patients.",
    "  leading and trailing  ",
    "tabs\tand\r\nnewlines collapse",
    "single spaces stay single",
  ]
  for sample in samples:
    assert normalize(sample) == extract_normalize(sample)


def test_a_verbatim_passage_is_grounded(tmp_path: Path):
  """A passage copied exactly from the Paper's text is accepted."""
  _write_parsed(tmp_path, "paper-a", "We studied outcomes in older patients.")

  parsed = read_parsed_paper(tmp_path, "paper-a")

  assert is_grounded("We studied outcomes in older patients.", parsed)


def test_a_passage_matches_across_line_wrapping(tmp_path: Path):
  """Whitespace differences from PDF line wrapping do not break a match."""
  _write_parsed(tmp_path, "paper-a", "We studied outcomes\n  in older   patients.")

  parsed = read_parsed_paper(tmp_path, "paper-a")

  assert is_grounded("We studied outcomes in older patients.", parsed)


def test_an_invented_passage_is_not_grounded(tmp_path: Path):
  """A passage that is not in the Paper's text is rejected."""
  _write_parsed(tmp_path, "paper-a", "We studied outcomes in older patients.")

  parsed = read_parsed_paper(tmp_path, "paper-a")

  assert not is_grounded("The study proved causation.", parsed)


def test_verify_grounded_raises_on_an_ungrounded_passage(tmp_path: Path):
  """Refusing an ungrounded correction is a loud, named error."""
  _write_parsed(tmp_path, "paper-a", "We studied outcomes in older patients.")
  parsed = read_parsed_paper(tmp_path, "paper-a")

  with pytest.raises(UngroundedEvidenceError):
    verify_grounded("Invented quote.", parsed)


def test_verify_grounded_refuses_an_empty_passage(tmp_path: Path):
  """An empty Evidence passage is refused, like an invented one."""
  _write_parsed(tmp_path, "paper-a", "We studied outcomes in older patients.")
  parsed = read_parsed_paper(tmp_path, "paper-a")

  with pytest.raises(UngroundedEvidenceError):
    verify_grounded("   ", parsed)


def test_reading_parsed_text_that_was_never_written_is_a_clear_error(tmp_path: Path):
  """Grounding against a Paper with no parsed text names the missing file."""
  with pytest.raises(FileNotFoundError, match="parse"):
    read_parsed_paper(tmp_path, "never-parsed")
