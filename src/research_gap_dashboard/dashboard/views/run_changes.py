"""
The "changed since last run" panel, shown after a detect run and on Home.

The comparison and its wording come from `dashboard.run_changes`; this module
only lays them out. Nothing renders before a Corpus has had two detect runs.
"""

from pathlib import Path

import streamlit as st

from research_gap_dashboard.dashboard import text
from research_gap_dashboard.dashboard.run_changes import (
  build_run_changes,
  details_markdown,
  summary_text,
)


def render_run_changes(corpus_root: Path) -> None:
  """Show what the last detect run changed on this Corpus, if it had a run before."""
  changes = build_run_changes(corpus_root)
  if changes is None:
    return
  with st.container(border=True):
    st.subheader(text.RUN_CHANGES_HEADING)
    st.markdown(summary_text(changes))
    if changes.has_changes or changes.new_citation_keys:
      with st.expander(text.RUN_CHANGES_DETAILS_LABEL):
        st.markdown(details_markdown(changes))
