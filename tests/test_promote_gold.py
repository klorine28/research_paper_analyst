"""
Behavior of the promote-gold step and its committed Gold Extraction fixture.

promote-gold is pipeline-side and CLI-only (ADR 0003): it reads `extractions.json`
plus the researcher's review overlay and writes a human-verified Gold Extraction.
These tests pin the overlay-to-gold transformation and, as a regression guard,
pin the committed gold fixture against re-promotion so a later change to the step
is caught. Running the in-app review over the cardiology fixture Corpus and
promoting the result is what satisfies issue #9's human gate.
"""

import json
import logging
import shutil
from pathlib import Path

import pytest

from research_gap_dashboard.cli import main
from research_gap_dashboard.promote_gold import (
  GOLD_EXTRACTION_NAME,
  GoldPromotionRefusedError,
  default_gold_path,
  promote_gold,
  read_gold,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "gold-review"
EXPECTED_GOLD = FIXTURE_DIR / "gold_extractions.expected.json"


def _copy_fixture(tmp_path: Path) -> Path:
  """Copy the gold-review fixture Corpus so a run does not touch the committed one."""
  target = tmp_path / "corpus"
  shutil.copytree(FIXTURE_DIR, target)
  return target


def test_approved_and_unreviewed_facts_are_kept(tmp_path: Path):
  """An approved fact and an unreviewed one both survive into the gold Extraction."""
  gold = promote_gold(_copy_fixture(tmp_path))

  fields = gold.extractions[0].fields
  assert len(fields.research_question) == 1
  assert len(fields.limitations) == 1


def test_flagged_facts_are_dropped(tmp_path: Path):
  """A fact the reviewer flagged wrong does not enter the gold Extraction."""
  gold = promote_gold(_copy_fixture(tmp_path))

  methods = gold.extractions[0].fields.methods
  assert [fact.statement for fact in methods] == ["Retrospective cohort study."]


def test_edited_facts_keep_the_reviewers_correction(tmp_path: Path):
  """An edited fact carries the reviewer's corrected text into the gold."""
  gold = promote_gold(_copy_fixture(tmp_path))

  datasets = gold.extractions[0].fields.datasets
  assert datasets[0].statement == "A single national registry."


def test_added_facts_are_appended(tmp_path: Path):
  """A missing fact the reviewer added is present in the gold Extraction."""
  gold = promote_gold(_copy_fixture(tmp_path))

  future_work = gold.extractions[0].fields.future_work
  assert [fact.statement for fact in future_work] == [
    "A multi-center study is warranted."
  ]


def test_gold_matches_the_committed_fixture(tmp_path: Path):
  """Re-promoting the fixture reproduces the committed gold, catching regressions."""
  gold = promote_gold(_copy_fixture(tmp_path))
  expected = read_gold(EXPECTED_GOLD)

  assert gold.extractions == expected.extractions


def test_cli_writes_the_gold_fixture(tmp_path: Path):
  """The promote-gold CLI writes the gold Extraction to the corpus artifacts."""
  corpus = _copy_fixture(tmp_path)

  exit_code = main(["promote-gold", str(corpus)])

  assert exit_code == 0
  written = default_gold_path(corpus)
  assert written.name == GOLD_EXTRACTION_NAME
  gold = read_gold(written)
  assert gold.extractions == read_gold(EXPECTED_GOLD).extractions


def _break_an_edit(corpus: Path) -> None:
  """Make the reviewer's edited Evidence a passage the Paper never says."""
  overlay = corpus / "judgments" / "extraction_review.json"
  saved = json.loads(overlay.read_text(encoding="utf-8"))
  for verdict in saved["reviews"][0]["verdicts"]:
    if verdict["verdict"] == "edited":
      verdict["edited_passage"] = "A sentence that is nowhere in this Paper."
  overlay.write_text(json.dumps(saved), encoding="utf-8")


def test_ungrounded_evidence_refuses_the_whole_promotion(tmp_path: Path):
  """One unverifiable passage refuses the promotion and names the Paper."""
  corpus = _copy_fixture(tmp_path)
  _break_an_edit(corpus)

  with pytest.raises(GoldPromotionRefusedError) as refused:
    promote_gold(corpus)

  assert refused.value.citation_key == "paper-a"
  assert "nowhere in this Paper" in str(refused.value)


def test_cli_reports_a_refused_promotion_and_writes_nothing(
  tmp_path: Path, caplog: pytest.LogCaptureFixture
):
  """The CLI exits 1 with the refusal reason as its last word; no gold is written."""
  corpus = _copy_fixture(tmp_path)
  _break_an_edit(corpus)

  with caplog.at_level(logging.ERROR, logger="research_gap_dashboard"):
    exit_code = main(["promote-gold", str(corpus)])

  assert exit_code == 1
  assert not default_gold_path(corpus).exists()
  last = caplog.records[-1].getMessage()
  assert "paper-a" in last
  assert "refused" in last.lower()
