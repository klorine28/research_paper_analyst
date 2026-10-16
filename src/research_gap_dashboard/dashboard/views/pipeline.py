"""
The Pipeline page: every stage's status, with a run or re-run button for each.

Status (done / not run / stale) comes from `pipeline_status`; the buttons launch
the stage through `run_controls` (ADR 0007). Cost is kept out of the way, in a
collapsed "Run details" expander with each LLM stage's estimate and the last
run's actual cost (issue #70). Running detect here also runs the automatic
saturation checks after it (`saturate --auto`, issue #89). An Add Papers panel
uploads more Papers into an ingested Corpus and checks their pairing before re-ingesting (issue #75).
"""

from pathlib import Path

import streamlit as st

from research_gap_dashboard.dashboard import text
from research_gap_dashboard.dashboard.artifacts import CorpusChoice
from research_gap_dashboard.dashboard.pipeline_status import (
  STAGES,
  StageState,
  StageStatus,
  build_pipeline_status,
)
from research_gap_dashboard.dashboard.run_cost import (
  CostConfigError,
  actual_stage_cost,
  cost_confirm_threshold,
  estimate_stage_cost,
  uses_llm,
)
from research_gap_dashboard.dashboard.views.paper_upload import render_paper_upload
from research_gap_dashboard.dashboard.views.run_controls import (
  render_last_run,
  render_run_button,
)

# A stale stage names this many of its newer inputs before summarising the rest.
_NEWER_INPUTS_SHOWN = 3

# Detect is followed, under the same lock, by the automatic saturation checks
# of its high-confidence gaps (ADR 0009 §5); detect itself never searches.
_CHAINED_AFTER: dict[str, tuple[str, ...]] = {"detect": ("saturate",)}
_CHAINED_ARGS: dict[str, tuple[str, ...]] = {"saturate": ("--auto",)}


def render_page(chosen: CorpusChoice) -> None:
  """Render the pipeline status panel for the chosen Corpus."""
  corpus_root = chosen.root
  st.header(text.PIPELINE_HEADING)
  render_last_run(corpus_root)
  st.markdown(text.PIPELINE_INTRO)
  render_paper_upload(corpus_root, expanded=False)
  for status in build_pipeline_status(corpus_root):
    _render_stage(corpus_root, status)
  _render_run_details(corpus_root)


def _render_stage(corpus_root: Path, status: StageStatus) -> None:
  """Render one stage's status line and its run or re-run button."""
  with st.container(border=True):
    st.markdown(
      text.STAGE_ROW.format(
        stage=status.stage,
        state=text.STAGE_STATE_LABELS[status.state.value],
        artifact=status.artifact,
      )
    )
    if status.last_run is not None:
      st.caption(text.STAGE_LAST_RUN.format(when=status.last_run))
    if status.newer_inputs:
      st.caption(_newer_inputs(status.newer_inputs))
    template = (
      text.RUN_STAGE_BUTTON
      if status.state == StageState.NOT_RUN
      else text.RERUN_STAGE_BUTTON
    )
    if status.stage in _CHAINED_AFTER:
      st.caption(text.PIPELINE_AUTO_SATURATE_NOTE)
    render_run_button(
      corpus_root,
      status.stage,
      template.format(stage=status.stage),
      then=_CHAINED_AFTER.get(status.stage, ()),
      stage_args=_CHAINED_ARGS,
    )


def _newer_inputs(inputs: list[str]) -> str:
  shown = ", ".join(f"`{path}`" for path in inputs[:_NEWER_INPUTS_SHOWN])
  hidden = len(inputs) - _NEWER_INPUTS_SHOWN
  more = text.STAGE_NEWER_INPUTS_MORE.format(count=hidden) if hidden > 0 else ""
  return text.STAGE_NEWER_INPUTS.format(inputs=shown + more)


def _render_run_details(corpus_root: Path) -> None:
  """Render the collapsed expander with each LLM stage's estimated and actual cost."""
  llm_stages = [spec.stage for spec in STAGES if uses_llm(spec.stage)]
  estimates = {stage: estimate_stage_cost(corpus_root, stage) for stage in llm_stages}
  with st.expander(text.RUN_DETAILS_LABEL, expanded=False):
    try:
      threshold = _usd(cost_confirm_threshold())
    except CostConfigError as error:
      st.error(str(error))
      threshold = _usd(0.0)
    first = estimates[llm_stages[0]]
    st.caption(
      text.RUN_DETAILS_INTRO.format(
        papers=first.paper_count, model=first.model, threshold=threshold
      )
    )
    rows = [text.RUN_COST_HEADER]
    for stage in llm_stages:
      estimate = estimates[stage].usd
      actual = actual_stage_cost(corpus_root, stage)
      rows.append(
        text.RUN_COST_ROW.format(
          stage=stage,
          estimate=text.RUN_COST_UNPRICED if estimate is None else _usd(estimate),
          actual=_actual(actual.usd) if actual else text.RUN_COST_NOT_RECORDED,
          calls=(
            f"{actual.calls} ({actual.cached_calls})"
            if actual
            else text.RUN_COST_NOT_APPLICABLE
          ),
        )
      )
    st.markdown("\n".join(rows))


def _actual(usd: float | None) -> str:
  return text.RUN_COST_UNPRICED if usd is None else _usd(usd)


def _usd(amount: float) -> str:
  return f"${amount:,.2f}"
