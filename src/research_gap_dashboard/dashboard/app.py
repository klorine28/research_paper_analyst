"""
The Streamlit app shell: corpus selection, navigation, and page dispatch.

This layer is deliberately thin. It picks (or creates) a Corpus, states its
scope, applies any pending page switch, draws the guided navigation (the
phase-grouped sidebar, the step bar and Previous/Next, issue #76) and hands the
chosen page to its renderer in `dashboard.page_registry`; each page lives in its
own module under `dashboard.views`. A page on a step the soft Trust gate guards
shows the gate's lock screen instead while the gate is closed (issue #77). It
holds no analysis logic and imports no pipeline stage (ADR 0002). Run it with
`streamlit run` (see the justfile). Point it at the folder that holds your corpus
directories with the RESEARCH_GAP_CORPORA_DIR environment variable; new Corpora
created in the app are made there.
"""

import os
from pathlib import Path

import streamlit as st

from research_gap_dashboard.dashboard import text
from research_gap_dashboard.dashboard.artifacts import (
  CorpusChoice,
  discover_corpora,
  discover_uningested_corpora,
  has_manifest,
)
from research_gap_dashboard.dashboard.attention import build_attention
from research_gap_dashboard.dashboard.guided_flow import (
  GATED_STEPS,
  build_flow_progress,
)
from research_gap_dashboard.dashboard.page_registry import first_page_title, page_for
from research_gap_dashboard.dashboard.trust_gate import build_trust_gate
from research_gap_dashboard.dashboard.views.corpus_setup import (
  render_new_analysis,
  render_scope,
  render_unbuilt_corpus,
)
from research_gap_dashboard.dashboard.views.navigation import (
  render_previous_next,
  render_sidebar_nav,
  render_step_bar,
)
from research_gap_dashboard.dashboard.views.run_controls import (
  render_last_run,
  render_run_button,
)
from research_gap_dashboard.dashboard.views.trust_gate import (
  render_acknowledged_note,
  render_locked_step,
)
from research_gap_dashboard.dashboard.views.session_keys import (
  CORPUS_PICKER_KEY,
  NAV_PAGE_KEY,
  PENDING_CORPUS_KEY,
  PENDING_NAV_KEY,
  PENDING_STEP_KEY,
)
from research_gap_dashboard.env import load_env

CORPORA_DIR_ENV = "RESEARCH_GAP_CORPORA_DIR"
DEFAULT_CORPORA_DIR = "corpora"


def _corpora_root() -> Path:
  """Return the folder that holds the corpus directories to offer."""
  return Path(os.environ.get(CORPORA_DIR_ENV, DEFAULT_CORPORA_DIR))


def _select_corpus(choices: list[CorpusChoice]) -> CorpusChoice:
  """Render the sidebar corpus picker and return the chosen corpus."""
  by_name = {choice.name: choice for choice in choices}
  pending = st.session_state.pop(PENDING_CORPUS_KEY, None)
  if pending in by_name:
    st.session_state[CORPUS_PICKER_KEY] = pending
  elif st.session_state.get(CORPUS_PICKER_KEY) not in by_name:
    st.session_state.pop(CORPUS_PICKER_KEY, None)
  name = st.sidebar.selectbox(
    text.CORPUS_PICKER_LABEL, list(by_name), key=CORPUS_PICKER_KEY
  )
  return by_name[name] if name in by_name else choices[0]


def _apply_pending_nav() -> None:
  """
  Honour a page switch requested last run, before the sidebar is built.

  A page that switches to another (a card's "Discuss this gap", issue #48)
  records the target page, and the Home screen records the recommended step
  (issue #76); either way the switch lands here on the next run.
  """
  pending = st.session_state.pop(PENDING_NAV_KEY, None)
  if pending is not None:
    st.session_state[NAV_PAGE_KEY] = pending
  step = st.session_state.pop(PENDING_STEP_KEY, None)
  if step is not None:
    st.session_state[NAV_PAGE_KEY] = first_page_title(step)


def main() -> None:
  """Run the dashboard: pick a corpus and page, then render it."""
  load_env()
  st.set_page_config(page_title=text.APP_TITLE, layout="wide")
  st.title(text.APP_TITLE)

  corpora_root = _corpora_root()
  choices = discover_corpora(corpora_root)
  if not choices:
    st.info(text.NO_CORPORA)
    render_new_analysis(st.container(), corpora_root, expanded=True)
    _render_ingestable_folders(corpora_root)
    return

  chosen = _select_corpus(choices)
  render_new_analysis(st.sidebar, corpora_root, expanded=False)
  render_scope(chosen.root)
  if not has_manifest(chosen.root):
    render_unbuilt_corpus(chosen)
    return
  _render_incompleteness_banner(chosen.root)
  _apply_pending_nav()
  page = page_for(st.session_state.get(NAV_PAGE_KEY, ""))
  progress = build_flow_progress(chosen.root)
  render_sidebar_nav(page.title, progress)
  render_step_bar(page.step, progress)
  gate = build_trust_gate(chosen.root)
  if page.step is not None and page.step in GATED_STEPS and not gate.is_open:
    render_locked_step(chosen.root, page.step, gate)
  else:
    if gate.state == "acknowledged":
      render_acknowledged_note(gate)
    page.render(chosen)
  st.divider()
  render_previous_next(page.step)


def _render_ingestable_folders(corpora_root: Path) -> None:
  """Offer to ingest each corpus folder that already holds Papers but no manifest."""
  folders = discover_uningested_corpora(corpora_root)
  if not folders:
    return
  st.markdown(text.INGESTABLE_FOLDERS_INTRO)
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
