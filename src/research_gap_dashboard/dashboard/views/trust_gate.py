"""
The soft Trust gate's lock screen, shown in place of a gated page (issue #77).

A locked page states the denominator and offers two ways on: fix the fallout on
the Trust step, or acknowledge the denominator and continue. Whether the gate is
open, and the recording of an acknowledgement, live in `dashboard.trust_gate`;
this module only lays them out.
"""

from pathlib import Path

import streamlit as st

from research_gap_dashboard.dashboard import text
from research_gap_dashboard.dashboard.guided_flow import Step, step_label
from research_gap_dashboard.dashboard.page_registry import first_page_title
from research_gap_dashboard.dashboard.trust_gate import (
  TrustGate,
  acknowledge_denominator,
)
from research_gap_dashboard.dashboard.views.session_keys import NAV_PAGE_KEY

ACKNOWLEDGE_BUTTON_KEY = "trust-gate-acknowledge"
FIX_BUTTON_KEY = "trust-gate-fix"


def _open_trust_step() -> None:
  st.session_state[NAV_PAGE_KEY] = first_page_title(Step.TRUST)


def _acknowledge(corpus_root: Path) -> None:
  acknowledge_denominator(corpus_root)


def render_locked_step(corpus_root: Path, step: Step, gate: TrustGate) -> None:
  """Render the lock screen for a gated step of an unacknowledged, partial Corpus."""
  with st.container(border=True):
    st.subheader(text.TRUST_GATE_HEADING)
    st.markdown(
      text.trust_gate_message(
        step_label(step),
        gate.reached_papers,
        gate.total_papers,
        gate.papers_with_dropped_facts,
      )
    )
    fix_col, acknowledge_col = st.columns(2)
    fix_col.button(
      text.TRUST_GATE_FIX_BUTTON,
      key=FIX_BUTTON_KEY,
      width="stretch",
      on_click=_open_trust_step,
    )
    acknowledge_col.button(
      text.TRUST_GATE_ACKNOWLEDGE_BUTTON.format(
        reached=gate.reached_papers,
        total=gate.total_papers,
        dropped=text.TRUST_GATE_ACKNOWLEDGE_DROPPED.format(
          dropped=gate.papers_with_dropped_facts
        )
        if gate.papers_with_dropped_facts
        else "",
      ),
      key=ACKNOWLEDGE_BUTTON_KEY,
      type="primary",
      width="stretch",
      on_click=_acknowledge,
      args=(corpus_root,),
    )


def render_acknowledged_note(gate: TrustGate) -> None:
  """Remind the researcher, under the banner, which denominator they accepted."""
  st.caption(
    text.TRUST_GATE_ACKNOWLEDGED_NOTE.format(
      reached=gate.reached_papers, total=gate.total_papers
    )
  )
