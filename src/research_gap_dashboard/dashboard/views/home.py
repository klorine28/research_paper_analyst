"""
The Home screen: Corpus health and the recommended next step (issue #76).

The recommendation and the completed steps come from `guided_flow`, which reads
the pipeline status and the fallout on disk; this module only lays them out.
The Home screen knows steps, not pages: its button records the step and the
shell opens that step's first page, so the page registry stays the one place
that maps steps to pages.
"""

import streamlit as st

from research_gap_dashboard.dashboard import text
from research_gap_dashboard.dashboard.artifacts import CorpusChoice
from research_gap_dashboard.dashboard.guided_flow import (
  STEPS,
  FlowProgress,
  Step,
  build_flow_progress,
  step_label,
)
from research_gap_dashboard.dashboard.views.session_keys import PENDING_STEP_KEY

RECOMMENDED_BUTTON_KEY = "home-recommended-step"


def render_page(chosen: CorpusChoice) -> None:
  """Render the Home screen for the chosen Corpus."""
  progress = build_flow_progress(chosen.root)
  st.header(text.HOME_HEADING)
  st.caption(text.HOME_INTRO)
  _render_recommendation(progress)
  _render_health(progress)
  _render_steps(progress)


def _open_step(step: Step) -> None:
  st.session_state[PENDING_STEP_KEY] = step


def _render_recommendation(progress: FlowProgress) -> None:
  with st.container(border=True):
    st.subheader(text.HOME_RECOMMENDED_HEADING)
    st.markdown(f"**{step_label(progress.recommended)}**: {progress.reason}")
    st.button(
      text.HOME_GO_TO_STEP.format(step=step_label(progress.recommended)),
      key=RECOMMENDED_BUTTON_KEY,
      type="primary",
      on_click=_open_step,
      args=(progress.recommended,),
    )


def _render_health(progress: FlowProgress) -> None:
  health = progress.health
  st.subheader(text.HOME_HEALTH_HEADING)
  papers, reached, stages = st.columns(3)
  papers.metric(text.HOME_METRIC_PAPERS, health.total_papers)
  reached.metric(
    text.HOME_METRIC_REACHED, f"{health.reached_papers} / {health.total_papers}"
  )
  stages.metric(
    text.HOME_METRIC_STAGES, f"{health.stages_done} / {health.stages_total}"
  )
  gaps, accepted, undecided = st.columns(3)
  gaps.metric(text.HOME_METRIC_GAPS, health.gap_count)
  accepted.metric(text.HOME_METRIC_ACCEPTED, health.accepted)
  undecided.metric(text.HOME_METRIC_UNDECIDED, health.undecided)
  st.caption(text.HOME_HEALTH_CAPTION.format(total=health.total_papers))


def _render_steps(progress: FlowProgress) -> None:
  st.subheader(text.HOME_STEPS_HEADING)
  lines = [
    text.HOME_STEP_LINE.format(
      mark=text.STEP_DONE_MARK if step in progress.completed else "",
      label=step_label(step),
      blurb=text.STEP_BLURBS[step],
    )
    for step in STEPS
  ]
  st.markdown("\n".join(f"- {line}" for line in lines))
