"""
The follow-up matcher on a fixture Corpus with live-recorded completions (#55).

The fixture (`fixtures/follow-up-corpus`, built and recorded by
`scripts/record_follow_up_fixture.py`) holds one limitation a later Paper
addresses and a control nobody does. The recorded model answers cite facts in
the bracketed form the prompt shows them in, the shape that once hid every
follow-up on real Corpora.
"""

import shutil
from pathlib import Path

import pytest

from research_gap_dashboard.detect import (
  CandidateGapsReport,
  UnansweredLimitationGap,
  detect_corpus,
)
from research_gap_dashboard.limitations import LimitationGroup
from research_gap_dashboard.llm import RecordedLlmClient

FIXTURE = Path(__file__).parent / "fixtures" / "follow-up-corpus"


@pytest.fixture(name="report")
def report_fixture(tmp_path: Path) -> CandidateGapsReport:
  """Run detect on a copy of the fixture Corpus with the recorded completions."""
  root = Path(shutil.copytree(FIXTURE, tmp_path / FIXTURE.name))
  client = RecordedLlmClient.from_file(FIXTURE / "llm-responses.json")
  return detect_corpus(root, client)


def _group_of(report: CandidateGapsReport, citation_key: str) -> LimitationGroup:
  """Return the limitation group holding `citation_key`'s statement."""
  return next(
    group
    for group in report.limitation_groups
    if citation_key in group.source_citation_keys
  )


def test_a_limitation_a_later_paper_addressed_is_recognised(
  report: CandidateGapsReport,
) -> None:
  """The prospective multicentre cohort answers the single-centre limitation."""
  group = _group_of(report, "early2016")

  assert group.addressed
  assert {f.citation_key for f in group.follow_ups} == {"later2021"}
  assert "later2021:study_design[0]" in {f.fact_id for f in group.follow_ups}
  assert not [note for note in group.notes if "dropped" in note]


def test_the_control_limitation_stays_unanswered(report: CandidateGapsReport) -> None:
  """Nobody studied triggers, so that limitation alone becomes a card."""
  control = _group_of(report, "control2016")
  cards = [g for g in report.gaps if isinstance(g, UnansweredLimitationGap)]

  assert not control.addressed
  assert control.later_citation_keys == ["later2021", "unrelated2019"]
  assert [card.group_id for card in cards] == [control.group_id]
