"""
Run buttons that launch a pipeline stage through the runner, and their outcome.

The page renders a button; clicking it runs the stage (or a chain of commands,
such as a seam command and the stage it feeds) via `pipeline_runner` (a blocking
subprocess, ADR 0007) and keeps the results in session state across the rerun
that follows, so the page redraws with the new artifacts while each outcome and
its captured output stay on screen.
"""

from collections.abc import Sequence
from pathlib import Path

import streamlit as st

from research_gap_dashboard.dashboard import pipeline_runner, text
from research_gap_dashboard.dashboard.pipeline_runner import RunOutcome, RunResult
from research_gap_dashboard.dashboard.views.session_keys import LAST_RUN_KEY


def render_run_button(corpus_root: Path, stage: str, label: str) -> None:
  """Render a button that runs `stage` on the Corpus, then redraws the page."""
  if st.button(label, key=f"run-{stage}", type="primary"):
    run_and_redraw(corpus_root, [stage])


def run_and_redraw(corpus_root: Path, stages: Sequence[str]) -> None:
  """Run CLI commands in order on the Corpus, remember the results, redraw."""
  with st.spinner(text.RUN_IN_PROGRESS.format(stage=" \u2192 ".join(stages))):
    # Looked up on the module at call time so tests can patch the subprocess.
    results = pipeline_runner.run_stages(corpus_root, stages)
  st.session_state[LAST_RUN_KEY] = (str(corpus_root), results)
  st.rerun()


def render_last_run(corpus_root: Path) -> None:
  """Show the outcome of the last run on this Corpus, if there was one."""
  stored = st.session_state.get(LAST_RUN_KEY)
  if stored is None or stored[0] != str(corpus_root):
    return
  results: list[RunResult] = stored[1]
  for result in results:
    _render_result(result)


def _render_result(result: RunResult) -> None:
  """Show one command's outcome, with its captured output behind an expander."""
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
  reason = pipeline_runner.failure_reason(result)
  if result.outcome == RunOutcome.FAILED and reason:
    return text.RUN_FAILED_WITH_REASON.format(stage=result.stage, reason=reason)
  return templates[result.outcome].format(stage=result.stage)
