"""
Run buttons that launch a pipeline stage through the runner, and their outcome.

The page renders a button; clicking it runs the stage via `pipeline_runner`
(a blocking subprocess, ADR 0007) and keeps the result in session state across
the rerun that follows, so the page redraws with the new artifact while the
outcome and the captured output stay on screen.
"""

from pathlib import Path

import streamlit as st

from research_gap_dashboard.dashboard import pipeline_runner, text
from research_gap_dashboard.dashboard.pipeline_runner import RunOutcome, RunResult
from research_gap_dashboard.dashboard.views.session_keys import LAST_RUN_KEY


def render_run_button(corpus_root: Path, stage: str, label: str) -> None:
  """Render a button that runs `stage` on the Corpus, then redraws the page."""
  if not st.button(label, key=f"run-{stage}", type="primary"):
    return
  with st.spinner(text.RUN_IN_PROGRESS.format(stage=stage)):
    # Looked up on the module at call time so tests can patch the subprocess.
    result = pipeline_runner.run_stage(corpus_root, stage)
  st.session_state[LAST_RUN_KEY] = (str(corpus_root), result)
  st.rerun()


def render_last_run(corpus_root: Path) -> None:
  """Show the outcome of the last run on this Corpus, if there was one."""
  stored = st.session_state.get(LAST_RUN_KEY)
  if stored is None or stored[0] != str(corpus_root):
    return
  result: RunResult = stored[1]
  message = _outcome_message(result)
  if result.outcome == RunOutcome.SUCCEEDED:
    st.success(message)
  elif result.outcome in (RunOutcome.INCOMPLETE, RunOutcome.LOCKED):
    st.warning(message)
  else:
    st.error(message)
  if result.outcome in (RunOutcome.LOCKED, RunOutcome.MISSING_KEY):
    return
  with st.expander(text.RUN_DETAILS_LABEL):
    st.caption(
      text.RUN_COMMAND_LABEL.format(
        command=" ".join(result.command),
        exit_code="\u2014" if result.exit_code is None else result.exit_code,
      )
    )
    st.markdown(f"**{text.RUN_STDOUT_LABEL}**")
    st.code(result.stdout or text.RUN_NO_OUTPUT, language=None)
    st.markdown(f"**{text.RUN_STDERR_LABEL}**")
    st.code(result.stderr or text.RUN_NO_OUTPUT, language=None)


def _outcome_message(result: RunResult) -> str:
  """Return the researcher-facing sentence for a run's outcome."""
  templates = {
    RunOutcome.SUCCEEDED: text.RUN_SUCCEEDED,
    RunOutcome.INCOMPLETE: text.RUN_INCOMPLETE,
    RunOutcome.FAILED: text.RUN_FAILED,
    RunOutcome.LOCKED: text.RUN_LOCKED,
    RunOutcome.MISSING_KEY: text.RUN_MISSING_KEY,
  }
  return templates[result.outcome].format(stage=result.stage)
