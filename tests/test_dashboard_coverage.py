"""
Behavior of the Coverage & Trends view: the artifacts in, chart data and figures out.

The heatmap reads the Coverage Matrices detect writes, and the Trends chart reads
the NormalizedFacts aggregate writes together with the manifest's years, so a
schema drift between writer and the dashboard's readers would surface here. The
tests build real artifacts and read them back through the dashboard's own readers
and chart-building functions. An empty cell must read as "no papers", never as a
measured zero, and Papers with no known year must be reported, not dropped.
"""

import math
from pathlib import Path

import pytest

from conftest import StubLlmClient
from test_detect import _PLACEMENTS, _write_corpus

import research_gap_dashboard.dashboard as dashboard_pkg

from research_gap_dashboard.dashboard.artifacts import (
  load_candidate_gaps,
  load_manifest,
  load_normalized_facts,
)
from research_gap_dashboard.dashboard.coverage import (
  build_coverage_matrices,
  build_heatmap_figure,
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


def _matrix(corpus: Path, key: str):
  """Build the matrix views and return the one with this key, failing if absent."""
  views = {
    view.key: view for view in build_coverage_matrices(load_candidate_gaps(corpus))
  }
  assert key in views, f"{key} not in {sorted(views)}"
  return views[key]


def test_matrices_are_laid_out_as_grids(corpus: Path):
  """Each Coverage Matrix becomes a dense counts grid the heatmap can render."""
  view = _matrix(corpus, "topicxmethod")

  assert view.row_labels == ["Heart Failure", "Takotsubo Cardiomyopathy"]
  assert view.column_labels == ["Randomized Controlled Trial", "Retrospective Studies"]
  # Heart Failure x RCT: 1; Takotsubo x RCT: 0; Takotsubo x Retrospective: 4.
  by_label = dict(zip(view.row_labels, view.counts, strict=True))
  assert by_label["Heart Failure"] == [1, 1]
  assert by_label["Takotsubo Cardiomyopathy"] == [0, 4]
  assert view.corpus_paper_count == 6


def test_empty_matrices_are_dropped_from_the_picker(corpus: Path):
  """A matrix an axis this Corpus never touched (no dataset) offers nothing."""
  keys = {view.key for view in build_coverage_matrices(load_candidate_gaps(corpus))}

  assert "topicxdataset" not in keys
  assert "topicxmethod" in keys


def test_heatmap_reads_no_papers_apart_from_a_zero_value(corpus: Path):
  """An uncombined cell carries NaN and a 'no papers' label, not a 0."""
  figure = build_heatmap_figure(_matrix(corpus, "topicxmethod"))
  heatmap = figure.data[0]

  flat_z = [value for row in heatmap.z for value in row]
  flat_text = [value for row in heatmap.text for value in row]
  # The empty Takotsubo x RCT cell is NaN, never 0, and reads as "no papers".
  assert any(isinstance(v, float) and math.isnan(v) for v in flat_z)
  assert not any(v == 0 for v in flat_z)
  assert "no papers" in flat_text
  # A covered cell keeps its count as text and as a value on the scale.
  assert "4" in flat_text
  assert heatmap.zmin == 1


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


def _run_coverage_app(corpus: Path, monkeypatch: pytest.MonkeyPatch):
  """Run the Streamlit shell on the Coverage & Trends page for a corpus."""
  from streamlit.testing.v1 import AppTest

  monkeypatch.setenv("RESEARCH_GAP_CORPORA_DIR", str(corpus.parent))
  app_path = Path(dashboard_pkg.__file__).parent / "app.py"
  app = AppTest.from_file(str(app_path), default_timeout=30).run()
  app.radio[0].set_value("Coverage & Trends").run()
  return app


def test_coverage_page_renders_without_error(
  corpus: Path, monkeypatch: pytest.MonkeyPatch
):
  """The Coverage & Trends page renders its heatmap and trends without erroring."""
  app = _run_coverage_app(corpus, monkeypatch)

  assert not app.exception
  assert "Coverage & Trends" in [header.value for header in app.header]
