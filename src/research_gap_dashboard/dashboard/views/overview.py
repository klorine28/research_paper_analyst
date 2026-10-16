"""The Corpus Overview page: counts, completeness gauge, chart gallery, exclusions."""

from pathlib import Path

import streamlit as st

from research_gap_dashboard.dashboard import text
from research_gap_dashboard.dashboard.artifacts import (
  CorpusChoice,
  has_extractions,
  has_normalized_facts,
  load_extractions,
  load_manifest,
  load_normalized_facts,
)
from research_gap_dashboard.dashboard.attention import (
  build_attention,
)
from research_gap_dashboard.dashboard.overview import (
  AxisFrequency,
  CompletenessGauge,
  FactDensity,
  OverviewData,
  build_overview,
)
from research_gap_dashboard.dashboard.suggestions import load_suggested_papers
from research_gap_dashboard.dashboard.views.run_controls import (
  render_last_run,
  render_run_buttons,
)
from research_gap_dashboard.dashboard.views.widgets import (
  render_mini_bar,
  render_tile_gallery,
)

# The Corpus Overview chart gallery: each descriptive chart is a clickable tile
# in its own shade of blue, so the four charts read as distinct identities
# rather than one long scroll (issue #45 follow-up). Order here is the order the
# tiles and their expanded panels appear in.
_OVERVIEW_TILES: list[tuple[str, str, str]] = [
  ("papers_year", text.PAPERS_PER_YEAR_HEADING, "#e8f1fc"),
  ("venues", text.VENUES_HEADING, "#cfe3f8"),
  ("axes", text.AXIS_FREQUENCIES_HEADING, "#b6d4f2"),
  ("facts", text.FACT_DENSITY_HEADING, "#9dc4ec"),
]


OVERVIEW_OPEN_KEY = "overview_open_charts"


def render_page(chosen: CorpusChoice) -> None:
  """Render the Corpus Overview page for the chosen Corpus."""
  root = chosen.root
  normalized = load_normalized_facts(root) if has_normalized_facts(root) else None
  extractions = load_extractions(root) if has_extractions(root) else None
  completeness = build_attention(root).banner
  render_overview(
    build_overview(
      load_manifest(root),
      normalized=normalized,
      extractions=extractions,
      completeness=completeness,
    ),
    root,
  )


def render_overview(overview: OverviewData, corpus_root: Path) -> None:
  """Render the Corpus Overview page from already-computed data."""
  st.header(text.OVERVIEW_HEADING)
  render_last_run(corpus_root)
  st.info(overview.scope_statement)
  # A paper the researcher could not obtain is part of the scope: the Corpus
  # is missing relevant work, and says so (ADR 0009 \u00a712).
  unavailable = len(load_suggested_papers(corpus_root).unavailable)
  if unavailable:
    st.caption(text.OVERVIEW_UNAVAILABLE_DISCLOSURE.format(count=unavailable))

  paper_col, venue_col, year_col = st.columns(3)
  paper_col.metric(text.METRIC_PAPER_COUNT, overview.paper_count)
  venue_col.metric(text.METRIC_VENUE_COUNT, overview.venue_count)
  year_col.metric(text.METRIC_YEAR_SPAN, overview.year_span)

  _render_completeness(overview.completeness, corpus_root)
  _render_overview_gallery(overview, corpus_root)

  st.subheader(text.EXCLUSIONS_HEADING)
  if not overview.has_exclusions:
    st.success(text.NO_EXCLUSIONS)
    return
  if overview.unmatched_entries:
    st.caption(text.UNMATCHED_ENTRIES_LABEL)
    st.table(
      [
        {text.COLUMN_ENTRY: entry.label, text.COLUMN_REASON: entry.reason}
        for entry in overview.unmatched_entries
      ]
    )
  if overview.orphan_pdfs:
    st.caption(text.ORPHAN_PDFS_LABEL)
    st.table([{text.COLUMN_FILE: name} for name in overview.orphan_pdfs])


def _render_completeness(gauge: CompletenessGauge | None, corpus_root: Path) -> None:
  """Render the prominent parse/extract completeness gauge: the honesty meter."""
  st.subheader(text.COMPLETENESS_HEADING)
  st.caption(text.COMPLETENESS_QUESTION)
  if gauge is None or gauge.total_papers == 0:
    st.info(text.COMPLETENESS_NO_DATA)
    render_run_buttons(corpus_root, ["parse", "extract"], scope="completeness")
    return
  st.progress(
    gauge.reached_fraction,
    text=text.COMPLETENESS_PROGRESS.format(
      reached=gauge.reached_papers,
      total=gauge.total_papers,
      percent=gauge.reached_percent,
    ),
  )
  if gauge.is_complete:
    st.success(text.COMPLETENESS_COMPLETE)
  else:
    st.warning(
      text.completeness_caveat(gauge.missing_papers, gauge.papers_with_dropped_facts)
    )


def _render_overview_gallery(overview: OverviewData, corpus_root: Path) -> None:
  """Render the 2x2 grid of coloured preview tiles and the expanded panels."""
  st.subheader(text.OVERVIEW_GALLERY_HEADING)
  st.caption(text.OVERVIEW_GALLERY_HINT)
  render_tile_gallery(
    "ovtile",
    _OVERVIEW_TILES,
    OVERVIEW_OPEN_KEY,
    lambda chart_id: _render_tile_preview(chart_id, overview),
    lambda chart_id: _render_overview_panel_body(chart_id, overview, corpus_root),
  )


def _render_tile_preview(chart_id: str, overview: OverviewData) -> None:
  """Render a clean thumbnail of a chart inside its tile (labels on hover)."""
  if chart_id == "papers_year":
    buckets = overview.papers_per_year
    st.caption(
      text.PREVIEW_PAPERS_YEAR.format(
        count=overview.paper_count, span=overview.year_span
      )
    )
    render_mini_bar(
      [b.label for b in buckets], [b.count for b in buckets], key=chart_id
    )
  elif chart_id == "venues":
    top_venues = overview.venues[:8]
    if top_venues:
      st.caption(
        text.PREVIEW_VENUES.format(
          count=overview.venue_count,
          top=top_venues[0].venue,
          n=top_venues[0].count,
        )
      )
    render_mini_bar(
      [v.venue for v in top_venues], [v.count for v in top_venues], key=chart_id
    )
  elif chart_id == "axes":
    if overview.axis_frequencies:
      axis = overview.axis_frequencies[0]
      top = axis.categories[:8]
      st.caption(
        text.PREVIEW_AXES.format(
          count=len(axis.categories),
          axis=axis.label,
          top=top[0].label,
          n=top[0].count,
        )
      )
      render_mini_bar([c.label for c in top], [c.count for c in top], key=chart_id)
    else:
      st.caption(text.OVERVIEW_TILE_PREVIEW_EMPTY)
  elif chart_id == "facts":
    density = overview.fact_density
    if density is not None and density.papers:
      st.caption(
        text.PREVIEW_FACTS.format(facts=density.total_facts, mean=density.mean)
      )
      render_mini_bar(
        [p.label for p in density.papers[:8]],
        [p.count for p in density.papers[:8]],
        key=chart_id,
      )
    else:
      st.caption(text.OVERVIEW_TILE_PREVIEW_EMPTY)


def _render_overview_panel_body(
  chart_id: str, overview: OverviewData, corpus_root: Path
) -> None:
  """Render one expanded overview chart (the coloured header is drawn generically)."""
  if chart_id == "papers_year":
    _render_papers_per_year(overview)
  elif chart_id == "venues":
    _render_venues(overview)
  elif chart_id == "axes":
    _render_axis_frequencies(
      overview.axis_frequencies, overview.paper_count, corpus_root
    )
  elif chart_id == "facts":
    _render_fact_density(overview.fact_density, corpus_root)


def _render_papers_per_year(overview: OverviewData) -> None:
  """Render the papers-per-year bar chart with its how-to-read explainer."""
  with st.expander(text.HOW_TO_READ_LABEL):
    st.markdown(text.PAPERS_PER_YEAR_EXPLAINER)
  st.bar_chart(
    {
      text.COLUMN_PAPERS: {
        bucket.label: bucket.count for bucket in overview.papers_per_year
      }
    }
  )


def _render_venues(overview: OverviewData) -> None:
  """Render the venues table with its how-to-read explainer."""
  with st.expander(text.HOW_TO_READ_LABEL):
    st.markdown(text.VENUES_EXPLAINER)
  st.table(
    [
      {text.COLUMN_VENUE: venue.venue, text.COLUMN_PAPERS: venue.count}
      for venue in overview.venues
    ]
  )


def _render_axis_frequencies(
  axes: list[AxisFrequency], total_papers: int, corpus_root: Path
) -> None:
  """Render one bar chart per axis: papers per Topic, Method, Population, Dataset."""
  with st.expander(text.HOW_TO_READ_LABEL):
    st.markdown(text.AXIS_FREQUENCIES_EXPLAINER)
  if not axes:
    st.info(text.AXIS_FREQUENCIES_NO_DATA)
    render_run_buttons(corpus_root, ["aggregate"], scope="axes")
    return
  for axis in axes:
    st.caption(
      text.AXIS_DENOMINATOR.format(
        placed=axis.papers_placed, total=total_papers, axis=axis.label
      )
    )
    st.bar_chart(
      {
        text.COLUMN_PAPERS: {
          category.label: category.count for category in axis.categories
        }
      }
    )


def _render_fact_density(density: FactDensity | None, corpus_root: Path) -> None:
  """Render the facts-per-paper extraction-density chart and its explainer."""
  with st.expander(text.HOW_TO_READ_LABEL):
    st.markdown(text.FACT_DENSITY_EXPLAINER)
  if density is None or not density.papers:
    st.info(text.FACT_DENSITY_NO_DATA)
    render_run_buttons(corpus_root, ["extract"], scope="facts")
    return
  st.caption(
    text.FACT_DENSITY_SUMMARY.format(
      facts=density.total_facts,
      papers=density.papers_with_facts,
      mean=density.mean,
    )
  )
  st.bar_chart(
    {text.COLUMN_FACTS: {paper.label: paper.count for paper in density.papers}}
  )
