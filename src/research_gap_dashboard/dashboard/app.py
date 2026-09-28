"""
The Streamlit app shell: corpus selection, navigation, and the Corpus Overview.

This layer is deliberately thin. It reads on-disk artifacts through
`dashboard.artifacts`, asks `dashboard.overview` for finished data, and renders
it; it holds no analysis logic and imports no pipeline stage (ADR 0002). Run it
with `streamlit run` (see the justfile). Point it at the folder that holds your
corpus directories with the RESEARCH_GAP_CORPORA_DIR environment variable.
"""

import os
from pathlib import Path

import streamlit as st

from research_gap_dashboard.dashboard import text
from research_gap_dashboard.dashboard.artifacts import (
  CorpusChoice,
  discover_corpora,
  has_candidate_gaps,
  has_normalized_facts,
  load_candidate_gaps,
  load_manifest,
  load_normalized_facts,
)
from research_gap_dashboard.dashboard.coverage import (
  CoverageMatrixView,
  TrendsData,
  build_coverage_matrices,
  build_heatmap_figure,
  build_trends,
  build_trends_figure,
)
from research_gap_dashboard.dashboard.gaps import GapCard, GapCardsData, build_gap_cards
from research_gap_dashboard.dashboard.judgments import (
  clear_judgment,
  load_judgments,
  record_judgment,
)
from research_gap_dashboard.dashboard.overview import OverviewData, build_overview

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


def _select_page() -> str:
  """Render the sidebar page picker and return the chosen page."""
  return st.sidebar.radio(
    text.NAV_LABEL,
    [text.PAGE_OVERVIEW, text.PAGE_COVERAGE, text.PAGE_GAP_CARDS],
    index=0,
    label_visibility="collapsed",
  )


def render_overview(overview: OverviewData) -> None:
  """Render the Corpus Overview page from already-computed data."""
  st.header(text.OVERVIEW_HEADING)
  st.info(overview.scope_statement)

  paper_col, venue_col, year_col = st.columns(3)
  paper_col.metric(text.METRIC_PAPER_COUNT, overview.paper_count)
  venue_col.metric(text.METRIC_VENUE_COUNT, overview.venue_count)
  year_col.metric(text.METRIC_YEAR_SPAN, overview.year_span)

  st.subheader(text.PAPERS_PER_YEAR_HEADING)
  st.bar_chart(
    {
      text.COLUMN_PAPERS: {
        bucket.label: bucket.count for bucket in overview.papers_per_year
      }
    }
  )

  st.subheader(text.VENUES_HEADING)
  st.table(
    [
      {text.COLUMN_VENUE: venue.venue, text.COLUMN_PAPERS: venue.count}
      for venue in overview.venues
    ]
  )

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


def render_coverage(matrices: list[CoverageMatrixView], trends: TrendsData) -> None:
  """Render the Coverage Matrix heatmap and the Trends chart from finished data."""
  st.header(text.COVERAGE_HEADING)
  st.info(text.COVERAGE_INTRO)

  st.subheader(text.HEATMAP_HEADING)
  if not matrices:
    st.warning(text.HEATMAP_NO_MATRICES)
  else:
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
      st.markdown(f"> {passage.passage}")

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


def main() -> None:
  """Run the dashboard: pick a corpus and page, then render it."""
  st.set_page_config(page_title=text.APP_TITLE, layout="wide")
  st.title(text.APP_TITLE)

  choices = discover_corpora(_corpora_root())
  if not choices:
    st.warning(text.NO_CORPORA)
    return

  chosen = _select_corpus(choices)
  page = _select_page()

  if page == text.PAGE_COVERAGE:
    st.header(text.COVERAGE_HEADING)
    if not has_candidate_gaps(chosen.root):
      st.warning(text.NO_CANDIDATE_GAPS_ARTIFACT_COVERAGE)
      return
    if not has_normalized_facts(chosen.root):
      st.warning(text.NO_NORMALIZED_FACTS_ARTIFACT)
      return
    matrices = build_coverage_matrices(load_candidate_gaps(chosen.root))
    trends = build_trends(
      load_normalized_facts(chosen.root), load_manifest(chosen.root)
    )
    render_coverage(matrices, trends)
    return

  if page == text.PAGE_GAP_CARDS:
    if not has_candidate_gaps(chosen.root):
      st.header(text.GAP_CARDS_HEADING)
      st.warning(text.NO_CANDIDATE_GAPS_ARTIFACT)
      return
    data = build_gap_cards(
      load_candidate_gaps(chosen.root), load_judgments(chosen.root)
    )
    render_gap_cards(data, chosen.root)
    return

  overview = build_overview(load_manifest(chosen.root))
  render_overview(overview)


if __name__ == "__main__":
  main()
