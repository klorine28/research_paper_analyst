"""
Behavior of the Unanswered Limitations view: CandidateGaps artifact in, groups out.

The dashboard reads the artifact the detect stage writes, so these tests build a
real artifact with detect and then read it back through the dashboard's own
reader: a schema drift between writer and reader would surface here. Every group,
addressed or not, must be shown with its follow-up count and the verbatim source
passages behind it.
"""

from pathlib import Path

import pytest

from test_detect import (
  _GROUPING,
  _NONE_ADDRESSED,
  _SAMPLE_ADDRESSED,
  _limitations_root,
  RoutedLlmClient,
)

import research_gap_dashboard.dashboard as dashboard_pkg

from research_gap_dashboard.dashboard.artifacts import load_candidate_gaps
from research_gap_dashboard.dashboard.limitations import build_limitations
from research_gap_dashboard.detect import detect_corpus
from research_gap_dashboard.limitations import (
  FOLLOW_UP_PROMPT_VERSION,
  GROUP_PROMPT_VERSION,
)


@pytest.fixture(name="corpus")
def corpus_fixture(tmp_path: Path) -> Path:
  """
  Write a synthetic Corpus and run detect so a CandidateGaps artifact exists.

  The corpus lives in its own subdirectory so its parent holds only this Corpus,
  keeping the app's corpus picker deterministic under the shared pytest tmp dir.
  One group ("Small sample size") is addressed by a later Paper; the other
  ("Sex differences") is not.
  """
  client = RoutedLlmClient(
    {
      GROUP_PROMPT_VERSION: [_GROUPING],
      FOLLOW_UP_PROMPT_VERSION: [_SAMPLE_ADDRESSED, _NONE_ADDRESSED],
    }
  )
  root = _limitations_root(tmp_path / "corpus")
  detect_corpus(root, client)
  return root


def _group(corpus: Path, label: str):
  """Build the view and return the group with this label, failing if absent."""
  data = build_limitations(load_candidate_gaps(corpus))
  by_label = {group.label: group for group in data.groups}
  assert label in by_label, f"{label} not in {sorted(by_label)}"
  return by_label[label]


def test_every_limitation_group_becomes_a_view(corpus: Path):
  """The view lists one group per limitation group the artifact holds."""
  artifact = load_candidate_gaps(corpus)
  data = build_limitations(artifact)

  assert data.group_count == len(artifact.limitation_groups)
  assert data.group_count == 2


def test_unanswered_and_addressed_groups_are_counted(corpus: Path):
  """The summary separates groups a later Paper addressed from those it did not."""
  data = build_limitations(load_candidate_gaps(corpus))

  assert data.unanswered_count == 1
  assert data.addressed_count == 1
  assert data.extracted_paper_count == 4


def test_an_unanswered_group_shows_its_counts_and_source_passages(corpus: Path):
  """An unaddressed group cites its source Papers and their verbatim passages."""
  group = _group(corpus, "Sex differences")

  assert not group.addressed
  assert group.follow_up_count == 0
  assert group.later_paper_count == 1
  assert group.source_citation_keys == ["early1"]
  passages = {(s.citation_key, s.statement, s.passage) for s in group.statements}
  assert (
    "early1",
    "Sex differences need study.",
    "women warrant study",
  ) in passages
  assert all(s.section for s in group.statements)


def test_an_addressed_group_shows_its_follow_up_passages(corpus: Path):
  """An addressed group carries the later Paper's passage and why it addressed it."""
  group = _group(corpus, "Small sample size")

  assert group.addressed
  assert group.follow_up_count == 1
  assert group.source_paper_count == 2
  assert group.source_citation_keys == ["early1", "early2"]
  follow_up = group.follow_ups[0]
  assert follow_up.citation_key == "later1"
  assert follow_up.passage == "2000 patients"
  assert follow_up.reason


def _run_limitations_app(corpus: Path, monkeypatch: pytest.MonkeyPatch):
  """Run the Streamlit shell on the Unanswered Limitations page for a corpus."""
  from streamlit.testing.v1 import AppTest

  monkeypatch.setenv("RESEARCH_GAP_CORPORA_DIR", str(corpus.parent))
  app_path = Path(dashboard_pkg.__file__).parent / "app.py"
  app = AppTest.from_file(str(app_path), default_timeout=30).run()
  app.radio[0].set_value("Unanswered Limitations").run()
  return app


def test_limitations_page_renders_groups(corpus: Path, monkeypatch: pytest.MonkeyPatch):
  """The Unanswered Limitations page shows the groups without error."""
  app = _run_limitations_app(corpus, monkeypatch)

  assert not app.exception
  assert "Unanswered Limitations" in [header.value for header in app.header]
