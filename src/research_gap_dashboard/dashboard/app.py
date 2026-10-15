"""
The Streamlit app shell: corpus selection, navigation, and page dispatch.

This layer is deliberately thin. It picks a Corpus, applies any pending page
switch, and hands the chosen page to its renderer in `dashboard.page_registry`;
each page lives in its own module under `dashboard.views`. It holds no analysis
logic and imports no pipeline stage (ADR 0002). Run it with `streamlit run` (see
the justfile). Point it at the folder that holds your corpus directories with the
RESEARCH_GAP_CORPORA_DIR environment variable.
"""

import os
from pathlib import Path

import streamlit as st

from research_gap_dashboard.dashboard import text
from research_gap_dashboard.dashboard.artifacts import (
  CorpusChoice,
  discover_corpora,
  discover_uningested_corpora,
)
from research_gap_dashboard.dashboard.attention import build_attention
from research_gap_dashboard.dashboard.page_registry import page_for, page_titles
from research_gap_dashboard.dashboard.views.run_controls import (
  render_last_run,
  render_run_button,
)
from research_gap_dashboard.dashboard.views.session_keys import (
  NAV_PAGE_KEY,
  PENDING_NAV_KEY,
)
from research_gap_dashboard.env import load_env

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
    page_titles(),
    key=NAV_PAGE_KEY,
    label_visibility="collapsed",
  )


def main() -> None:
  """Run the dashboard: pick a corpus and page, then render it."""
  load_env()
  st.set_page_config(page_title=text.APP_TITLE, layout="wide")
  st.title(text.APP_TITLE)

  choices = discover_corpora(_corpora_root())
  if not choices:
    _render_no_corpora(_corpora_root())
    return

  chosen = _select_corpus(choices)
  _render_incompleteness_banner(chosen.root)
  _apply_pending_nav()
  page = _select_page()
  page_for(page).render(chosen)


def _render_no_corpora(corpora_root: Path) -> None:
  """With nothing ingested, offer to ingest each corpus folder found instead."""
  st.warning(text.NO_CORPORA)
  folders = discover_uningested_corpora(corpora_root)
  if not folders:
    st.info(text.NO_CORPUS_FOLDERS)
    return
  for folder in folders:
    render_last_run(folder.root)
    render_run_button(
      folder.root,
      "ingest",
      text.RUN_INGEST_CORPUS_BUTTON.format(name=folder.name),
      key=f"run-ingest-{folder.name}",
    )


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


if __name__ == "__main__":
  main()
