"""
The manual parse-correction round-trip (issue #46 Step 3).

The dashboard writes a researcher's pasted full text to a `judgments/` overlay
(ADR 0002/0003); the pipeline's `apply-parse-corrections` step reads the overlay
and writes the corrected `paper-data/<key>.parsed.json` so `extract` reruns on
it. These tests exercise both halves and the boundary between them.
"""

import shutil
from pathlib import Path

import pytest

from research_gap_dashboard.apply_corrections import apply_parse_corrections
from research_gap_dashboard.cli import main
from research_gap_dashboard.dashboard.parse_corrections import (
  EmptyCorrectionError,
  clear_parse_correction,
  load_parse_corrections,
  record_parse_correction,
)
from research_gap_dashboard.ingest import ingest_corpus
from research_gap_dashboard.parsing import read_parsed_paper


@pytest.fixture(name="corpus")
def corpus_fixture(cardiology_corpus: Path, tmp_path: Path) -> Path:
  """Copy the fixture Corpus and ingest it so a manifest is on disk."""
  target = tmp_path / "corpus"
  shutil.copytree(cardiology_corpus, target)
  ingest_corpus(target)
  return target


def test_a_correction_round_trips_through_the_overlay(tmp_path: Path):
  """A saved correction reads back, and re-saving replaces it rather than doubling."""
  record_parse_correction(tmp_path, "paper-a", "The real full text.")
  record_parse_correction(tmp_path, "paper-a", "An even better full text.")

  log = load_parse_corrections(tmp_path)

  assert len(log.corrections) == 1
  assert log.correction_for("paper-a").corrected_text == "An even better full text."


def test_an_empty_correction_is_refused(tmp_path: Path):
  """A blank paste is never written; there is nothing to correct with."""
  with pytest.raises(EmptyCorrectionError):
    record_parse_correction(tmp_path, "paper-a", "   \n  ")

  assert load_parse_corrections(tmp_path).corrections == []


def test_clearing_a_correction_removes_it(tmp_path: Path):
  """Clearing a correction drops it from the overlay."""
  record_parse_correction(tmp_path, "paper-a", "text")
  clear_parse_correction(tmp_path, "paper-a")

  assert load_parse_corrections(tmp_path).correction_for("paper-a") is None


def test_apply_writes_the_corrected_text_to_the_parse_artifact(corpus: Path):
  """Applying the overlay writes paper-data so a later extract sees the fixed text."""
  citation_key = "hanna2019"
  record_parse_correction(corpus, citation_key, "Corrected methods and results text.")

  applied = apply_parse_corrections(corpus)
  parsed = read_parsed_paper(corpus, citation_key)

  assert applied == [citation_key]
  assert parsed.parser == "manual"
  assert "Corrected methods and results text." in parsed.full_text


def test_apply_skips_corrections_for_unknown_papers(corpus: Path):
  """A stale overlay entry never invents a Paper outside the manifest."""
  record_parse_correction(corpus, "not-a-real-paper", "ghost text")

  assert apply_parse_corrections(corpus) == []


def test_cli_apply_parse_corrections(corpus: Path):
  """The CLI applies the overlay and reports success."""
  record_parse_correction(corpus, "hanna2019", "Corrected full text.")

  assert main(["apply-parse-corrections", str(corpus)]) == 0
  assert read_parsed_paper(corpus, "hanna2019").parser == "manual"
