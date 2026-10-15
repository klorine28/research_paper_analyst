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
  STATUS_FILTERS,
  FilteredGapCards,
  GapCard,
  GapCardsData,
  StatusFilter,
  build_gap_cards,
  filter_gap_cards,
  next_undecided_gap_id,
)
from research_gap_dashboard.dashboard.judgments import (
  clear_judgment,
  load_judgments,
  record_judgment,
)
from research_gap_dashboard.dashboard.views.session_keys import (
  DISCUSS_GAP_KEY,
  GAP_FILTER_STATUS_KEY,
  GAP_FILTER_TYPE_KEY,
  GAP_FOCUS_KEY,
  PENDING_NAV_KEY,
)
from research_gap_dashboard.dashboard.views.widgets import (
  render_evidence_body,
)

_STATUS_FILTER_LABELS: dict[StatusFilter, str] = {
  "accepted": text.GAP_STATUS_ACCEPTED,
  "rejected": text.GAP_STATUS_REJECTED,
  "undecided": text.GAP_STATUS_UNDECIDED,
}


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
  shown = _render_filters(data)
  if not shown.cards:
    st.info(text.GAP_FILTER_NO_MATCH)
    return
  focus_id = _render_next_undecided(shown.cards)
  focused = [card for card in shown.cards if card.gap_id == focus_id]
  others = [card for card in shown.cards if card.gap_id != focus_id]
  if focused:
    st.subheader(text.GAP_FOCUS_HEADING)
    _render_gap_card(focused[0], corpus_root)
    if others:
      st.subheader(text.GAP_OTHERS_HEADING)
  for card in others:
    _render_gap_card(card, corpus_root)


def _render_filters(data: GapCardsData) -> FilteredGapCards:
  """Render the status and Gap Type filters and return the cards they keep."""
  gap_types = list(dict.fromkeys(card.gap_type for card in data.cards))
  status_col, type_col = st.columns(2)
  statuses = status_col.multiselect(
    text.GAP_FILTER_STATUS_LABEL,
    options=list(STATUS_FILTERS),
    default=list(STATUS_FILTERS),
    format_func=_STATUS_FILTER_LABELS.__getitem__,
    key=GAP_FILTER_STATUS_KEY,
  )
  selected_types = type_col.multiselect(
    text.GAP_FILTER_TYPE_LABEL,
    options=gap_types,
    default=gap_types,
    format_func=_gap_type_label,
    key=GAP_FILTER_TYPE_KEY,
  )
  shown = filter_gap_cards(data, statuses=statuses, gap_types=selected_types)
  st.caption(
    text.GAP_FILTER_SUMMARY.format(
      shown=len(shown.cards), total=data.gap_count, hidden=shown.hidden_count
    )
  )
  return shown


def _gap_type_label(gap_type: str) -> str:
  """Return the human label for a Gap Type, falling back to its raw name."""
  return text.GAP_TYPE_LABELS.get(gap_type, gap_type)


def _render_next_undecided(cards: list[GapCard]) -> str | None:
  """
  Render "Next undecided" and return the card currently in focus, if any.

  Pressing it advances the focus past the current card to the next undecided
  one among the cards the filters show; the focus is kept in session state so
  judging the focused card leaves it in place until the next press.
  """
  current = st.session_state.get(GAP_FOCUS_KEY)
  if st.button(text.GAP_NEXT_UNDECIDED_BUTTON, key="gap-next-undecided"):
    following = next_undecided_gap_id(cards, current)
    if following is None:
      st.info(text.GAP_NO_UNDECIDED_SHOWN)
    else:
      st.session_state[GAP_FOCUS_KEY] = following
      current = following
  return current


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
      render_evidence_body(passage.passage, passage.context)

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
