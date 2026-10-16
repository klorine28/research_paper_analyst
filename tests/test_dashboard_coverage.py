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
  MAX_TREND_LINES,
  TopicTrend,
  TrendsData,
  TrendStatus,
  build_trends,
  build_trends_figure,
  trends_cap_caption,
  trends_table_rows,
  trends_text_summary,
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


def _topic(index: int, status: TrendStatus, total: int) -> TopicTrend:
  """Build a dated TopicTrend carrying ``total`` Papers in one year."""
  return TopicTrend(
    category_id=f"t{index:02d}",
    label=f"Topic {index:02d}",
    counts=[total, 0, 0] if status != "emerging" else [0, 0, total],
    status=status,
    first_year=2018 if status != "emerging" else 2021,
    last_year=2018 if status == "abandoned" else 2021,
  )


def _many_topics(statuses: list[TrendStatus]) -> TrendsData:
  """TrendsData whose Topics are ranked by total, most-covered first."""
  topics = [
    _topic(index, status, total=len(statuses) - index + 1)
    for index, status in enumerate(statuses)
  ]
  return TrendsData(
    years=[2018, 2019, 2021],
    topics=topics,
    corpus_paper_count=40,
    papers_with_year=40,
    papers_without_year=0,
  )


# Okabe & Ito (2008) qualitative palette, without its yellow (#F0E442), which
# falls below graphical contrast on a white background.
_OKABE_ITO = {
  "#000000",
  "#E69F00",
  "#56B4E9",
  "#009E73",
  "#0072B2",
  "#D55E00",
  "#CC79A7",
}


def test_trends_lines_use_a_colour_blind_safe_palette_and_distinct_markers():
  """Each line has its own Okabe-Ito colour and its own marker symbol."""
  figure = build_trends_figure(_many_topics(["steady"] * 7))

  colours = [trace.line.color for trace in figure.data]
  symbols = [trace.marker.symbol for trace in figure.data]
  assert len(figure.data) == 7
  assert set(colours) == _OKABE_ITO
  assert len(set(symbols)) == 7
  for trace in figure.data:
    assert trace.marker.color == trace.line.color


def test_trends_status_encoding_is_unchanged(corpus: Path):
  """Emerging bold solid, abandoned bold dashed, steady thin solid."""
  trends = build_trends(load_normalized_facts(corpus), load_manifest(corpus))
  figure = build_trends_figure(trends)

  by_name = {trace.name: trace for trace in figure.data}
  emerging = by_name["Heart Failure (emerging)"]
  abandoned = by_name["Takotsubo Cardiomyopathy (abandoned)"]
  assert (emerging.line.dash, emerging.line.width) == ("solid", 3)
  assert (abandoned.line.dash, abandoned.line.width) == ("dash", 3)


def test_trends_cap_the_lines_and_table_the_rest():
  """Past the cap the least-covered steady Topics move to the table."""
  trends = _many_topics(["steady"] * 10)

  figure = build_trends_figure(trends)

  assert [trace.name for trace in figure.data] == [f"Topic {i:02d}" for i in range(7)]
  assert [topic.label for topic in trends.tabled_topics] == [
    "Topic 07",
    "Topic 08",
    "Topic 09",
  ]


def test_trends_cap_keeps_emerging_and_abandoned_lines_on_the_chart():
  """Status lines are what the chart is for, so they win a slot over volume."""
  statuses = ["steady"] * 8 + ["emerging", "abandoned"]
  trends = _many_topics(statuses)

  names = [trace.name for trace in build_trends_figure(trends).data]

  assert "Topic 08 (emerging)" in names
  assert "Topic 09 (abandoned)" in names
  assert len(names) == 7
  assert [topic.label for topic in trends.tabled_topics] == [
    "Topic 05",
    "Topic 06",
    "Topic 07",
  ]


def test_trends_cap_caption_states_how_many_topics_are_in_the_table():
  """The cap is reported, never silent."""
  trends = _many_topics(["steady"] * 10)

  assert trends_cap_caption(trends) == (
    "Showing 7 of 10 Topics as lines; the other 3 are listed in the table below."
  )
  assert trends_cap_caption(_many_topics(["steady"] * 7)) is None


def test_trends_table_lists_each_tabled_topic_with_its_volume_and_status():
  """The table carries what the chart would have: volume, span, trend."""
  rows = trends_table_rows(_many_topics(["steady"] * 8))

  assert rows == [
    {
      "Topic": "Topic 07",
      "Papers with a year": 2,
      "First year": 2018,
      "Last year": 2021,
      "Trend": "Steady",
    }
  ]


def test_trends_text_alternative_states_what_it_shows_and_its_denominator(
  corpus: Path,
):
  """The text summary names the chart, its lines, its span and its denominator."""
  trends = build_trends(load_normalized_facts(corpus), load_manifest(corpus))

  summary = trends_text_summary(trends)

  assert "line chart" in summary.lower()
  assert "2 of 2 Topics" in summary
  assert "2018\u20132021" in summary
  assert "5 of 6 Papers" in summary
  assert "Takotsubo Cardiomyopathy (4 Papers)" in summary
  assert "1 emerging" in summary
  assert "1 abandoned" in summary


def test_trends_text_alternative_counts_only_the_plotted_lines():
  """Past the line cap, the summary says how many Topics are drawn as lines."""
  trends = _many_topics(["steady"] * 9)

  summary = trends_text_summary(trends)

  assert f"{MAX_TREND_LINES} of 9 Topics" in summary
  assert "40 of 40 Papers" in summary
