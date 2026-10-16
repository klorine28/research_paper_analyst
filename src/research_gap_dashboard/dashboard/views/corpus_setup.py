"""
Corpus set-up in the shell: the New analysis form and the scope statement.

The New analysis form creates an empty Corpus with its scope through
`dashboard.corpus_scope`; the scope statement is then rendered above every
page, and a Corpus without one shows a prompt to add it (issue #74). A Corpus
that has no manifest yet gets a holding page instead of the analysis pages,
which all read the manifest.
"""

from pathlib import Path

import streamlit as st
from streamlit.delta_generator import DeltaGenerator

from research_gap_dashboard.dashboard import text
from research_gap_dashboard.dashboard.artifacts import CorpusChoice
from research_gap_dashboard.dashboard.corpus_scope import (
  CorpusCreationError,
  create_corpus,
  load_scope,
  save_scope,
)
from research_gap_dashboard.dashboard.views.run_controls import (
  render_last_run,
  render_run_button,
)
from research_gap_dashboard.dashboard.views.session_keys import PENDING_CORPUS_KEY


def render_new_analysis(
  container: DeltaGenerator, corpora_root: Path, *, expanded: bool
) -> None:
  """Render the New analysis form; on success, open the new Corpus."""
  with container.expander(text.NEW_CORPUS_HEADING, expanded=expanded):
    st.caption(text.NEW_CORPUS_INTRO)
    with st.form("new_corpus_form", clear_on_submit=False):
      name = st.text_input(
        text.NEW_CORPUS_NAME_LABEL,
        key="new_corpus_name",
        help=text.NEW_CORPUS_NAME_HELP,
      )
      question, field, searched = _scope_inputs("new_corpus")
      submitted = st.form_submit_button(text.NEW_CORPUS_SUBMIT, type="primary")
    if not submitted:
      return
    try:
      choice = create_corpus(
        corpora_root, name=name, question=question, field=field, searched=searched
      )
    except CorpusCreationError as error:
      st.error(str(error))
      return
  # The picker already exists this run, so the switch is applied on the next.
  st.session_state[PENDING_CORPUS_KEY] = choice.name
  st.rerun()


def render_scope(corpus_root: Path) -> None:
  """State the Corpus's scope, or prompt for one when it was never recorded."""
  scope = load_scope(corpus_root)
  if scope is not None:
    st.info(
      text.CORPUS_SCOPE_STATEMENT.format(
        question=scope.question, field=scope.field, searched=scope.searched
      )
    )
    return
  st.warning(text.SCOPE_MISSING_PROMPT)
  with st.expander(text.SCOPE_ADD_EXPANDER):
    with st.form("add_scope_form"):
      question, field, searched = _scope_inputs("add_scope")
      submitted = st.form_submit_button(text.SCOPE_SAVE_BUTTON, type="primary")
    if not submitted:
      return
    try:
      save_scope(corpus_root, question=question, field=field, searched=searched)
    except CorpusCreationError as error:
      st.error(str(error))
      return
  st.rerun()


def render_unbuilt_corpus(chosen: CorpusChoice) -> None:
  """Render the holding page for a Corpus whose Papers have not been ingested yet."""
  root = chosen.root
  st.info(text.CORPUS_HAS_NO_PAPERS.format(papers_dir=root / "papers", root=root))
  render_last_run(root)
  render_run_button(root, "ingest", text.RUN_INGEST_BUTTON)


def _scope_inputs(key_prefix: str) -> tuple[str, str, str]:
  """Render the three scope fields inside a form and return their values."""
  question = st.text_area(text.SCOPE_QUESTION_LABEL, key=f"{key_prefix}_question")
  field = st.text_input(text.SCOPE_FIELD_LABEL, key=f"{key_prefix}_field")
  searched = st.text_area(
    text.SCOPE_SEARCHED_LABEL,
    key=f"{key_prefix}_searched",
    help=text.SCOPE_SEARCHED_HELP,
  )
  return question, field, searched
