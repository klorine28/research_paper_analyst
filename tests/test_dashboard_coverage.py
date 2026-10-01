"""
Behavior of the publication-volume Trends view: the artifacts in, figures out.

The Trends chart reads the NormalizedFacts aggregate writes together with the
manifest's years, so a schema drift between writer and the dashboard's readers
would surface here. The tests build real artifacts and read them back through the
dashboard's own readers and chart-building functions. Papers with no known year
must be reported, not dropped.
"""

from pathlib import Path

import pytest

from conftest import StubLlmClient
from test_detect import _PLACEMENTS, _write_corpus

from research_gap_dashboard.dashboard.artifacts import (
  load_manifest,
  load_normalized_facts,
)
from research_gap_dashboard.dashboard.coverage import (
  build_trends,
  build_trends_figure,
)
from research_gap_dashboard.detect import detect_corpus

# Takotsubo runs 2018-2019 only; heart failure appears in 2021. Over the dated
# span 2018-2021 (midpoint 2019.5) takotsubo is abandoned and heart failure
# emerging, and p06 carries no year to exercise the missing-year denominator.
_YEARS = {
  "p01": 2018,
  "p02": 2018,
  "p03": 2019,
  "p04": 2019,
  "p05": 2021,
  "p06": None,
}


@pytest.fixture(name="corpus")
def corpus_fixture(tmp_path: Path) -> Path:
  """Write a synthetic Corpus with years and run detect so the artifacts exist."""
  root = _write_corpus(tmp_path / "corpus", _PLACEMENTS, years=_YEARS)
  detect_corpus(root, StubLlmClient({}))
  return root


def test_trends_count_papers_per_topic_per_year(corpus: Path):
  """Each Topic's line counts the dated Papers that cover it, per year."""
  trends = build_trends(load_normalized_facts(corpus), load_manifest(corpus))

  assert trends.years == [2018, 2019, 2021]
  by_label = {topic.label: topic for topic in trends.topics}
  # Takotsubo: 2x2018, 2x2019, 0x2021.
  assert by_label["Takotsubo Cardiomyopathy"].counts == [2, 2, 0]
  # Heart Failure: only p05 (2021) has a year; p06 has none.
  assert by_label["Heart Failure"].counts == [0, 0, 1]


def test_trends_flag_emerging_and_abandoned_topics(corpus: Path):
  """A recent-only Topic reads as emerging; an early-only Topic as abandoned."""
  trends = build_trends(load_normalized_facts(corpus), load_manifest(corpus))

  assert [t.label for t in trends.abandoned] == ["Takotsubo Cardiomyopathy"]
  assert [t.label for t in trends.emerging] == ["Heart Failure"]


def test_trends_report_papers_without_a_year(corpus: Path):
  """Papers with no known year are counted, not silently dropped."""
  trends = build_trends(load_normalized_facts(corpus), load_manifest(corpus))

  assert trends.corpus_paper_count == 6
  assert trends.papers_with_year == 5
  assert trends.papers_without_year == 1


def test_trends_figure_draws_a_line_per_topic(corpus: Path):
  """The chart has one trace per Topic, and marks emerging/abandoned lines."""
  trends = build_trends(load_normalized_facts(corpus), load_manifest(corpus))
  figure = build_trends_figure(trends)

  names = [trace.name for trace in figure.data]
  assert len(figure.data) == len(trends.topics)
  assert "Heart Failure (emerging)" in names
  assert "Takotsubo Cardiomyopathy (abandoned)" in names
