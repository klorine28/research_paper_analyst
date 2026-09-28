"""
Behavior of the Extraction Review overlay: verdicts in, JSON out and back.

Extraction Review is annotation-only (ADR 0003): verdicts persist to a
researcher-review overlay under `judgments/` and never touch `extractions.json`.
These tests exercise the read/write functions directly (an acceptance criterion),
including that edited and added Evidence is refused unless it is verbatim in the
Paper's parsed text, and that a reload restores what a write left.
"""

import json
from pathlib import Path

import pytest

from research_gap_dashboard.dashboard.extraction_review import (
  EXTRACTION_REVIEW_NAME,
  JUDGMENTS_DIR,
  UnknownFieldError,
  add_fact,
  clear_fact_verdict,
  load_extraction_review,
  record_fact_verdict,
  remove_added_fact,
)
from research_gap_dashboard.dashboard.grounding import (
  PARSED_SUFFIX,
  UngroundedEvidenceError,
)
from research_gap_dashboard.parsing import ParsedPaper, ParsedSection

_TEXT = "We studied outcomes in older patients. A retrospective cohort of 200 patients."


def _write_parsed(corpus_root: Path, citation_key: str = "paper-a") -> None:
  """Write a parsed-paper file so grounding has text to check corrections against."""
  parsed = ParsedPaper(
    source_pdf=Path(f"{citation_key}.pdf"),
    sections=[ParsedSection(label="abstract", heading="Abstract", text=_TEXT)],
  )
  paper_data = corpus_root / "paper-data"
  paper_data.mkdir(parents=True, exist_ok=True)
  (paper_data / f"{citation_key}{PARSED_SUFFIX}").write_text(
    parsed.model_dump_json(indent=2), encoding="utf-8"
  )


def test_no_saved_review_reads_as_an_empty_log(tmp_path: Path):
  """A Corpus never reviewed reads back with no reviews, not an error."""
  log = load_extraction_review(tmp_path)

  assert log.reviews == []
  assert log.review_for("paper-a").verdicts == []


def test_approving_a_fact_persists_to_the_overlay(tmp_path: Path):
  """An approve verdict writes a JSON overlay under judgments/."""
  record_fact_verdict(tmp_path, "paper-a", "methods", 0, "approved")

  path = tmp_path / JUDGMENTS_DIR / EXTRACTION_REVIEW_NAME
  assert path.is_file()
  saved = json.loads(path.read_text(encoding="utf-8"))
  verdict = saved["reviews"][0]["verdicts"][0]
  assert verdict["field"] == "methods"
  assert verdict["verdict"] == "approved"


def test_a_verdict_survives_a_reload(tmp_path: Path):
  """What one write leaves on disk, a later independent read restores."""
  record_fact_verdict(tmp_path, "paper-a", "methods", 0, "flagged")
  record_fact_verdict(tmp_path, "paper-b", "datasets", 1, "removed")

  restored = load_extraction_review(tmp_path)

  paper_a = restored.review_for("paper-a").verdict_for("methods", 0)
  paper_b = restored.review_for("paper-b").verdict_for("datasets", 1)
  assert paper_a is not None and paper_a.verdict == "flagged"
  assert paper_b is not None and paper_b.verdict == "removed"


def test_re_deciding_a_fact_overwrites_its_verdict(tmp_path: Path):
  """A reviewer who changes their mind leaves one verdict per fact, the latest."""
  record_fact_verdict(tmp_path, "paper-a", "methods", 0, "approved")

  record_fact_verdict(tmp_path, "paper-a", "methods", 0, "flagged")

  review = load_extraction_review(tmp_path).review_for("paper-a")
  assert len(review.verdicts) == 1
  verdict = review.verdict_for("methods", 0)
  assert verdict is not None and verdict.verdict == "flagged"


def test_clearing_a_verdict_returns_the_fact_to_unreviewed(tmp_path: Path):
  """Undoing a verdict removes the entry, so the fact reads as unreviewed."""
  record_fact_verdict(tmp_path, "paper-a", "methods", 0, "approved")
  record_fact_verdict(tmp_path, "paper-a", "methods", 1, "approved")

  clear_fact_verdict(tmp_path, "paper-a", "methods", 0)

  review = load_extraction_review(tmp_path).review_for("paper-a")
  assert review.verdict_for("methods", 0) is None
  kept = review.verdict_for("methods", 1)
  assert kept is not None and kept.verdict == "approved"


def test_editing_a_fact_with_grounded_evidence_is_saved(tmp_path: Path):
  """An edit whose Evidence is verbatim in the Paper's text is persisted."""
  _write_parsed(tmp_path)

  record_fact_verdict(
    tmp_path,
    "paper-a",
    "methods",
    0,
    "edited",
    edited_statement="Retrospective cohort of 200.",
    edited_passage="A retrospective cohort of 200 patients.",
    edited_section="methods",
  )

  review = load_extraction_review(tmp_path).review_for("paper-a")
  verdict = review.verdict_for("methods", 0)
  assert verdict is not None
  assert verdict.verdict == "edited"
  assert verdict.edited_passage == "A retrospective cohort of 200 patients."


def test_editing_with_ungrounded_evidence_is_refused_and_nothing_is_saved(
  tmp_path: Path,
):
  """An edit whose Evidence is not in the Paper's text raises and writes nothing."""
  _write_parsed(tmp_path)

  with pytest.raises(UngroundedEvidenceError):
    record_fact_verdict(
      tmp_path,
      "paper-a",
      "methods",
      0,
      "edited",
      edited_passage="An invented quote never in the paper.",
    )

  assert not (tmp_path / JUDGMENTS_DIR / EXTRACTION_REVIEW_NAME).exists()


def test_adding_a_grounded_missing_fact_is_saved(tmp_path: Path):
  """A missing fact with verbatim Evidence is appended to the Paper's review."""
  _write_parsed(tmp_path)

  added = add_fact(
    tmp_path,
    "paper-a",
    "key_findings",
    "Outcomes were studied in older patients.",
    "We studied outcomes in older patients.",
    "abstract",
  )

  review = load_extraction_review(tmp_path).review_for("paper-a")
  assert len(review.added_facts) == 1
  assert review.added_facts[0].fact_id == added.fact_id


def test_adding_an_ungrounded_fact_is_refused(tmp_path: Path):
  """A missing fact whose Evidence is not in the Paper's text is refused."""
  _write_parsed(tmp_path)

  with pytest.raises(UngroundedEvidenceError):
    add_fact(
      tmp_path,
      "paper-a",
      "key_findings",
      "Invented finding.",
      "A quote that is not in the paper.",
      "abstract",
    )

  assert not (tmp_path / JUDGMENTS_DIR / EXTRACTION_REVIEW_NAME).exists()


def test_removing_an_added_fact_deletes_it(tmp_path: Path):
  """A reviewer can delete a fact they added, returning the field to its facts."""
  _write_parsed(tmp_path)
  added = add_fact(
    tmp_path,
    "paper-a",
    "key_findings",
    "Outcomes were studied.",
    "We studied outcomes in older patients.",
    "abstract",
  )

  remove_added_fact(tmp_path, "paper-a", added.fact_id)

  review = load_extraction_review(tmp_path).review_for("paper-a")
  assert review.added_facts == []


def test_an_unknown_field_is_refused(tmp_path: Path):
  """A verdict on anything but the seven Extraction fields is refused."""
  with pytest.raises(UnknownFieldError):
    record_fact_verdict(tmp_path, "paper-a", "not_a_field", 0, "approved")


def test_extractions_artifact_is_never_written_by_review(tmp_path: Path):
  """Reviewing never creates or touches extractions.json (ADR 0003)."""
  _write_parsed(tmp_path)
  record_fact_verdict(tmp_path, "paper-a", "methods", 0, "approved")
  add_fact(
    tmp_path,
    "paper-a",
    "key_findings",
    "Outcomes were studied.",
    "We studied outcomes in older patients.",
    "abstract",
  )

  assert not (tmp_path / "artifacts" / "extractions.json").exists()
