"""
Assembling the publication-volume Trends view from the artifacts.

These are plain functions with no Streamlit dependency: the layout layer stays
thin by asking here for finished data and finished Plotly figures. From
CODING_STANDARDS.md > Research integrity: every count states the denominator it
was computed over, and Papers with no known year are reported, not silently
dropped from the Trends view.

The Trends view reads which Topics each Paper covers from the NormalizedFacts
artifact and the Papers' years from the CorpusManifest, so it never imports a
pipeline stage (ADR 0002).
"""

from typing import Literal

import plotly.graph_objects as go
from pydantic import BaseModel

from research_gap_dashboard.dashboard import text
from research_gap_dashboard.dashboard.artifacts import (
  ManifestArtifact,
  NormalizedFactsArtifact,
)

TrendStatus = Literal["emerging", "abandoned", "steady"]

# Topic identity rides on colour *and* marker symbol, so no line depends on hue
# alone (chart audit C5). The colours are Okabe & Ito's colour-blind-safe
# qualitative palette minus its yellow, which falls below graphical contrast on
# white. One colour per line means the palette length is the line cap: past it
# colours would repeat, so the remaining Topics go to a table instead.
TRENDS_PALETTE = (
  "#000000",
  "#E69F00",
  "#56B4E9",
  "#009E73",
  "#0072B2",
  "#D55E00",
  "#CC79A7",
)
TRENDS_MARKERS = (
  "circle",
  "square",
  "diamond",
  "triangle-up",
  "x",
  "triangle-down",
  "star",
)
MAX_TREND_LINES = len(TRENDS_PALETTE)


class TopicTrend(BaseModel):
  """One Topic's publication volume over the Corpus's years, and its trend."""

  category_id: str
  label: str
  counts: list[int]
  status: TrendStatus
  first_year: int
  last_year: int

  @property
  def total(self) -> int:
    """How many Papers in the Corpus cover this Topic and carry a year."""
    return sum(self.counts)


class TrendsData(BaseModel):
  """Everything the Trends view shows, already computed from the artifacts."""

  years: list[int]
  topics: list[TopicTrend]
  corpus_paper_count: int
  papers_with_year: int
  papers_without_year: int

  @property
  def emerging(self) -> list[TopicTrend]:
    """Topics that first appear only in the recent half of the Corpus's span."""
    return [topic for topic in self.topics if topic.status == "emerging"]

  @property
  def abandoned(self) -> list[TopicTrend]:
    """Topics the Corpus stopped studying before the recent half of its span."""
    return [topic for topic in self.topics if topic.status == "abandoned"]

  @property
  def plotted_topics(self) -> list[TopicTrend]:
    """
    The Topics drawn as lines, at most ``MAX_TREND_LINES``, in volume order.

    Emerging and abandoned Topics claim slots first because they are what the
    chart exists to show; the remaining slots go to the most-covered steady
    Topics.
    """
    ranked = sorted(
      range(len(self.topics)),
      key=lambda index: (self.topics[index].status == "steady", index),
    )
    kept = sorted(ranked[:MAX_TREND_LINES])
    return [self.topics[index] for index in kept]

  @property
  def tabled_topics(self) -> list[TopicTrend]:
    """The Topics past the line cap, listed in a table in volume order."""
    plotted = {topic.category_id for topic in self.plotted_topics}
    return [topic for topic in self.topics if topic.category_id not in plotted]

  @property
  def has_data(self) -> bool:
    """Whether any Topic could be placed on the timeline."""
    return bool(self.years and self.topics)


def build_trends(
  normalized: NormalizedFactsArtifact, manifest: ManifestArtifact
) -> TrendsData:
  """
  Count Papers per Topic per year, and flag emerging and abandoned Topics.

  A Topic's line is built only from Papers with a known year; Papers without one
  are counted and reported so the denominator travels with the chart. A Topic is
  "emerging" when its first Paper falls in the recent half of the Corpus's span
  and "abandoned" when its last Paper falls before it; otherwise it is "steady".
  """
  years_by_key = {paper.citation_key: paper.year for paper in manifest.papers}
  corpus_keys = set(years_by_key)

  dated: list[tuple[str, int]] = []  # (citation_key, year), Corpus Papers only
  without_year = 0
  for facts in normalized.normalized:
    if facts.citation_key not in corpus_keys:
      continue
    year = years_by_key[facts.citation_key]
    if year is None:
      without_year += 1
    else:
      dated.append((facts.citation_key, year))

  years = sorted({year for _, year in dated})
  topics = _topic_trends(normalized, dict(dated), years)
  return TrendsData(
    years=years,
    topics=topics,
    corpus_paper_count=len(corpus_keys),
    papers_with_year=len(dated),
    papers_without_year=without_year,
  )


def _topic_trends(
  normalized: NormalizedFactsArtifact,
  year_by_key: dict[str, int],
  years: list[int],
) -> list[TopicTrend]:
  """Build one dated TopicTrend per Topic any Paper with a year was placed on."""
  if not years:
    return []
  midpoint = (years[0] + years[-1]) / 2
  labels: dict[str, str] = {}
  per_topic_years: dict[str, list[int]] = {}
  for facts in normalized.normalized:
    year = year_by_key.get(facts.citation_key)
    if year is None:
      continue
    for category_id in _topic_ids(facts):
      labels.setdefault(category_id, _topic_label(facts, category_id))
      per_topic_years.setdefault(category_id, []).append(year)

  trends: list[TopicTrend] = []
  for category_id, topic_years in per_topic_years.items():
    counts = [topic_years.count(year) for year in years]
    first_year, last_year = min(topic_years), max(topic_years)
    trends.append(
      TopicTrend(
        category_id=category_id,
        label=labels[category_id],
        counts=counts,
        status=_trend_status(first_year, last_year, midpoint),
        first_year=first_year,
        last_year=last_year,
      )
    )
  trends.sort(key=lambda topic: (-topic.total, topic.label))
  return trends


def _topic_ids(facts) -> list[str]:  # noqa: ANN001 - read-model duck typing
  """Return the distinct Topic-axis category ids one Paper's NormalizedFacts holds."""
  return sorted({a.category_id for a in facts.assignments if a.axis == "topic"})


def _topic_label(facts, category_id: str) -> str:  # noqa: ANN001
  """Return the readable label for a Topic category on one Paper, or its id."""
  for assignment in facts.assignments:
    if assignment.axis == "topic" and assignment.category_id == category_id:
      return assignment.category_label or category_id
  return category_id


def _trend_status(first_year: int, last_year: int, midpoint: float) -> TrendStatus:
  """Flag a Topic emerging (recent-only), abandoned (early-only), or steady."""
  if first_year > midpoint:
    return "emerging"
  if last_year < midpoint:
    return "abandoned"
  return "steady"


def build_trends_figure(trends: TrendsData) -> go.Figure:
  """
  Build the Plotly line chart of publication volume per Topic over time.

  Emerging Topics are drawn solid and abandoned Topics dashed, so the lines the
  eye should catch stand out; steady Topics stay in the background. Each line
  carries its own palette colour and marker symbol; Topics past the line cap
  are left to the table (``TrendsData.tabled_topics``).
  """
  figure = go.Figure()
  for topic, colour, symbol in zip(
    trends.plotted_topics, TRENDS_PALETTE, TRENDS_MARKERS, strict=False
  ):
    dash = "solid" if topic.status != "abandoned" else "dash"
    width = 3 if topic.status in ("emerging", "abandoned") else 1.5
    figure.add_trace(
      go.Scatter(
        x=trends.years,
        y=topic.counts,
        mode="lines+markers",
        name=f"{topic.label}{text.TRENDS_STATUS_SUFFIX.get(topic.status, '')}",
        line={"dash": dash, "width": width, "color": colour},
        marker={"symbol": symbol, "color": colour, "size": 8},
      )
    )
  figure.update_layout(
    xaxis_title=text.TRENDS_X_TITLE,
    yaxis_title=text.TRENDS_Y_TITLE,
    xaxis={"dtick": 1},
    legend_title=text.TRENDS_LEGEND_TITLE,
  )
  return figure


def trends_cap_caption(trends: TrendsData) -> str | None:
  """State how many Topics the line cap moved to the table, or None if none."""
  tabled = len(trends.tabled_topics)
  if not tabled:
    return None
  return text.TRENDS_CAPPED.format(
    shown=len(trends.topics) - tabled, total=len(trends.topics), tabled=tabled
  )


def trends_table_rows(trends: TrendsData) -> list[dict[str, str | int]]:
  """One row per Topic past the line cap: its volume, span, and status."""
  return [
    {
      text.TRENDS_COL_TOPIC: topic.label,
      text.TRENDS_COL_PAPERS: topic.total,
      text.TRENDS_COL_FIRST_YEAR: topic.first_year,
      text.TRENDS_COL_LAST_YEAR: topic.last_year,
      text.TRENDS_COL_STATUS: text.TRENDS_STATUS_LABEL[topic.status],
    }
    for topic in trends.tabled_topics
  ]


def trends_text_summary(trends: TrendsData) -> str:
  """
  State the Trends chart in words: its lines, span, denominator and highlights.

  The text alternative for readers who cannot see the lines (chart audit C7);
  the per-year counts of Topics past the line cap are in the table already.
  """
  if not trends.has_data:
    return text.TRENDS_NO_DATA
  years = trends.years
  span = f"{years[0]}\u2013{years[-1]}" if years[0] != years[-1] else str(years[0])
  top = trends.topics[0]
  return text.TRENDS_ALT.format(
    shown=len(trends.plotted_topics),
    topics=len(trends.topics),
    span=span,
    with_year=trends.papers_with_year,
    total=trends.corpus_paper_count,
    top=top.label,
    top_total=top.total,
    emerging=len(trends.emerging),
    abandoned=len(trends.abandoned),
  )
