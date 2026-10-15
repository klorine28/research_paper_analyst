"""The Gap Cards page: one card per Candidate Gap, with Evidence and accept/reject."""

from pathlib import Path

import streamlit as st

from research_gap_dashboard.dashboard import text
from research_gap_dashboard.dashboard.artifacts import (
  CorpusChoice,
  has_candidate_gaps,
  load_candidate_gaps,
)
from research_gap_dashboard.dashboard.gaps import (
  CONFIDENCE_LEVELS,
  DEFAULT_CONFIDENCE_FILTER,
  GapCard,
  GapCardsData,
  build_gap_cards,
  filter_by_confidence,
)
from research_gap_dashboard.dashboard.judgments import (
  clear_judgment,
  load_judgments,
  record_judgment,
)
from research_gap_dashboard.dashboard.views.session_keys import (
  DISCUSS_GAP_KEY,
  GAP_CONFIDENCE_FILTER_KEY,
  GAP_CONFIDENCE_WIDGET_KEY,
  PENDING_NAV_KEY,
)
from research_gap_dashboard.dashboard.views.widgets import (
  render_evidence_body,
)


def render_page(chosen: CorpusChoice) -> None:
  """Render the Gap Cards page for the chosen Corpus."""
  if not has_candidate_gaps(chosen.root):
    st.header(text.GAP_CARDS_HEADING)
    st.warning(text.NO_CANDIDATE_GAPS_ARTIFACT)
    return
  data = build_gap_cards(
    load_candidate_gaps(chosen.root), load_judgments(chosen.root), chosen.root
  )
  render_gap_cards(data, chosen.root)


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
  filtered = filter_by_confidence(data, _confidence_filter())
  st.caption(
    text.GAP_FILTER_HIDDEN_SUMMARY.format(
      shown=filtered.shown_count,
      total=filtered.total_count,
      hidden=filtered.hidden_count,
    )
  )
  if not filtered.cards:
    st.info(text.GAP_FILTER_NONE_SHOWN)
  for card in filtered.cards:
    _render_gap_card(card, corpus_root)


def _remember_confidence_filter() -> None:
  """Copy the widget's choice to the key that outlives page navigation."""
  st.session_state[GAP_CONFIDENCE_FILTER_KEY] = list(
    st.session_state[GAP_CONFIDENCE_WIDGET_KEY]
  )


def _confidence_filter() -> list[str]:
  """Render the confidence filter, restored from the session, and return it."""
  remembered = st.session_state.setdefault(
    GAP_CONFIDENCE_FILTER_KEY, list(DEFAULT_CONFIDENCE_FILTER)
  )
  st.session_state[GAP_CONFIDENCE_WIDGET_KEY] = list(remembered)
  chosen = st.multiselect(
    text.GAP_FILTER_LABEL,
    options=list(CONFIDENCE_LEVELS),
    format_func=_confidence_option_label,
    key=GAP_CONFIDENCE_WIDGET_KEY,
    on_change=_remember_confidence_filter,
  )
  return list(chosen)


def _card_status_label(card: GapCard) -> str:
  """Return the human label for a card's current accept/reject state."""
  if card.status == "accepted":
    return text.GAP_STATUS_ACCEPTED
  if card.status == "rejected":
    return text.GAP_STATUS_REJECTED
  return text.GAP_STATUS_UNDECIDED


def _render_gap_card(card: GapCard, corpus_root: Path) -> None:
  """
  Render one Candidate Gap card in ADR 0008's three tiers, with its controls.

  Tier 1 is always visible; Tiers 2 and 3 are collapsed expanders. Every tier
  reads only the prebuilt card, so expanding one never calls an LLM.
  """
  with st.container(border=True):
    _render_headline(card)
    with st.expander(text.GAP_TIER_JUSTIFICATION_LABEL):
      _render_justification(card)
    with st.expander(text.GAP_TIER_DATA_LABEL):
      _render_data(card)
    _render_controls(card, corpus_root)


def _render_headline(card: GapCard) -> None:
  """Tier 1: statement, confidence, one-line why, and cell count."""
  st.markdown(f"**{card.gap_type_label}** \u2014 {card.title}")
  st.caption(
    text.GAP_CONFIDENCE_LABEL.format(confidence=card.confidence_label)
    + "  \u00b7  "
    + _card_status_label(card)
  )
  st.write(card.why)
  if card.cell_count is not None:
    st.caption(
      text.GAP_CELL_COUNT_LABEL.format(
        count=card.cell_count, total=card.corpus_paper_count
      )
    )


def _render_justification(card: GapCard) -> None:
  """Tier 2: the explanation and the verbatim Evidence behind the gap."""
  st.write(card.explanation)
  st.markdown(f"**{text.GAP_EVIDENCE_HEADING}**")
  for index, passage in enumerate(card.evidence):
    st.markdown(
      text.GAP_EVIDENCE_SOURCE.format(
        citation_key=passage.citation_key, section=passage.section
      )
    )
    if passage.detail:
      st.caption(passage.detail)
    render_evidence_body(
      passage.passage,
      passage.context,
      full_section_toggle_key=f"full-section-{card.gap_id}-{index}",
    )


_MAX_NEIGHBOURS = 10


def _render_data(card: GapCard) -> None:
  """Tier 3: the Papers per category, neighbouring cells, score and reasons."""
  data = card.data
  st.markdown(f"**{text.GAP_DATA_PAPERS_HEADING}**")
  for group in data.category_papers:
    st.markdown(
      text.GAP_DATA_CATEGORY_PAPERS.format(
        label=group.label,
        count=len(group.citation_keys),
        papers=", ".join(group.citation_keys) or text.GAP_DATA_NO_PAPERS,
      )
    )
  if card.cell_count is not None:
    st.markdown(f"**{text.GAP_DATA_NEIGHBOURS_HEADING}**")
    if not data.neighbours:
      st.caption(text.GAP_DATA_NO_NEIGHBOURS)
    shown = data.neighbours[:_MAX_NEIGHBOURS]
    if shown:
      st.markdown(
        "\n".join(
          text.GAP_DATA_NEIGHBOUR.format(
            row=cell.row_label, column=cell.column_label, count=cell.paper_count
          )
          for cell in shown
        )
      )
    hidden = len(data.neighbours) - len(shown)
    if hidden > 0:
      st.caption(text.GAP_DATA_NEIGHBOURS_MORE.format(count=hidden))
  st.markdown(f"**{text.GAP_DATA_SCORE_HEADING}**")
  if data.expected_count is not None:
    st.markdown(
      text.GAP_DATA_SCORE.format(
        expected=data.expected_count,
        total=card.corpus_paper_count,
        observed=data.observed_count,
      )
    )
  for reason in data.reasons:
    st.caption(reason)


def _render_controls(card: GapCard, corpus_root: Path) -> None:
  """Accept, reject, reset, and the scoped "Discuss" chat, on every card."""
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

  if st.button(text.GAP_DISCUSS_BUTTON, key=f"discuss-{card.gap_id}", width="stretch"):
    st.session_state[DISCUSS_GAP_KEY] = card.gap_id
    st.session_state[PENDING_NAV_KEY] = text.PAGE_ANALYTICS
    st.rerun()


def _confidence_option_label(level: str) -> str:
  """Show a confidence level the way the cards spell it."""
  return text.CONFIDENCE_LABELS.get(level, level)
