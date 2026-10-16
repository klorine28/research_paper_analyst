"""
The Grow-path controls under a suggested Paper: add, upload, mark unavailable.

One component for both routes (a Retrieval Gap candidate, a saturation hit),
so a paper suggested twice behaves the same in both places (ADR 0009 §11).
Every action runs a pipeline command through `dashboard.suggestions`; a
successful add then re-runs the stages through `request_run`, so the cost
confirmation and the run outcome work as for any other run button (ADR 0007).
"""

from pathlib import Path

import streamlit as st

from research_gap_dashboard.dashboard import text
from research_gap_dashboard.dashboard.pipeline_runner import RunOutcome
from research_gap_dashboard.dashboard.suggestions import (
  RERUN_STAGES,
  AddResult,
  Suggestion,
  SuggestionState,
  add_suggestion,
  mark_suggestion_unavailable,
  was_added,
)
from research_gap_dashboard.dashboard.pipeline_runner import failure_reason
from research_gap_dashboard.dashboard.views.run_controls import (
  render_pending_confirmation,
  request_run,
)
from research_gap_dashboard.dashboard.views.session_keys import SUGGESTION_OUTCOME_KEY


def render_suggestion_actions(
  corpus_root: Path, suggestion: Suggestion, state: SuggestionState, *, scope: str
) -> None:
  """
  Show where the suggestion stands and the actions that fit its state.

  `scope` keeps widget keys unique when one work is offered in several places
  on a page (the same hit on two gap cards).
  """
  key = f"{scope}-{suggestion.key}"
  rerun_key = f"rerun-after-add-{key}"
  _render_outcome(corpus_root, key)
  if render_pending_confirmation(corpus_root, rerun_key):
    return
  if state.status == "added":
    st.caption(
      text.SUGGEST_STATE_ADDED.format(date=state.updated_on, key=state.citation_key)
    )
    return
  if state.status == "pending":
    st.caption(
      text.SUGGEST_STATE_PENDING.format(date=state.updated_on, reason=state.reason)
    )
  elif state.unavailable:
    st.caption(
      text.SUGGEST_STATE_UNAVAILABLE.format(date=state.updated_on, reason=state.reason)
    )
  else:
    if st.button(text.SUGGEST_ADD_BUTTON, key=f"add-{key}", help=text.SUGGEST_ADD_HELP):
      with st.spinner(text.SUGGEST_IN_PROGRESS):
        result = add_suggestion(corpus_root, suggestion)
      _after_add(corpus_root, key, result)
    return
  _render_upload(corpus_root, suggestion, key)
  if state.status == "pending":
    _render_mark_unavailable(corpus_root, suggestion, key)


def _render_upload(corpus_root: Path, suggestion: Suggestion, key: str) -> None:
  with st.form(f"upload-suggested-{key}", clear_on_submit=True):
    pdf = st.file_uploader(text.SUGGEST_UPLOAD_LABEL, type=["pdf"], key=f"pdf-{key}")
    submitted = st.form_submit_button(text.SUGGEST_UPLOAD_SUBMIT)
  if not submitted:
    return
  if pdf is None:
    st.warning(text.SUGGEST_UPLOAD_MISSING)
    return
  result = add_suggestion(corpus_root, suggestion, upload=pdf.getvalue())
  _after_add(corpus_root, key, result)


def _render_mark_unavailable(
  corpus_root: Path, suggestion: Suggestion, key: str
) -> None:
  with st.form(f"unavailable-{key}", clear_on_submit=True):
    reason = st.text_input(text.SUGGEST_UNAVAILABLE_LABEL, key=f"reason-{key}")
    submitted = st.form_submit_button(text.SUGGEST_UNAVAILABLE_SUBMIT)
  if not submitted:
    return
  if not reason.strip():
    st.warning(text.SUGGEST_UNAVAILABLE_NO_REASON)
    return
  run = mark_suggestion_unavailable(corpus_root, suggestion, reason.strip())
  if run.outcome == RunOutcome.SUCCEEDED:
    _store(corpus_root, key, "success", text.SUGGEST_MARKED)
  elif run.outcome == RunOutcome.LOCKED:
    _store(corpus_root, key, "warning", text.SUGGEST_LOCKED)
  else:
    reason_text = failure_reason(run) or run.outcome.value
    _store(
      corpus_root, key, "error", text.SUGGEST_MARK_FAILED.format(reason=reason_text)
    )
  st.rerun()


def _after_add(corpus_root: Path, key: str, result: AddResult) -> None:
  """Keep the add's outcome on screen; re-run the stages once a Paper was added."""
  if was_added(result):
    _store(
      corpus_root, key, "success", text.SUGGEST_ADDED.format(message=result.message)
    )
    request_run(corpus_root, RERUN_STAGES, f"rerun-after-add-{key}")
    return
  if result.status == "already_in_corpus":
    _store(corpus_root, key, "info", text.SUGGEST_ALREADY)
  elif result.status == "pending":
    _store(
      corpus_root, key, "warning", text.SUGGEST_PENDING.format(message=result.message)
    )
  elif result.run.outcome == RunOutcome.LOCKED:
    _store(corpus_root, key, "warning", text.SUGGEST_LOCKED)
  else:
    _store(corpus_root, key, "error", text.SUGGEST_FAILED.format(reason=result.message))
  st.rerun()


def _store(corpus_root: Path, key: str, level: str, message: str) -> None:
  st.session_state[SUGGESTION_OUTCOME_KEY] = (str(corpus_root), key, level, message)


def _render_outcome(corpus_root: Path, key: str) -> None:
  stored = st.session_state.get(SUGGESTION_OUTCOME_KEY)
  if stored is None or stored[0] != str(corpus_root) or stored[1] != key:
    return
  level, message = stored[2], stored[3]
  {"success": st.success, "info": st.info, "warning": st.warning}.get(level, st.error)(
    message
  )
