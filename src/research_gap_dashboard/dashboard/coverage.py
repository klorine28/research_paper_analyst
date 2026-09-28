"""
Assembling the Coverage Matrix heatmap and the Trends view from the artifacts.

These are plain functions with no Streamlit dependency: the layout layer stays
thin by asking here for finished data and finished Plotly figures. Two things
this module is careful about, both from CODING_STANDARDS.md > Research integrity:

- An empty Coverage Matrix cell means "no Paper in the Corpus combines these
  two categories". It is a fact about the Corpus, not a measured value of zero,
  so the heatmap renders it in its own colour and labels it "no papers" rather
  than painting it as the low end of the count scale.
- Every count states the denominator it was computed over, and Papers with no
  known year are reported, not silently dropped from the Trends view.

The Trends view reads which Topics each Paper covers from the NormalizedFacts
artifact and the Papers' years from the CorpusManifest, so it never imports a
pipeline stage (ADR 0002).
"""

import math
from typing import Literal

import plotly.graph_objects as go
from pydantic import BaseModel

from research_gap_dashboard.dashboard import text
from research_gap_dashboard.dashboard.artifacts import (
  CandidateGapsArtifact,
  CoverageMatrixRecord,
  ManifestArtifact,
  NormalizedFactsArtifact,
)

# The value the heatmap uses for a cell no Paper covers: NaN, so Plotly paints
# it in the figure background colour instead of at the bottom of the count scale.
_NO_PAPERS_Z = math.nan

TrendStatus = Literal["emerging", "abandoned", "steady"]

# Human labels for the axes a Coverage Matrix can be built over, so the picker
# reads "Method" rather than "method" (kept here, not in detect: ADR 0002).
_AXIS_LABELS: dict[str, str] = {
  "topic": "Topic",
  "method": "Method",
  "population": "Population",
  "dataset": "Dataset",
}


def _axis_label(axis: str) -> str:
  """Return the human label for an axis id, falling back to the id itself."""
  return _AXIS_LABELS.get(axis, axis.capitalize())


class CoverageMatrixView(BaseModel):
  """One Coverage Matrix laid out as a grid the heatmap can render directly."""

  key: str
  column_axis: str
  option_label: str
  row_axis: str
  row_labels: list[str]
  column_labels: list[str]
  counts: list[list[int]]
  corpus_paper_count: int

  @property
  def is_empty(self) -> bool:
    """Whether the matrix has any rows and columns to show."""
    return not self.row_labels or not self.column_labels


def build_coverage_matrices(
  artifact: CandidateGapsArtifact,
) -> list[CoverageMatrixView]:
  """
  Lay out every Coverage Matrix the CandidateGaps artifact holds as a grid.

  Only matrices that have at least one row and one column are returned, so the
  axis picker never offers a matrix with nothing to show (an axis this Corpus
  never touched contributes no rows or columns; see the detect stage).
  """
  views = [_matrix_view(matrix) for matrix in artifact.matrices]
  return [view for view in views if not view.is_empty]


def _matrix_view(matrix: CoverageMatrixRecord) -> CoverageMatrixView:
  """Turn one artifact Coverage Matrix into a dense counts grid for the heatmap."""
  row_ids = [row.category_id for row in matrix.rows]
  column_ids = [column.category_id for column in matrix.columns]
  cell_counts = {
    (cell.row_id, cell.column_id): cell.paper_count for cell in matrix.cells
  }
  counts = [
    [cell_counts.get((row_id, column_id), 0) for column_id in column_ids]
    for row_id in row_ids
  ]
  column_label = _axis_label(matrix.column_axis)
  option_label = (
    text.HEATMAP_TOPIC_PAIR_OPTION
    if matrix.row_axis == matrix.column_axis
    else column_label
  )
  return CoverageMatrixView(
    key=f"{matrix.row_axis}x{matrix.column_axis}",
    column_axis=matrix.column_axis,
    option_label=option_label,
    row_axis=matrix.row_axis,
    row_labels=[row.label or row.category_id for row in matrix.rows],
    column_labels=[column.label or column.category_id for column in matrix.columns],
    counts=counts,
    corpus_paper_count=matrix.corpus_paper_count,
  )


def build_heatmap_figure(view: CoverageMatrixView) -> go.Figure:
  """
  Build the Plotly heatmap for one Coverage Matrix.

  Cells no Paper covers carry NaN (painted in the figure background colour) and
  are labelled "no papers"; covered cells carry their count on a colour scale
  that starts at one, so a low count never looks the same as no papers at all.
  """
  z = [
    [float(count) if count > 0 else _NO_PAPERS_Z for count in row]
    for row in view.counts
  ]
  cell_text = [
    [str(count) if count > 0 else text.HEATMAP_NO_PAPERS for count in row]
    for row in view.counts
  ]
  max_count = max((count for row in view.counts for count in row), default=0)
  figure = go.Figure(
    data=go.Heatmap(
      z=z,
      x=view.column_labels,
      y=view.row_labels,
      text=cell_text,
      texttemplate="%{text}",
      colorscale="Blues",
      zmin=1,
      zmax=max(max_count, 1),
      hoverongaps=False,
      colorbar={"title": text.HEATMAP_COLORBAR_TITLE},
      xgap=2,
      ygap=2,
    )
  )
  figure.update_layout(
    xaxis_title=_axis_label(view.column_axis),
    yaxis_title=_axis_label(view.row_axis),
    plot_bgcolor=text.HEATMAP_NO_PAPERS_COLOR,
    yaxis={"autorange": "reversed"},
  )
  return figure


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
  eye should catch stand out; steady Topics stay in the background.
  """
  figure = go.Figure()
  for topic in trends.topics:
    dash = "solid" if topic.status != "abandoned" else "dash"
    width = 3 if topic.status in ("emerging", "abandoned") else 1.5
    figure.add_trace(
      go.Scatter(
        x=trends.years,
        y=topic.counts,
        mode="lines+markers",
        name=f"{topic.label}{text.TRENDS_STATUS_SUFFIX.get(topic.status, '')}",
        line={"dash": dash, "width": width},
      )
    )
  figure.update_layout(
    xaxis_title=text.TRENDS_X_TITLE,
    yaxis_title=text.TRENDS_Y_TITLE,
    xaxis={"dtick": 1},
    legend_title=text.TRENDS_LEGEND_TITLE,
  )
  return figure
