"""
The Streamlit app shell: corpus selection, navigation, and the Corpus Overview.

This layer is deliberately thin. It reads on-disk artifacts through
`dashboard.artifacts`, asks `dashboard.overview` for finished data, and renders
it; it holds no analysis logic and imports no pipeline stage (ADR 0002). Run it
with `streamlit run` (see the justfile). Point it at the folder that holds your
corpus directories with the RESEARCH_GAP_CORPORA_DIR environment variable.
"""

import os
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

import plotly.graph_objects as go
import streamlit as st

from research_gap_dashboard.analytics import (
  CorpusPaper,
  GapFact,
  GroundedChatAnswer,
  GroundedNarrative,
  GroundingContext,
  LlmClient,
  answer_question,
  build_analytics_client,
  summarize_gaps,
)
from research_gap_dashboard.dashboard import text
from research_gap_dashboard.dashboard.chat_history import (
  ChatTurn,
  Role,
  append_turn,
  clear_chat_history,
  load_chat_history,
)
from research_gap_dashboard.dashboard.artifacts import (
  CorpusChoice,
  ManifestArtifact,
  discover_corpora,
  explained_citation_keys,
  has_candidate_gaps,
  has_extractions,
  has_manifest,
  has_normalized_facts,
  has_paper_explanation,
  has_retrieval_gaps,
  load_candidate_gaps,
  load_extractions,
  load_manifest,
  load_normalized_facts,
  load_paper_explanation,
  load_retrieval_gaps,
)
from research_gap_dashboard.dashboard.comparison import (
  MAX_SELECTION,
  AxisComparison,
  ComparisonResult,
  FieldComparison,
  build_comparison,
  selection_error,
)
from research_gap_dashboard.dashboard.coverage import (
  CoverageMatrixView,
  TrendsData,
  build_coverage_matrices,
  build_heatmap_figure,
  build_trends,
  build_trends_figure,
)
from research_gap_dashboard.dashboard.meta_analysis import (
  NetworkGraph,
  SankeyGraph,
  build_author_collaboration,
  build_citation_network,
  build_cooccurrence,
  build_limitation_flow,
  build_network_figure,
  build_sankey_figure,
)
from research_gap_dashboard.dashboard.gaps import (
  GapCard,
  GapCardsData,
  build_gap_cards,
  scope_context_to_gap,
)
from research_gap_dashboard.dashboard.judgments import (
  clear_judgment,
  load_judgments,
  record_judgment,
)
from research_gap_dashboard.dashboard.limitations import (
  LimitationGroupView,
  LimitationsData,
  build_limitations,
)
from research_gap_dashboard.dashboard.explainer import (
  PaperExplainerView,
  PaperMenuData,
  PaperMenuEntry,
  build_paper_explainer,
  build_paper_menu,
)
from research_gap_dashboard.dashboard.attention import (
  AttentionReport,
  build_attention,
)
from research_gap_dashboard.dashboard.parse_corrections import (
  EmptyCorrectionError,
  record_parse_correction,
)
from research_gap_dashboard.dashboard.overview import (
  AxisFrequency,
  CompletenessGauge,
  FactDensity,
  OverviewData,
  build_overview,
)
from research_gap_dashboard.dashboard.extraction_review import (
  UnknownFieldError,
  add_fact,
  clear_fact_verdict,
  load_extraction_review,
  record_fact_verdict,
  remove_added_fact,
)
from research_gap_dashboard.dashboard.evidence_context import ExpandedEvidence
from research_gap_dashboard.dashboard.grounding import (
  UngroundedEvidenceError,
  has_parsed_paper,
  read_parsed_paper,
)
from research_gap_dashboard.dashboard.verify import (
  PaperReviewView,
  ReviewFactView,
  ReviewFieldView,
  build_extraction_review,
)
from research_gap_dashboard.dashboard.retrieval import (
  RetrievalCandidateView,
  RetrievalGapsData,
  build_retrieval_gaps,
)

CORPORA_DIR_ENV = "RESEARCH_GAP_CORPORA_DIR"
DEFAULT_CORPORA_DIR = "corpora"


def _corpora_root() -> Path:
  """Return the folder that holds the corpus directories to offer."""
  return Path(os.environ.get(CORPORA_DIR_ENV, DEFAULT_CORPORA_DIR))


def _select_corpus(choices: list[CorpusChoice]) -> CorpusChoice:
  """Render the sidebar corpus picker and return the chosen corpus."""
  chosen = st.sidebar.selectbox(
    text.CORPUS_PICKER_LABEL,
    choices,
    format_func=lambda choice: choice.name,
  )
  return chosen


# Session-state keys. The nav radio carries a key so a gap card can switch the
# page to Conversational Analytics programmatically; the discuss key names the
# gap a scoped chat is focused on (issue #48).
NAV_PAGE_KEY = "nav_page"
PENDING_NAV_KEY = "pending_nav_page"
DISCUSS_GAP_KEY = "discuss_gap_id"


def _apply_pending_nav() -> None:
  """
  Honour a page switch requested last run, before the nav radio is built.

  A widget's state key can't be set once its widget exists, so a card's "Discuss
  this gap" button records the target page here and this runs before the radio is
  instantiated (issue #48).
  """
  pending = st.session_state.pop(PENDING_NAV_KEY, None)
  if pending is not None:
    st.session_state[NAV_PAGE_KEY] = pending


def _select_page() -> str:
  """Render the sidebar page picker and return the chosen page."""
  return st.sidebar.radio(
    text.NAV_LABEL,
    [
      text.PAGE_OVERVIEW,
      text.PAGE_COVERAGE,
      text.PAGE_META,
      text.PAGE_GAP_CARDS,
      text.PAGE_LIMITATIONS,
      text.PAGE_RETRIEVAL,
      text.PAGE_EXPLAINER,
      text.PAGE_COMPARISON,
      text.PAGE_ANALYTICS,
      text.PAGE_VERIFY,
      text.PAGE_ATTENTION,
    ],
    key=NAV_PAGE_KEY,
    label_visibility="collapsed",
  )


def render_attention(report: AttentionReport, corpus_root: Path) -> None:
  """Render the needs-manual-attention queue and per-Paper correction boxes."""
  st.header(text.ATTENTION_HEADING)
  banner = report.banner
  if banner.total_papers == 0:
    st.info(text.ATTENTION_NO_PIPELINE)
    return
  if banner.is_complete:
    st.success(text.ATTENTION_ALL_CLEAR)
    return

  st.warning(
    text.incompleteness_banner(
      banner.missing_papers,
      banner.papers_with_dropped_facts,
      banner.total_papers,
      banner.reached_artifact,
    )
  )

  _render_attention_section(
    report, corpus_root, "unparsed", text.ATTENTION_UNPARSED_HEADING, correct=True
  )
  _render_attention_section(
    report,
    corpus_root,
    "extraction_failed",
    text.ATTENTION_REJECTED_HEADING,
    correct=True,
  )
  _render_attention_section(
    report, corpus_root, "facts_dropped", text.ATTENTION_DROPPED_HEADING, correct=False
  )


def _render_attention_section(
  report: AttentionReport,
  corpus_root: Path,
  kind: str,
  heading: str,
  *,
  correct: bool,
) -> None:
  """Render one group of the queue, offering a correction box where it helps."""
  items = [item for item in report.items if item.kind == kind]
  if not items:
    return
  st.subheader(f"{heading} ({len(items)})")
  for item in items:
    with st.expander(f"{item.citation_key} — {item.title}"):
      st.write(item.reason)
      if correct:
        _render_correction_box(corpus_root, item.citation_key)


def _render_correction_box(corpus_root: Path, citation_key: str) -> None:
  """Render the paste-the-correct-text box that writes a judgments/ overlay."""
  pasted = st.text_area(
    text.ATTENTION_CORRECTION_LABEL,
    key=f"parse-correction-{citation_key}",
    help=text.ATTENTION_CORRECTION_HELP,
  )
  if not st.button(
    text.ATTENTION_SAVE_CORRECTION, key=f"save-correction-{citation_key}"
  ):
    return
  try:
    record_parse_correction(corpus_root, citation_key, pasted)
  except EmptyCorrectionError:
    st.warning(text.ATTENTION_CORRECTION_EMPTY)
    return
  st.success(text.ATTENTION_CORRECTION_SAVED)


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
_OVERVIEW_TILE_TEXT = "#0b2545"
OVERVIEW_OPEN_KEY = "overview_open_charts"


def render_overview(overview: OverviewData) -> None:
  """Render the Corpus Overview page from already-computed data."""
  st.header(text.OVERVIEW_HEADING)
  st.info(overview.scope_statement)

  paper_col, venue_col, year_col = st.columns(3)
  paper_col.metric(text.METRIC_PAPER_COUNT, overview.paper_count)
  venue_col.metric(text.METRIC_VENUE_COUNT, overview.venue_count)
  year_col.metric(text.METRIC_YEAR_SPAN, overview.year_span)

  _render_completeness(overview.completeness)
  _render_overview_gallery(overview)

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


def _render_completeness(gauge: CompletenessGauge | None) -> None:
  """Render the prominent parse/extract completeness gauge: the honesty meter."""
  st.subheader(text.COMPLETENESS_HEADING)
  st.caption(text.COMPLETENESS_QUESTION)
  if gauge is None or gauge.total_papers == 0:
    st.info(text.COMPLETENESS_NO_DATA)
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


_PREVIEW_HEIGHT = 150


def _render_overview_gallery(overview: OverviewData) -> None:
  """Render the 2x2 grid of coloured preview tiles and the expanded panels."""
  st.subheader(text.OVERVIEW_GALLERY_HEADING)
  st.caption(text.OVERVIEW_GALLERY_HINT)
  _inject_tile_styles()

  # Every tile starts collapsed to a miniature; the researcher expands the ones
  # they want full size (issue #45 follow-up).
  open_charts: set[str] = st.session_state.setdefault(OVERVIEW_OPEN_KEY, set())
  for row_start in range(0, len(_OVERVIEW_TILES), 2):
    columns = st.columns(2)
    for (chart_id, title, _shade), column in zip(
      _OVERVIEW_TILES[row_start : row_start + 2], columns
    ):
      with column, st.container(key=f"ovtile_{chart_id}", border=True):
        st.markdown(f"**{title}**")
        _render_tile_preview(chart_id, overview)
        is_open = chart_id in open_charts
        label = text.OVERVIEW_TILE_COLLAPSE if is_open else text.OVERVIEW_TILE_EXPAND
        if st.button(label, key=f"ovtile_btn_{chart_id}", use_container_width=True):
          open_charts.symmetric_difference_update({chart_id})
          st.rerun()

  for chart_id, title, shade in _OVERVIEW_TILES:
    if chart_id in open_charts:
      _render_overview_panel(chart_id, title, shade, overview)


def _inject_tile_styles() -> None:
  """
  Tint each overview tile its own shade of blue, keeping its text readable.

  The tile background is a light blue, but Streamlit text inherits the active
  theme's font colour (light in dark mode), which would vanish on a light tile.
  So the tile's text is pinned dark and the expand button is given an explicit
  light background, making both legible on every shade and in either theme.
  """
  blocks = []
  for chart_id, _title, shade in _OVERVIEW_TILES:
    key = f".st-key-ovtile_{chart_id}"
    blocks.append(
      f"{key}{{background:{shade};border-radius:0.6rem;}}"
      f"{key} p,{key} label,{key} strong,{key} span,"
      f"{key} h1,{key} h2,{key} h3{{color:{_OVERVIEW_TILE_TEXT} !important;}}"
      f"{key} button{{background:#ffffff !important;"
      f"color:{_OVERVIEW_TILE_TEXT} !important;"
      "border:1px solid rgba(11,37,69,0.25) !important;}"
      f"{key} button:hover{{background:#eef4fc !important;"
      f"color:{_OVERVIEW_TILE_TEXT} !important;}}"
    )
  st.markdown(f"<style>{''.join(blocks)}</style>", unsafe_allow_html=True)


def _render_tile_preview(chart_id: str, overview: OverviewData) -> None:
  """Render a clean thumbnail of a chart inside its tile (labels on hover)."""
  if chart_id == "papers_year":
    buckets = overview.papers_per_year
    st.caption(
      text.PREVIEW_PAPERS_YEAR.format(
        count=overview.paper_count, span=overview.year_span
      )
    )
    _render_mini_bar(
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
    _render_mini_bar(
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
      _render_mini_bar([c.label for c in top], [c.count for c in top], key=chart_id)
    else:
      st.caption(text.OVERVIEW_TILE_PREVIEW_EMPTY)
  elif chart_id == "facts":
    density = overview.fact_density
    if density is not None and density.papers:
      st.caption(
        text.PREVIEW_FACTS.format(facts=density.total_facts, mean=density.mean)
      )
      _render_mini_bar(
        [p.label for p in density.papers[:8]],
        [p.count for p in density.papers[:8]],
        key=chart_id,
      )
    else:
      st.caption(text.OVERVIEW_TILE_PREVIEW_EMPTY)


def _render_mini_bar(labels: list[str], values: list[int], *, key: str) -> None:
  """
  Render a compact bar thumbnail: value labels on bars, full names on hover.

  A miniature shows the shape of a distribution at a glance with a little
  detail. Long category names (paper titles, venue names) would swallow a fixed
  small chart, so the x ticks stay hidden and each bar instead carries its
  count; the full name is on hover and in the expanded panel below.
  """
  if not labels:
    st.caption(text.OVERVIEW_TILE_PREVIEW_EMPTY)
    return
  figure = go.Figure(
    go.Bar(
      x=list(range(len(labels))),
      y=values,
      marker_color="#2f6fb3",
      customdata=labels,
      text=values,
      textposition="outside",
      textfont={"size": 10, "color": "#0b2545"},
      cliponaxis=False,
      hovertemplate="%{customdata}: %{y}<extra></extra>",
    )
  )
  headroom = max(values) * 1.25 if values else 1
  figure.update_layout(
    height=_PREVIEW_HEIGHT,
    margin={"l": 6, "r": 6, "t": 14, "b": 6},
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="rgba(0,0,0,0)",
    showlegend=False,
    bargap=0.25,
    xaxis={"visible": False, "fixedrange": True},
    yaxis={"visible": False, "fixedrange": True, "range": [0, headroom]},
  )
  st.plotly_chart(
    figure,
    use_container_width=True,
    config={"displayModeBar": False},
    key=f"ovmini_{key}",
  )


def _render_overview_panel(
  chart_id: str, title: str, shade: str, overview: OverviewData
) -> None:
  """Render one expanded chart panel: coloured header, how-to-read, the chart."""
  st.markdown(
    f"<div style='background:{shade};color:{_OVERVIEW_TILE_TEXT};"
    "padding:0.45rem 0.9rem;border-radius:0.4rem;font-weight:600;"
    f"margin-top:0.6rem;'>{title}</div>",
    unsafe_allow_html=True,
  )
  if chart_id == "papers_year":
    _render_papers_per_year(overview)
  elif chart_id == "venues":
    _render_venues(overview)
  elif chart_id == "axes":
    _render_axis_frequencies(overview.axis_frequencies, overview.paper_count)
  elif chart_id == "facts":
    _render_fact_density(overview.fact_density)


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


def _render_axis_frequencies(axes: list[AxisFrequency], total_papers: int) -> None:
  """Render one bar chart per axis: papers per Topic, Method, Population, Dataset."""
  with st.expander(text.HOW_TO_READ_LABEL):
    st.markdown(text.AXIS_FREQUENCIES_EXPLAINER)
  if not axes:
    st.info(text.AXIS_FREQUENCIES_NO_DATA)
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


def _render_fact_density(density: FactDensity | None) -> None:
  """Render the facts-per-paper extraction-density chart and its explainer."""
  with st.expander(text.HOW_TO_READ_LABEL):
    st.markdown(text.FACT_DENSITY_EXPLAINER)
  if density is None or not density.papers:
    st.info(text.FACT_DENSITY_NO_DATA)
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


def render_coverage(matrices: list[CoverageMatrixView], trends: TrendsData) -> None:
  """Render the Coverage Matrix heatmap and the Trends chart from finished data."""
  st.header(text.COVERAGE_HEADING)
  st.info(text.COVERAGE_INTRO)

  st.subheader(text.HEATMAP_HEADING)
  if not matrices:
    st.warning(text.HEATMAP_NO_MATRICES)
  else:
    with st.expander(text.HOW_TO_READ_LABEL):
      st.markdown(text.HEATMAP_EXPLAINER)
    chosen = st.selectbox(
      text.HEATMAP_AXIS_LABEL,
      matrices,
      format_func=lambda view: view.option_label,
    )
    st.caption(text.HEATMAP_DENOMINATOR.format(total=chosen.corpus_paper_count))
    st.plotly_chart(build_heatmap_figure(chosen), use_container_width=True)

  st.subheader(text.TRENDS_HEADING)
  _render_trends(trends)


def _render_trends(trends: TrendsData) -> None:
  """Render the Trends chart with its denominator and emerging/abandoned lines."""
  with st.expander(text.HOW_TO_READ_LABEL):
    st.markdown(text.TRENDS_EXPLAINER)
  if not trends.has_data:
    st.info(text.TRENDS_NO_DATA)
    return
  st.caption(
    text.TRENDS_DENOMINATOR.format(
      with_year=trends.papers_with_year, total=trends.corpus_paper_count
    )
  )
  if trends.papers_without_year:
    st.caption(text.TRENDS_MISSING_YEAR.format(without_year=trends.papers_without_year))
  st.plotly_chart(build_trends_figure(trends), use_container_width=True)

  emerging = trends.emerging
  abandoned = trends.abandoned
  if not emerging and not abandoned:
    st.caption(text.TRENDS_NO_HIGHLIGHTS)
    return
  if emerging:
    st.caption(
      text.TRENDS_EMERGING_LABEL.format(
        topics=", ".join(topic.label for topic in emerging)
      )
    )
  if abandoned:
    st.caption(
      text.TRENDS_ABANDONED_LABEL.format(
        topics=", ".join(topic.label for topic in abandoned)
      )
    )


def _card_status_label(card: GapCard) -> str:
  """Return the human label for a card's current accept/reject state."""
  if card.status == "accepted":
    return text.GAP_STATUS_ACCEPTED
  if card.status == "rejected":
    return text.GAP_STATUS_REJECTED
  return text.GAP_STATUS_UNDECIDED


def _render_gap_card(card: GapCard, corpus_root: Path) -> None:
  """Render one Candidate Gap card with its Evidence and accept/reject controls."""
  with st.container(border=True):
    st.markdown(f"**{card.gap_type_label}** \u2014 {card.title}")
    st.caption(
      text.GAP_CONFIDENCE_LABEL.format(confidence=card.confidence_label)
      + "  \u00b7  "
      + _card_status_label(card)
    )
    st.write(card.explanation)
    st.caption(card.confidence_reason)

    if card.cell_count is not None:
      st.caption(
        text.GAP_CELL_COUNT_LABEL.format(
          count=card.cell_count, total=card.corpus_paper_count
        )
      )
    if card.source_citation_keys:
      st.caption(
        text.GAP_SOURCE_PAPERS_LABEL.format(papers=", ".join(card.source_citation_keys))
      )

    st.markdown(f"**{text.GAP_EVIDENCE_HEADING}**")
    for passage in card.evidence:
      st.markdown(
        text.GAP_EVIDENCE_SOURCE.format(
          citation_key=passage.citation_key, section=passage.section
        )
      )
      if passage.detail:
        st.caption(passage.detail)
      _render_evidence_body(passage.passage, passage.context)

    accept_col, reject_col, clear_col = st.columns(3)
    if accept_col.button(
      text.GAP_ACCEPT_BUTTON, key=f"accept-{card.gap_id}", width="stretch"
    ):
      record_judgment(corpus_root, card.gap_id, "accepted")
      st.rerun()
    if reject_col.button(
      text.GAP_REJECT_BUTTON, key=f"reject-{card.gap_id}", width="stretch"
    ):
      record_judgment(corpus_root, card.gap_id, "rejected")
      st.rerun()
    if clear_col.button(
      text.GAP_CLEAR_BUTTON, key=f"clear-{card.gap_id}", width="stretch"
    ):
      clear_judgment(corpus_root, card.gap_id)
      st.rerun()

    if st.button(
      text.GAP_DISCUSS_BUTTON, key=f"discuss-{card.gap_id}", width="stretch"
    ):
      st.session_state[DISCUSS_GAP_KEY] = card.gap_id
      st.session_state[PENDING_NAV_KEY] = text.PAGE_ANALYTICS
      st.rerun()


def _render_limitation_group(group: LimitationGroupView) -> None:
  """Render one limitation group with its counts and source passages."""
  status = (
    text.LIMITATION_STATUS_ADDRESSED
    if group.addressed
    else text.LIMITATION_STATUS_UNANSWERED
  )
  with st.container(border=True):
    st.markdown(f"**{group.label}** \u2014 {status}")
    st.caption(
      text.LIMITATION_SOURCE_LABEL.format(
        count=group.source_paper_count,
        papers=", ".join(group.source_citation_keys),
      )
    )
    st.caption(
      text.LIMITATION_FOLLOW_UP_LABEL.format(
        follow_up_count=group.follow_up_count,
        later_count=group.later_paper_count,
      )
    )

    st.markdown(f"**{text.LIMITATION_STATEMENTS_HEADING}**")
    for statement in group.statements:
      st.markdown(
        text.LIMITATION_SOURCE_PASSAGE.format(
          citation_key=statement.citation_key, section=statement.section
        )
      )
      st.markdown(f"> {statement.passage}")

    if group.follow_ups:
      st.markdown(f"**{text.LIMITATION_FOLLOW_UPS_HEADING}**")
      for follow_up in group.follow_ups:
        st.markdown(
          text.LIMITATION_FOLLOW_UP_PASSAGE.format(
            citation_key=follow_up.citation_key, section=follow_up.section
          )
        )
        if follow_up.reason:
          st.caption(follow_up.reason)
        st.markdown(f"> {follow_up.passage}")


def render_limitations(data: LimitationsData) -> None:
  """Render the Unanswered Limitations page from already-computed data."""
  st.header(text.LIMITATIONS_HEADING)
  st.info(text.LIMITATIONS_INTRO)
  if not data.groups:
    st.success(text.NO_LIMITATION_GROUPS)
    return
  st.caption(
    text.LIMITATIONS_SUMMARY.format(
      total=data.group_count,
      extracted=data.extracted_paper_count,
      unanswered=data.unanswered_count,
      addressed=data.addressed_count,
    )
  )
  for group in data.groups:
    _render_limitation_group(group)


def _render_retrieval_candidate(candidate: RetrievalCandidateView) -> None:
  """Render one out-of-corpus candidate with its citation overlap."""
  with st.container(border=True):
    st.markdown(f"**{candidate.title or candidate.openalex_id}**")
    if candidate.authors:
      st.caption(
        text.RETRIEVAL_AUTHORS_LABEL.format(authors=", ".join(candidate.authors))
      )
    st.caption(
      text.RETRIEVAL_META_LABEL.format(
        venue=candidate.venue,
        year=(
          text.RETRIEVAL_YEAR_PART.format(year=candidate.year)
          if candidate.year is not None
          else ""
        ),
        cited_by=(
          text.RETRIEVAL_CITED_BY_PART.format(count=candidate.cited_by_count)
          if candidate.cited_by_count
          else ""
        ),
      )
    )
    st.caption(
      text.RETRIEVAL_OVERLAP_LABEL.format(
        overlap=candidate.citation_overlap,
        papers=", ".join(candidate.citing_citation_keys),
      )
    )
    if candidate.doi:
      st.caption(text.RETRIEVAL_DOI_LABEL.format(doi=candidate.doi))


def render_retrieval_gaps(data: RetrievalGapsData) -> None:
  """Render the Retrieval Gaps page, kept clearly separate from gap cards."""
  st.header(text.RETRIEVAL_HEADING)
  st.warning(text.RETRIEVAL_SEPARATION_NOTICE)
  if not data.candidates:
    st.success(text.NO_RETRIEVAL_CANDIDATES)
    return
  st.caption(
    text.RETRIEVAL_SUMMARY.format(
      total=data.candidate_count,
      coupled=data.coupled_paper_count,
      corpus=data.corpus_paper_count,
      min_overlap=data.min_overlap,
    )
  )
  st.caption(text.RETRIEVAL_SOURCE_LABEL.format(source=data.source))
  for candidate in data.candidates:
    _render_retrieval_candidate(candidate)


def render_paper_explainer(
  menu: PaperMenuData, view: PaperExplainerView | None
) -> None:
  """Render the Paper Explainer page: pick a Paper, show both registers."""
  st.header(text.EXPLAINER_HEADING)
  st.info(text.EXPLAINER_INTRO)
  st.caption(
    text.EXPLAINER_SUMMARY.format(
      explained=menu.explained_count, total=menu.paper_count
    )
  )
  if view is None:
    st.warning(text.NO_EXPLANATION_FOR_PAPER)
    return

  st.subheader(view.title or view.citation_key)
  st.caption(
    text.EXPLAINER_METADATA_VENUE.format(
      journal=view.journal,
      year=(
        text.EXPLAINER_METADATA_YEAR_PART.format(year=view.year)
        if view.year is not None
        else ""
      ),
    )
  )
  if view.doi:
    st.markdown(
      f"[{text.EXPLAINER_DOI_LABEL.format(doi=view.doi)}](https://doi.org/{view.doi})"
    )

  st.markdown(f"**{text.EXPLAINER_DOMAIN_HEADING}**")
  st.write(view.domain_explanation)
  st.markdown(f"**{text.EXPLAINER_LAY_HEADING}**")
  st.write(view.lay_explanation)


def render_gap_cards(data: GapCardsData, corpus_root: Path) -> None:
  """Render the Gap Cards page: one card per Candidate Gap, with persistence."""
  st.header(text.GAP_CARDS_HEADING)
  st.info(text.GAP_CARDS_INTRO)
  if not data.cards:
    st.success(text.NO_GAPS_DETECTED)
    return
  st.caption(
    text.GAP_JUDGMENT_SUMMARY.format(
      total=data.gap_count,
      accepted=data.accepted_count,
      rejected=data.rejected_count,
      undecided=data.undecided_count,
    )
  )
  for card in data.cards:
    _render_gap_card(card, corpus_root)


def _render_coverage_page(chosen: CorpusChoice) -> None:
  """Render the Coverage & Trends page for the chosen Corpus."""
  st.header(text.COVERAGE_HEADING)
  if not has_candidate_gaps(chosen.root):
    st.warning(text.NO_CANDIDATE_GAPS_ARTIFACT_COVERAGE)
    return
  if not has_normalized_facts(chosen.root):
    st.warning(text.NO_NORMALIZED_FACTS_ARTIFACT)
    return
  matrices = build_coverage_matrices(load_candidate_gaps(chosen.root))
  trends = build_trends(load_normalized_facts(chosen.root), load_manifest(chosen.root))
  render_coverage(matrices, trends)


def _render_meta_page(chosen: CorpusChoice) -> None:
  """Render the Field Meta-Analysis page for the chosen Corpus."""
  st.header(text.META_HEADING)
  if not has_manifest(chosen.root):
    st.warning(text.META_NO_MANIFEST)
    return
  st.info(text.META_INTRO)
  manifest = load_manifest(chosen.root)

  render_network_graph(build_citation_network(manifest))

  if has_normalized_facts(chosen.root):
    normalized = load_normalized_facts(chosen.root)
    render_network_graph(build_cooccurrence(normalized, manifest, axis="topic"))
    render_network_graph(build_cooccurrence(normalized, manifest, axis="method"))
    st.subheader(text.TRENDS_HEADING)
    _render_trends(build_trends(normalized, manifest))
  else:
    st.info(text.META_NO_NORMALIZED)

  render_network_graph(build_author_collaboration(manifest))

  if has_candidate_gaps(chosen.root):
    render_limitation_flow(
      build_limitation_flow(build_limitations(load_candidate_gaps(chosen.root)))
    )


def render_network_graph(graph: NetworkGraph) -> None:
  """Render one meta-analysis network graph with its question, caveat, and table."""
  st.subheader(graph.title)
  st.caption(f"**{text.META_QUESTION_LABEL}:** {graph.question}")
  st.caption(f"**{text.META_GAP_LENS_LABEL}:** {graph.gap_lens}")
  if graph.is_empty:
    st.info(text.META_EMPTY_GRAPH)
    for note in graph.notes:
      st.caption(note)
    return
  st.plotly_chart(
    build_network_figure(graph),
    use_container_width=True,
    key=f"meta-{graph.kind}-{graph.title}",
  )
  for note in graph.notes:
    st.caption(note)
  if graph.isolated_labels:
    st.caption(text.META_ISOLATED_LEGEND + ": " + ", ".join(graph.isolated_labels))
  if graph.missing_pairs:
    with st.expander(f"{text.META_MISSING_PAIRS_LABEL} ({len(graph.missing_pairs)})"):
      for pair in graph.missing_pairs:
        st.markdown(
          "- "
          + text.META_MISSING_PAIR_ROW.format(
            source=pair.source_label, target=pair.target_label
          )
        )
  elif graph.kind == "cooccurrence":
    st.caption(text.META_NO_MISSING_PAIRS)
  st.caption(graph.caveat)
  _render_graph_data_table(graph)


def _render_graph_data_table(graph: NetworkGraph) -> None:
  """Show the graph's edges as a table: the accessible, non-interactive fallback."""
  rows = [
    {"Source": edge.source, "Target": edge.target, "Papers": edge.weight}
    for edge in graph.edges
  ]
  with st.expander(text.META_DATA_TABLE_LABEL):
    if rows:
      st.dataframe(rows, use_container_width=True, hide_index=True)
    else:
      st.caption(text.META_EMPTY_GRAPH)


def render_limitation_flow(graph: SankeyGraph) -> None:
  """Render the limitation follow-up Sankey with its question and caveat."""
  st.subheader(graph.title)
  st.caption(f"**{text.META_QUESTION_LABEL}:** {graph.question}")
  st.caption(f"**{text.META_GAP_LENS_LABEL}:** {graph.gap_lens}")
  if graph.is_empty:
    st.info(text.META_LIMITATION_NO_DATA)
    return
  st.plotly_chart(
    build_sankey_figure(graph), use_container_width=True, key="meta-sankey"
  )
  st.caption(
    text.META_LIMITATION_SUMMARY.format(
      open=graph.unanswered_count,
      total=graph.unanswered_count + graph.addressed_count,
      addressed=graph.addressed_count,
    )
  )
  st.caption(graph.caveat)


def _render_gap_cards_page(chosen: CorpusChoice) -> None:
  """Render the Gap Cards page for the chosen Corpus."""
  if not has_candidate_gaps(chosen.root):
    st.header(text.GAP_CARDS_HEADING)
    st.warning(text.NO_CANDIDATE_GAPS_ARTIFACT)
    return
  data = build_gap_cards(
    load_candidate_gaps(chosen.root), load_judgments(chosen.root), chosen.root
  )
  render_gap_cards(data, chosen.root)


def _render_limitations_page(chosen: CorpusChoice) -> None:
  """Render the Unanswered Limitations page for the chosen Corpus."""
  if not has_candidate_gaps(chosen.root):
    st.header(text.LIMITATIONS_HEADING)
    st.warning(text.NO_CANDIDATE_GAPS_ARTIFACT_LIMITATIONS)
    return
  render_limitations(build_limitations(load_candidate_gaps(chosen.root)))


def _render_retrieval_page(chosen: CorpusChoice) -> None:
  """Render the Retrieval Gaps page for the chosen Corpus."""
  if not has_retrieval_gaps(chosen.root):
    st.header(text.RETRIEVAL_HEADING)
    st.warning(text.NO_RETRIEVAL_GAPS_ARTIFACT)
    return
  render_retrieval_gaps(build_retrieval_gaps(load_retrieval_gaps(chosen.root)))


def _render_overview_page(chosen: CorpusChoice) -> None:
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
    )
  )


# Each dashboard page maps to the function that renders it for a chosen Corpus,
# so `main` just dispatches instead of growing a return per page.
_PAGE_RENDERERS: dict[str, Callable[[CorpusChoice], None]] = {
  text.PAGE_COVERAGE: _render_coverage_page,
  text.PAGE_META: _render_meta_page,
  text.PAGE_GAP_CARDS: _render_gap_cards_page,
  text.PAGE_LIMITATIONS: _render_limitations_page,
  text.PAGE_RETRIEVAL: _render_retrieval_page,
  text.PAGE_EXPLAINER: lambda chosen: _render_explainer_page(chosen.root),
  text.PAGE_COMPARISON: lambda chosen: _render_comparison_page(chosen.root),
  text.PAGE_ANALYTICS: lambda chosen: _render_analytics_page(chosen.root),
  text.PAGE_VERIFY: lambda chosen: _render_verify_page(chosen.root),
  text.PAGE_ATTENTION: lambda chosen: _render_attention_page(chosen.root),
  text.PAGE_OVERVIEW: _render_overview_page,
}

# The dashboard caches Conversational Analytics answers alongside the pipeline's
# other LLM calls, keyed by their inputs (ADR 0001), so a repeat question is free.
LLM_CACHE_DIR = ".llm-cache"


def main() -> None:
  """Run the dashboard: pick a corpus and page, then render it."""
  st.set_page_config(page_title=text.APP_TITLE, layout="wide")
  st.title(text.APP_TITLE)

  choices = discover_corpora(_corpora_root())
  if not choices:
    st.warning(text.NO_CORPORA)
    return

  chosen = _select_corpus(choices)
  _render_incompleteness_banner(chosen.root)
  _apply_pending_nav()
  page = _select_page()
  _PAGE_RENDERERS.get(page, _render_overview_page)(chosen)


def _render_incompleteness_banner(corpus_root: Path) -> None:
  """Show a loud banner on every page when the Corpus is incomplete (issue #46)."""
  banner = build_attention(corpus_root).banner
  if banner.total_papers == 0 or banner.is_complete:
    return
  st.warning(
    text.incompleteness_banner(
      banner.missing_papers,
      banner.papers_with_dropped_facts,
      banner.total_papers,
      banner.reached_artifact,
    )
  )


def _render_attention_page(corpus_root: Path) -> None:
  """Assemble and render the needs-manual-attention queue for a Corpus."""
  render_attention(build_attention(corpus_root), corpus_root)


def _render_explainer_page(corpus_root: Path) -> None:
  """Assemble the Paper Explainer page's data and render it."""
  manifest = load_manifest(corpus_root)
  menu = build_paper_menu(manifest, explained_citation_keys(corpus_root))
  if not menu.entries:
    st.header(text.EXPLAINER_HEADING)
    st.warning(text.EXPLAINER_NO_PAPERS)
    return

  chosen_paper = st.selectbox(
    text.EXPLAINER_PAPER_PICKER_LABEL,
    menu.entries,
    format_func=lambda entry: entry.label,
  )
  view = _load_explainer_view(corpus_root, manifest, chosen_paper)
  render_paper_explainer(menu, view)


def _render_comparison_page(corpus_root: Path) -> None:
  """Assemble the Paper Comparison page's data and render it."""
  st.header(text.COMPARISON_HEADING)
  st.info(text.COMPARISON_INTRO)
  if not has_extractions(corpus_root) or not has_normalized_facts(corpus_root):
    st.warning(text.COMPARISON_NO_ARTIFACTS)
    return

  manifest = load_manifest(corpus_root)
  entries = manifest.papers
  selection_keys = st.multiselect(
    text.COMPARISON_PICKER_LABEL,
    [paper.citation_key for paper in entries],
    format_func=lambda key: _paper_label(manifest, key),
    max_selections=MAX_SELECTION,
  )
  if selection_error(selection_keys, manifest) is not None:
    st.info(text.COMPARISON_TOO_FEW)
    return

  result = build_comparison(
    selection_keys,
    load_extractions(corpus_root),
    load_normalized_facts(corpus_root),
    manifest,
  )
  render_comparison(result)


def _paper_label(manifest: ManifestArtifact, citation_key: str) -> str:
  """Return a human label for a Paper in the comparison picker."""
  paper = next(p for p in manifest.papers if p.citation_key == citation_key)
  name = paper.title or paper.citation_key
  return f"{name} ({paper.year})" if paper.year is not None else name


def _render_axis_comparison(axis: AxisComparison) -> None:
  """Render one axis's agreements and differences from finished data."""
  if not (axis.agreements or axis.differences):
    return
  st.markdown(f"**{axis.axis_label}**")
  if axis.agreements:
    st.caption(
      text.COMPARISON_AGREEMENTS_LABEL
      + " "
      + ", ".join(item.label for item in axis.agreements)
    )
  if axis.differences:
    st.caption(text.COMPARISON_DIFFERENCES_LABEL)
    for item in axis.differences:
      st.caption(
        "\u00b7 "
        + text.COMPARISON_DIFFERENCE_ITEM.format(
          label=item.label, keys=", ".join(item.covering_keys)
        )
      )


def _render_field_comparison(field: FieldComparison) -> None:
  """Render one field's statements, one column per compared unit."""
  st.markdown(f"**{field.label}**")
  columns = st.columns(len(field.units))
  for column, unit in zip(columns, field.units, strict=True):
    column.caption(unit.label)
    if not unit.statements:
      column.markdown(text.COMPARISON_NO_STATEMENTS)
      continue
    for statement in unit.statements:
      column.markdown(f"- {statement}")


def render_comparison(result: ComparisonResult) -> None:
  """Render the Paper Comparison page from already-computed data."""
  if result.is_chunked:
    st.warning(
      text.COMPARISON_CHUNK_NOTICE.format(
        count=len(result.citation_keys), sets=len(result.chunks)
      )
    )

  st.subheader(text.COMPARISON_FIELDS_HEADING)
  for field in result.fields:
    _render_field_comparison(field)

  st.subheader(text.COMPARISON_AXES_HEADING)
  if not any(axis.agreements or axis.differences for axis in result.axes):
    st.caption(text.COMPARISON_NO_AXIS_SIGNAL)
  for axis in result.axes:
    _render_axis_comparison(axis)

  st.subheader(text.COMPARISON_BLIND_SPOTS_HEADING)
  st.caption(
    text.COMPARISON_BLIND_SPOTS_DENOMINATOR.format(total=result.corpus_paper_count)
  )
  blind = [axis for axis in result.axes if axis.blind_spots]
  if not blind:
    st.success(text.COMPARISON_NO_BLIND_SPOTS)
    return
  for axis in blind:
    st.markdown(
      text.COMPARISON_BLIND_SPOT_AXIS.format(
        axis=axis.axis_label,
        labels=", ".join(item.label for item in axis.blind_spots),
      )
    )


def _load_explainer_view(
  corpus_root: Path, manifest: ManifestArtifact, chosen: PaperMenuEntry
) -> PaperExplainerView | None:
  """Load the chosen Paper's explanation view, or None when it has none yet."""
  if not has_paper_explanation(corpus_root, chosen.citation_key):
    return None
  paper = next(p for p in manifest.papers if p.citation_key == chosen.citation_key)
  explanation = load_paper_explanation(corpus_root, chosen.citation_key)
  return build_paper_explainer(paper, explanation)


def _render_chat_turn(turn: ChatTurn) -> None:
  """Render one persisted chat turn with the citations behind an answer."""
  with st.chat_message(turn.role):
    st.markdown(turn.content)
    if turn.role == "assistant":
      if turn.citations:
        st.caption(
          text.ANALYTICS_CITATIONS_LABEL.format(papers=", ".join(turn.citations))
        )
      else:
        st.caption(text.ANALYTICS_NO_CITATIONS)


def _render_grounding_flags(dropped: list[str]) -> None:
  """Warn when generation cited work that does not resolve to a Corpus Paper."""
  if dropped:
    st.warning(
      text.ANALYTICS_DROPPED_LABEL.format(count=len(dropped), papers=", ".join(dropped))
    )


def _render_analytics_page(corpus_root: Path) -> None:
  """Render the Conversational Analytics page: grounded chat and narrative summary."""
  st.header(text.ANALYTICS_HEADING)
  st.info(text.ANALYTICS_INTRO)
  if not has_manifest(corpus_root):
    st.warning(text.ANALYTICS_NO_MANIFEST)
    return

  client = build_analytics_client(corpus_root / LLM_CACHE_DIR)
  if client is None:
    st.warning(text.ANALYTICS_NO_KEY)
    return

  manifest = load_manifest(corpus_root)
  has_gaps = has_candidate_gaps(corpus_root)
  context = _grounding_context(corpus_root, manifest, has_gaps)

  if _render_scoped_gap_chat(corpus_root, context, client):
    return
  _render_narrative_summary(context, client, has_gaps)
  _render_chat(corpus_root, context, client)


def _scoped_history_key(gap_id: str) -> str:
  """Session-state key for one gap's ephemeral scoped-chat transcript."""
  return f"scoped_chat_{gap_id}"


def _scoped_gap_card(corpus_root: Path, gap_id: str) -> GapCard | None:
  """Find the card a scoped chat is focused on, or None when it is gone."""
  if not has_candidate_gaps(corpus_root):
    return None
  data = build_gap_cards(
    load_candidate_gaps(corpus_root), load_judgments(corpus_root), corpus_root
  )
  return next((card for card in data.cards if card.gap_id == gap_id), None)


def _render_scoped_gap_chat(
  corpus_root: Path, context: GroundingContext, client: LlmClient
) -> bool:
  """
  Render the one-click chat scoped to a single Candidate Gap (issue #48).

  Returns True when a scoped chat was shown, so the page skips the whole-Corpus
  chat and narrative summary. The scoped conversation is ephemeral in session
  state; per-gap persistent conversations are a separate v2 feature (issue #44).
  """
  gap_id = st.session_state.get(DISCUSS_GAP_KEY)
  if not gap_id:
    return False
  card = _scoped_gap_card(corpus_root, gap_id)
  if card is None:
    st.session_state.pop(DISCUSS_GAP_KEY, None)
    st.info(text.ANALYTICS_SCOPED_GAP_MISSING)
    return False

  scoped = scope_context_to_gap(card, context)
  st.subheader(text.ANALYTICS_SCOPED_HEADING.format(title=card.title))
  st.info(text.ANALYTICS_SCOPED_INTRO)
  if st.button(text.ANALYTICS_SCOPED_EXIT_BUTTON):
    st.session_state.pop(DISCUSS_GAP_KEY, None)
    st.session_state.pop(_scoped_history_key(gap_id), None)
    st.rerun()

  turns: list[ChatTurn] = st.session_state.get(_scoped_history_key(gap_id), [])
  for turn in turns:
    _render_chat_turn(turn)

  question = st.chat_input(
    text.ANALYTICS_SCOPED_CHAT_INPUT_LABEL, key=f"scoped-input-{gap_id}"
  )
  if not question:
    return True
  transcript = [f"{turn.role}: {turn.content}" for turn in turns]
  with st.spinner(text.ANALYTICS_THINKING):
    answer = answer_question(question, scoped, client, history=transcript)
  st.session_state[_scoped_history_key(gap_id)] = [
    *turns,
    _ephemeral_turn("user", question),
    _ephemeral_turn("assistant", answer.answer, answer.citations),
  ]
  _render_grounding_flags(answer.dropped_citations)
  st.rerun()


def _ephemeral_turn(
  role: Role, content: str, citations: list[str] | None = None
) -> ChatTurn:
  """Build one in-memory scoped-chat turn (not persisted to disk; see issue #44)."""
  return ChatTurn(
    role=role,
    content=content,
    citations=list(citations or []),
    at=datetime.now(timezone.utc),
  )


def _grounding_context(
  corpus_root: Path, manifest: ManifestArtifact, has_gaps: bool
) -> GroundingContext:
  """Map this Corpus's artifact read models onto the analytics grounding context."""
  papers = [
    CorpusPaper(citation_key=paper.citation_key, title=paper.title, year=paper.year)
    for paper in manifest.papers
  ]
  gap_facts: list[GapFact] = []
  if has_gaps:
    for gap in load_candidate_gaps(corpus_root).gaps:
      gap_facts.append(
        GapFact(
          gap_type=gap.gap_type,
          title=gap.title,
          explanation=gap.explanation,
          confidence=gap.confidence,
          source_citation_keys=gap.source_citation_keys,
        )
      )
  return GroundingContext(papers=papers, gaps=gap_facts)


def _render_narrative_summary(
  context: GroundingContext,
  client: LlmClient,
  has_gaps: bool,
) -> None:
  """Render the narrative-summary section, generated on demand and cached."""
  st.subheader(text.ANALYTICS_SUMMARY_HEADING)
  st.caption(text.ANALYTICS_SUMMARY_INTRO)
  if not has_gaps:
    st.info(text.ANALYTICS_SUMMARY_NEEDS_GAPS)
    return
  if st.button(text.ANALYTICS_GENERATE_SUMMARY_BUTTON):
    with st.spinner(text.ANALYTICS_THINKING):
      narrative = summarize_gaps(context, client)
    _render_narrative(narrative)


def _render_narrative(narrative: GroundedNarrative) -> None:
  """Render a generated narrative summary with its grounded citations."""
  for paragraph in narrative.paragraphs:
    st.write(paragraph)
  if narrative.citations:
    st.caption(
      text.ANALYTICS_CITATIONS_LABEL.format(papers=", ".join(narrative.citations))
    )
  else:
    st.caption(text.ANALYTICS_NO_CITATIONS)
  _render_grounding_flags(narrative.dropped_citations)


def _render_chat(
  corpus_root: Path, context: GroundingContext, client: LlmClient
) -> None:
  """Render the grounded chat: prior turns, a fresh answer, and its persistence."""
  st.subheader(text.ANALYTICS_CHAT_HEADING)
  history = load_chat_history(corpus_root)
  if history.turns and st.button(text.ANALYTICS_CLEAR_CHAT_BUTTON):
    clear_chat_history(corpus_root)
    st.rerun()

  for turn in history.turns:
    _render_chat_turn(turn)

  question = st.chat_input(text.ANALYTICS_CHAT_INPUT_LABEL)
  if not question:
    return
  transcript = [f"{turn.role}: {turn.content}" for turn in history.turns]
  append_turn(corpus_root, "user", question)
  with st.spinner(text.ANALYTICS_THINKING):
    answer = answer_question(question, context, client, history=transcript)
  append_turn(corpus_root, "assistant", answer.answer, answer.citations)
  _render_answer_flags(answer)
  st.rerun()


def _render_answer_flags(answer: GroundedChatAnswer) -> None:
  """Surface any dropped, ungrounded citations before the chat reruns."""
  _render_grounding_flags(answer.dropped_citations)


def _verdict_label(verdict: str | None) -> str:
  """Return the human label for a fact's current review verdict."""
  return {
    "approved": text.VERIFY_VERDICT_APPROVED,
    "edited": text.VERIFY_VERDICT_EDITED,
    "flagged": text.VERIFY_VERDICT_FLAGGED,
    "removed": text.VERIFY_VERDICT_REMOVED,
  }.get(verdict or "", text.VERIFY_VERDICT_UNREVIEWED)


def _render_evidence_body(passage: str, context: ExpandedEvidence | None) -> None:
  """
  Show the verbatim anchor inside its paragraph, highlighted, with pointers.

  The anchor stays ground truth (CODING_STANDARDS > Research integrity); the code
  only widens it to the containing paragraph and highlights the anchor, so the
  reader gets context the dashboard did not invent. Falls back to the bare quote
  when no parsed text grounds the passage.
  """
  if context is None or not context.grounded:
    st.markdown(f"> {passage}")
    return

  prefix = "\u2026 " if context.truncated_before else ""
  suffix = " \u2026" if context.truncated_after else ""
  body = (
    f"{prefix}{context.before}"
    f":orange-background[{context.anchor}]"
    f"{context.after}{suffix}"
  )
  st.markdown(f"> {body}")
  if context.pointers:
    st.caption(
      text.EVIDENCE_POINTERS_LABEL.format(
        pointers=", ".join(pointer.label for pointer in context.pointers)
      )
    )
  if context.full_section_adds_more:
    with st.expander(text.EVIDENCE_SHOW_FULL_SECTION):
      st.markdown(f"> {context.full_section}")


def _render_pdf_link(view: PaperReviewView) -> None:
  """Render a link back to the Paper's PDF when its path is known."""
  if view.pdf_path is None:
    return
  # Manifest PDF paths may be relative to the corpus; resolve so the link is a
  # valid file URI, and fall back to showing the path when it cannot be a URI.
  try:
    uri = view.pdf_path.resolve().as_uri()
  except ValueError:
    st.caption(f"{text.VERIFY_PDF_LINK}: {view.pdf_path}")
    return
  st.markdown(f"[{text.VERIFY_PDF_LINK}]({uri})")


def _render_extracted_fact(
  fact: ReviewFactView, corpus_root: Path, citation_key: str, sections: list[str]
) -> None:
  """Render one extracted fact with its Evidence and the five review verbs."""
  key = f"{citation_key}-{fact.field}-{fact.fact_index}"
  st.markdown(f"- {fact.effective_statement}")
  st.caption(text.VERIFY_SECTION_LABEL.format(section=fact.effective_section))
  _render_evidence_body(fact.effective_passage, fact.context)
  st.caption(_verdict_label(fact.verdict))

  approve_col, flag_col, remove_col, clear_col = st.columns(4)
  if approve_col.button(text.VERIFY_APPROVE_BUTTON, key=f"approve-{key}"):
    record_fact_verdict(
      corpus_root, citation_key, fact.field, fact.fact_index, "approved"
    )
    st.rerun()
  if flag_col.button(text.VERIFY_FLAG_BUTTON, key=f"flag-{key}"):
    record_fact_verdict(
      corpus_root, citation_key, fact.field, fact.fact_index, "flagged"
    )
    st.rerun()
  if remove_col.button(text.VERIFY_REMOVE_BUTTON, key=f"remove-{key}"):
    record_fact_verdict(
      corpus_root, citation_key, fact.field, fact.fact_index, "removed"
    )
    st.rerun()
  if clear_col.button(text.VERIFY_CLEAR_BUTTON, key=f"clear-{key}"):
    clear_fact_verdict(corpus_root, citation_key, fact.field, fact.fact_index)
    st.rerun()

  _render_edit_fact(fact, corpus_root, citation_key, sections, key)


def _render_edit_fact(
  fact: ReviewFactView,
  corpus_root: Path,
  citation_key: str,
  sections: list[str],
  key: str,
) -> None:
  """Render the edit form for one extracted fact, refusing ungrounded Evidence."""
  with st.expander(text.VERIFY_EDIT_BUTTON):
    statement = st.text_area(
      text.VERIFY_EDIT_STATEMENT_LABEL,
      value=fact.effective_statement,
      key=f"edit-statement-{key}",
    )
    passage = st.text_area(
      text.VERIFY_EDIT_PASSAGE_LABEL,
      value=fact.effective_passage,
      key=f"edit-passage-{key}",
    )
    section = _section_picker(
      text.VERIFY_EDIT_SECTION_LABEL, sections, fact.effective_section, f"edit-{key}"
    )
    if st.button(text.VERIFY_EDIT_BUTTON, key=f"save-edit-{key}"):
      try:
        record_fact_verdict(
          corpus_root,
          citation_key,
          fact.field,
          fact.fact_index,
          "edited",
          edited_statement=statement,
          edited_passage=passage,
          edited_section=section,
        )
      except UngroundedEvidenceError:
        st.error(text.VERIFY_UNGROUNDED_ERROR)
        return
      st.rerun()


def _render_added_fact(
  fact: ReviewFactView, corpus_root: Path, citation_key: str
) -> None:
  """Render one reviewer-added fact, with a control to delete it again."""
  st.markdown(f"- {fact.effective_statement} *({text.VERIFY_ADDED_BADGE})*")
  st.caption(text.VERIFY_SECTION_LABEL.format(section=fact.effective_section))
  st.markdown(f"> {fact.effective_passage}")
  if st.button(text.VERIFY_DELETE_ADDED_BUTTON, key=f"delete-{fact.fact_id}"):
    remove_added_fact(corpus_root, citation_key, fact.fact_id)
    st.rerun()


def _section_picker(label: str, sections: list[str], current: str, key: str) -> str:
  """Render a section picker seeded with the current section, falling back to text."""
  options = sections or [current]
  index = options.index(current) if current in options else 0
  return st.selectbox(label, options, index=index, key=key)


def _render_add_fact(
  field: ReviewFieldView, corpus_root: Path, citation_key: str, sections: list[str]
) -> None:
  """Render the add-a-missing-fact form for one field, refusing ungrounded Evidence."""
  with st.expander(text.VERIFY_ADD_FACT_HEADING):
    key = f"add-{citation_key}-{field.field}"
    statement = st.text_area(text.VERIFY_ADD_STATEMENT_LABEL, key=f"{key}-statement")
    passage = st.text_area(text.VERIFY_ADD_PASSAGE_LABEL, key=f"{key}-passage")
    section = _section_picker(
      text.VERIFY_ADD_SECTION_LABEL,
      sections,
      sections[0] if sections else "other",
      f"{key}-section",
    )
    if st.button(text.VERIFY_ADD_BUTTON, key=f"{key}-button"):
      try:
        add_fact(corpus_root, citation_key, field.field, statement, passage, section)
      except UngroundedEvidenceError:
        st.error(text.VERIFY_UNGROUNDED_ERROR)
        return
      except UnknownFieldError:
        return
      st.rerun()


def _render_verify_field(
  field: ReviewFieldView, corpus_root: Path, citation_key: str, sections: list[str]
) -> None:
  """Render one Extraction field: its facts, their verdicts, and an add-fact form."""
  st.subheader(field.label)
  if not field.facts:
    st.caption(text.VERIFY_NO_FACTS)
  for fact in field.facts:
    with st.container(border=True):
      if fact.is_added:
        _render_added_fact(fact, corpus_root, citation_key)
      else:
        _render_extracted_fact(fact, corpus_root, citation_key, sections)
  _render_add_fact(field, corpus_root, citation_key, sections)


def render_verify(
  view: PaperReviewView, corpus_root: Path, sections: list[str]
) -> None:
  """Render the Verify Extractions page for one Paper from already-assembled data."""
  st.header(text.VERIFY_HEADING)
  st.info(text.VERIFY_INTRO)
  st.subheader(view.title or view.citation_key)
  _render_pdf_link(view)
  st.caption(
    text.VERIFY_SUMMARY.format(
      reviewed=view.reviewed_count,
      extracted=view.extracted_count,
      added=view.added_count,
    )
  )
  for field in view.fields:
    _render_verify_field(field, corpus_root, view.citation_key, sections)


def _render_verify_page(corpus_root: Path) -> None:
  """Assemble the Verify Extractions page's data for a chosen Paper and render it."""
  if not has_extractions(corpus_root):
    st.header(text.VERIFY_HEADING)
    st.warning(text.VERIFY_NO_EXTRACTIONS)
    return

  extractions = load_extractions(corpus_root)
  manifest = load_manifest(corpus_root)
  by_key = {paper.citation_key: paper for paper in manifest.papers}
  choices = [
    extraction
    for extraction in extractions.extractions
    if extraction.citation_key in by_key
  ]
  if not choices:
    st.header(text.VERIFY_HEADING)
    st.warning(text.VERIFY_NO_EXTRACTIONS)
    return

  chosen = st.selectbox(
    text.VERIFY_PAPER_PICKER_LABEL,
    choices,
    format_func=lambda extraction: _paper_label(manifest, extraction.citation_key),
  )
  citation_key = chosen.citation_key
  if not has_parsed_paper(corpus_root, citation_key):
    st.header(text.VERIFY_HEADING)
    st.warning(text.VERIFY_NO_PARSED_TEXT)
    return

  review = load_extraction_review(corpus_root).review_for(citation_key)
  view = build_extraction_review(chosen, review, by_key[citation_key], corpus_root)
  sections = read_parsed_paper(corpus_root, citation_key).section_labels
  render_verify(view, corpus_root, sections)


if __name__ == "__main__":
  main()
