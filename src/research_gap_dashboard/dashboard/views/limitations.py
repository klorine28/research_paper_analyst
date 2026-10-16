"""The Unanswered Limitations page: limitation groups and their follow-ups."""

import streamlit as st

from research_gap_dashboard.dashboard import text
from research_gap_dashboard.dashboard.artifacts import (
  CorpusChoice,
  has_candidate_gaps,
  load_candidate_gaps,
)
from research_gap_dashboard.dashboard.limitations import (
  LimitationGroupView,
  LimitationsData,
  build_limitations,
)
from research_gap_dashboard.dashboard.views.run_controls import (
  render_last_run,
  render_run_buttons,
)


def render_page(chosen: CorpusChoice) -> None:
  """Render the Unanswered Limitations page for the chosen Corpus."""
  if not has_candidate_gaps(chosen.root):
    st.header(text.LIMITATIONS_HEADING)
    render_last_run(chosen.root)
    st.warning(text.NO_CANDIDATE_GAPS_ARTIFACT_LIMITATIONS)
    render_run_buttons(chosen.root, ["detect"], scope="limitations")
    return
  render_limitations(build_limitations(load_candidate_gaps(chosen.root)))


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
