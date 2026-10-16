"""
The Gap Cards page: one card per Candidate Gap, with Evidence and accept/reject.

A card's wider-literature hits can be added to the Corpus through the shared
Grow path; a hit that is also a Retrieval Gap candidate says so (#91).
"""

from pathlib import Path

import streamlit as st

from research_gap_dashboard.dashboard import text
from research_gap_dashboard.dashboard.artifacts import (
  CorpusChoice,
  has_candidate_gaps,
  has_retrieval_gaps,
  load_candidate_gaps,
  load_retrieval_gaps,
  load_saturation_checks,
)
from research_gap_dashboard.dashboard.comparison import MAX_SELECTION, MIN_SELECTION
from research_gap_dashboard.dashboard.gaps import (
  CONFIDENCE_LEVELS,
  DEFAULT_CONFIDENCE_FILTER,
  STATUS_FILTERS,
  FilteredGapCards,
  GapCard,
  GapCardsData,
  SaturationHitView,
  SaturationSearchView,
  SaturationView,
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
from research_gap_dashboard.dashboard.suggestions import load_suggested_papers
from research_gap_dashboard.dashboard.views.run_controls import (
  render_last_run,
  render_run_button,
)
from research_gap_dashboard.dashboard.views.suggestions import (
  render_suggestion_actions,
)
from research_gap_dashboard.dashboard.views.session_keys import (
  COMPARISON_PAPERS_KEY,
  DISCUSS_GAP_KEY,
  EXPLAINER_PAPER_KEY,
  GAP_FILTER_STATUS_KEY,
  GAP_FILTER_TYPE_KEY,
  GAP_FOCUS_KEY,
  GAP_CONFIDENCE_FILTER_KEY,
  GAP_CONFIDENCE_WIDGET_KEY,
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
    render_last_run(chosen.root)
    st.info(text.NO_CANDIDATE_GAPS_ARTIFACT)
    render_run_button(chosen.root, "detect", text.RUN_DETECT_BUTTON)
    return
  data = build_gap_cards(
    load_candidate_gaps(chosen.root),
    load_judgments(chosen.root),
    chosen.root,
    load_saturation_checks(chosen.root),
    suggested=load_suggested_papers(chosen.root),
    retrieval=(
      load_retrieval_gaps(chosen.root) if has_retrieval_gaps(chosen.root) else None
    ),
  )
  render_gap_cards(data, chosen.root)


def render_gap_cards(data: GapCardsData, corpus_root: Path) -> None:
  """Render the Gap Cards page: one card per Candidate Gap, with persistence."""
  st.header(text.GAP_CARDS_HEADING)
  render_last_run(corpus_root)
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
  _render_l1_margins(data)
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


def _render_l1_margins(data: GapCardsData) -> None:
  """State the smallest category sizes that can rank High at this Corpus size."""
  if data.l1_smallest_margins is None:
    return
  high, medium = data.l1_smallest_margins
  if high is None or medium is None:
    return
  st.caption(
    text.GAP_L1_MARGINS.format(total=data.corpus_paper_count, high=high, medium=medium)
  )


def _render_filters(data: GapCardsData) -> FilteredGapCards:
  """Render the confidence, status and Gap Type filters; return what they keep."""
  levels = _confidence_filter()
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
  shown = filter_gap_cards(
    data,
    statuses=statuses,
    gap_types=selected_types,
    confidence_levels=levels,
  )
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
      _render_data(card, corpus_root)
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
  if card.saturation.checkable:
    st.caption(card.saturation.badge)


def _render_justification(card: GapCard) -> None:
  """Tier 2: the explanation, any wider-literature result, and the Evidence."""
  st.write(card.explanation)
  if card.saturation.summary:
    st.write(card.saturation.summary)
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


def _render_data(card: GapCard, corpus_root: Path) -> None:
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
  if card.saturation.checkable:
    _render_saturation(card.saturation, card.gap_id, corpus_root)


def _render_saturation(view: SaturationView, gap_id: str, corpus_root: Path) -> None:
  """Tier 3 drill-down: the verbatim queries, every hit with its verdict, history."""
  st.markdown(f"**{text.GAP_SATURATION_HEADING}**")
  if view.status == "not_checked":
    st.caption(text.GAP_SATURATION_NOT_CHECKED)
    return
  st.info(text.GAP_SATURATION_OUT_OF_CORPUS)
  st.caption(
    text.GAP_SATURATION_CHECKED_AT.format(
      date=view.checked_on, method=view.screening_method
    )
  )
  if view.llm_screening:
    st.caption(view.llm_screening)
  st.markdown(f"*{text.GAP_SATURATION_QUERIES_HEADING}*")
  _render_searches([s for s in view.searches if s.attempt == "base"])
  if view.second_attempt_label:
    st.markdown(f"*{view.second_attempt_label}*")
    if view.second_attempt_provenance:
      st.caption(view.second_attempt_provenance)
    if view.second_attempt_note:
      st.caption(view.second_attempt_note)
    _render_searches([s for s in view.searches if s.attempt != "base"])
  st.markdown(
    "*"
    + text.GAP_SATURATION_HITS_HEADING.format(
      screened=view.screened_count, total=len(view.hits)
    )
    + "*"
  )
  offered = [hit for hit in view.hits if not hit.state.unavailable]
  unavailable = [hit for hit in view.hits if hit.state.unavailable]
  if not view.hits:
    st.caption(text.GAP_SATURATION_NO_HITS)
  if offered:
    st.markdown(
      "\n".join(
        "- "
        + text.GAP_SATURATION_HIT.format(
          marker=(
            text.GAP_SATURATION_SCREENED_MARKER
            if hit.screened
            else text.GAP_SATURATION_UNSCREENED_MARKER
          ),
          title=hit.title,
          meta=text.GAP_SATURATION_HIT_META.format(details=hit.details)
          + (text.GAP_SATURATION_SECOND_ATTEMPT_MARKER if hit.second_attempt else ""),
          reason=hit.reason,
        )
        + (
          text.GAP_SATURATION_HIT_LLM.format(
            verdict=hit.llm_verdict, reason=hit.llm_reason
          )
          if hit.llm_verdict
          else ""
        )
        + _hit_markers(hit)
        for hit in offered
      )
    )
    _render_add_hit(offered, gap_id, corpus_root)
  if unavailable:
    st.markdown(f"*{text.GAP_SATURATION_UNAVAILABLE_HEADING}*")
    for hit in unavailable:
      st.markdown(f"- {hit.title}")
      if hit.suggestion is not None:
        render_suggestion_actions(corpus_root, hit.suggestion, hit.state, scope=gap_id)
  st.markdown(f"*{text.GAP_SATURATION_HISTORY_HEADING}*")
  st.markdown("\n".join(view.history))


def _hit_markers(hit: SaturationHitView) -> str:
  """Return the double signal and Grow-path state shown after a hit's line."""
  markers = text.GAP_SATURATION_STATE_MARKERS.get(hit.state.status, "")
  if hit.retrieval_overlap:
    markers += text.GAP_SATURATION_ALSO_RETRIEVAL.format(count=hit.retrieval_overlap)
  return markers


def _render_add_hit(
  hits: list[SaturationHitView], gap_id: str, corpus_root: Path
) -> None:
  """Pick one hit and offer the Grow path for it (one picker, however many hits)."""
  addable = [hit for hit in hits if hit.suggestion is not None]
  if not addable:
    return
  st.markdown(f"*{text.GAP_SATURATION_ADD_HEADING}*")
  index = st.selectbox(
    text.GAP_SATURATION_ADD_PICKER,
    range(len(addable)),
    format_func=lambda i: addable[i].title,
    key=f"add-hit-pick-{gap_id}",
  )
  chosen = addable[index or 0]
  if chosen.suggestion is not None:
    render_suggestion_actions(
      corpus_root, chosen.suggestion, chosen.state, scope=gap_id
    )


def _render_searches(searches: list[SaturationSearchView]) -> None:
  """Each search's source, what it returned, and its query verbatim."""
  for search in searches:
    if search.error:
      st.markdown(
        text.GAP_SATURATION_QUERY_ERROR.format(source=search.source, error=search.error)
      )
    else:
      st.markdown(
        text.GAP_SATURATION_QUERY.format(
          source=search.source, count=search.returned_count
        )
      )
    st.code(search.query, language=None)
    if search.in_corpus_count:
      st.caption(text.GAP_SATURATION_IN_CORPUS.format(count=search.in_corpus_count))


def _render_controls(card: GapCard, corpus_root: Path) -> None:
  """Accept, reject, reset, the Paper links, and the "Discuss" chat, on every card."""
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

  _render_paper_links(card)
  if st.button(text.GAP_DISCUSS_BUTTON, key=f"discuss-{card.gap_id}", width="stretch"):
    st.session_state[DISCUSS_GAP_KEY] = card.gap_id
    st.session_state[PENDING_NAV_KEY] = text.PAGE_ANALYTICS
    st.rerun()
  if card.saturation.checkable:
    render_run_button(
      corpus_root,
      "saturate",
      text.GAP_SATURATION_BUTTON,
      args=["--gap", card.gap_id],
      key=f"saturate-{card.gap_id}",
      primary=False,
    )


def _render_paper_links(card: GapCard) -> None:
  """Open the Explainer for one of the gap's Papers, or compare them all (#80)."""
  if not card.paper_keys:
    return
  explain_col, compare_col = st.columns(2)
  chosen = explain_col.selectbox(
    text.GAP_EXPLAIN_PICKER_LABEL,
    card.paper_keys,
    key=f"explain-pick-{card.gap_id}",
  )
  if explain_col.button(
    text.GAP_EXPLAIN_BUTTON, key=f"explain-{card.gap_id}", width="stretch"
  ):
    st.session_state[EXPLAINER_PAPER_KEY] = chosen
    st.session_state[PENDING_NAV_KEY] = text.PAGE_EXPLAINER
    st.rerun()

  count = len(card.paper_keys)
  if count < MIN_SELECTION:
    compare_col.caption(text.GAP_COMPARE_TOO_FEW)
    return
  if count > MAX_SELECTION:
    compare_col.caption(text.GAP_COMPARE_LIMIT.format(shown=MAX_SELECTION, total=count))
  if compare_col.button(
    text.GAP_COMPARE_BUTTON.format(count=count),
    key=f"compare-{card.gap_id}",
    width="stretch",
  ):
    st.session_state[COMPARISON_PAPERS_KEY] = list(card.paper_keys)
    st.session_state[PENDING_NAV_KEY] = text.PAGE_COMPARISON
    st.rerun()


def _confidence_option_label(level: str) -> str:
  """Show a confidence level the way the cards spell it."""
  return text.CONFIDENCE_LABELS.get(level, level)
