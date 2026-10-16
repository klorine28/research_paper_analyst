"""
Run buttons that launch a pipeline stage through the runner, and their outcome.

The page renders a button; clicking it runs the stage (or a chain of commands,
such as a seam command and the stage it feeds) via `pipeline_runner` (a blocking
subprocess, ADR 0007) and keeps the results in session state across the rerun
that follows, so the page redraws with the new artifacts while each outcome and
its captured output stay on screen.

A run whose estimated cost is above `LLM_COST_CONFIRM_ABOVE` is not launched on
the first click: the button records it as pending and the next rerun asks the
researcher to confirm or cancel, under the button that asked. A chain asks when
any of its stages would. The prompt names no amount; the estimate itself is only
in the pipeline panel's collapsed Run details (issue #70).
"""

import os
from collections.abc import Sequence
from pathlib import Path

import streamlit as st

from research_gap_dashboard.dashboard import pipeline_runner, text
from research_gap_dashboard.dashboard.pipeline_runner import RunOutcome, RunResult
from research_gap_dashboard.dashboard.run_cost import (
  CostConfigError,
  cost_confirm_threshold,
  estimate_stage_cost,
  needs_confirmation,
)
from research_gap_dashboard.dashboard.views.session_keys import (
  LAST_RUN_KEY,
  PENDING_RUN_KEY,
)


def render_run_button(
  corpus_root: Path,
  stage: str,
  label: str | None = None,
  *,
  args: Sequence[str] = (),
  key: str | None = None,
  primary: bool = True,
) -> None:
  """
  Render a button that runs `stage` on the Corpus, then redraws the page.

  `key` tells apart two buttons for the same stage on one page (e.g. one per
  Corpus, or one per empty chart); it defaults to the stage name. Every button
  goes through the cost confirmation, wherever it sits. `primary=False` draws
  a quieter button, for one repeated on every card.
  """
  label = label or text.run_stage_button(stage)
  button_key = key or f"run-{stage}"
  if render_pending_confirmation(corpus_root, button_key):
    return
  if st.button(label, key=button_key, type="primary" if primary else "secondary"):
    request_run(corpus_root, [stage], button_key, args)


def request_run(
  corpus_root: Path,
  stages: Sequence[str],
  button_key: str,
  args: Sequence[str] = (),
) -> None:
  """
  Launch a run now, or park it for confirmation if its estimate is too high.

  A parked run is shown by `render_pending_confirmation` under the button
  `button_key` names, on the next rerun.
  """
  if any(_must_confirm(corpus_root, stage) for stage in stages):
    st.session_state[PENDING_RUN_KEY] = (
      str(corpus_root),
      tuple(stages),
      button_key,
      tuple(args),
    )
    st.rerun()
  _launch(corpus_root, stages, args)


def render_pending_confirmation(corpus_root: Path, button_key: str) -> bool:
  """Ask to confirm the run parked under this button, if any; True if asked."""
  pending = st.session_state.get(PENDING_RUN_KEY)
  if pending is None or pending[0] != str(corpus_root) or pending[2] != button_key:
    return False
  _, stages, _, args = pending
  st.warning(text.RUN_CONFIRM_PROMPT.format(stage=" \u2192 ".join(stages)))
  confirm, cancel = st.columns(2)
  if confirm.button(
    text.RUN_CONFIRM_BUTTON, key=f"confirm-{button_key}", type="primary"
  ):
    st.session_state.pop(PENDING_RUN_KEY, None)
    _launch(corpus_root, stages, args)
  if cancel.button(text.RUN_CANCEL_BUTTON, key=f"cancel-{button_key}"):
    st.session_state.pop(PENDING_RUN_KEY, None)
    st.rerun()
  return True


def _must_confirm(corpus_root: Path, stage: str) -> bool:
  """
  Whether this run's estimate needs the researcher's confirmation.

  A stage that will be refused for a missing key launches nothing, so it isn't
  worth confirming. An unreadable threshold is shown and treated as zero, so a
  misconfiguration asks too often rather than never.
  """
  if pipeline_runner.needs_api_key(stage) and not os.environ.get(
    pipeline_runner.API_KEY_ENV
  ):
    return False
  try:
    threshold = cost_confirm_threshold()
  except CostConfigError as error:
    st.error(str(error))
    threshold = 0.0
  return needs_confirmation(estimate_stage_cost(corpus_root, stage), threshold)


def _launch(corpus_root: Path, stages: Sequence[str], args: Sequence[str] = ()) -> None:
  """Run the stages now under one lock, keep their results, and redraw."""
  with st.spinner(text.RUN_IN_PROGRESS.format(stage=" \u2192 ".join(stages))):
    # Looked up on the module at call time so tests can patch the subprocess.
    results = pipeline_runner.run_stages(corpus_root, stages, args=args)
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
  with st.expander(text.RUN_OUTPUT_LABEL):
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
  # A refusal (e.g. a gold promotion) names its cause; show it, not just "failed".
  reason = pipeline_runner.failure_reason(result)
  if result.outcome == RunOutcome.FAILED and reason:
    return text.RUN_FAILED_WITH_REASON.format(stage=result.stage, reason=reason)
  template = templates[result.outcome]
  if result.outcome == RunOutcome.INCOMPLETE:
    template = text.RUN_INCOMPLETE_BY_STAGE.get(result.stage, template)
  return template.format(stage=result.stage)


def render_run_buttons(corpus_root: Path, stages: Sequence[str], *, scope: str) -> None:
  """
  Render one run button per missing stage, in pipeline order, side by side.

  This is what every empty or guard state offers in place of a CLI command
  (ADR 0007). `scope` keeps the keys unique when one page shows several guards.
  """
  for column, stage in zip(st.columns(len(stages)), stages):
    with column:
      render_run_button(corpus_root, stage, key=f"run-{scope}-{stage}")
