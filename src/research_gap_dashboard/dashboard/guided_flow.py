"""
The guided flow read model: where a Corpus stands on the researcher's path.

The path is Scope → Build → Trust → Explore → Judge → Grow → Write, grouped into
the phases Set up, Check, Analyse and Extend (the navigation prototype verdict,
issue #76). This module says which steps are complete and which one the home
screen recommends next, from what is on disk only: the scope record, the
pipeline status (existence + mtime, ADR 0007), the fallout the attention report
finds, and the researcher's gap verdicts. It never blocks anything; free
navigation stays, and the soft Trust gate is a separate concern.

Step completion, step by step:

- Scope: a scope statement is recorded.
- Build: every core stage (the ones Candidate Gaps depend on) is done, not
  stale. `explain` and `retrieve` feed single pages and do not hold Build back.
- Trust: every Paper reached the artifact with fully verified facts.
- Explore: judging has started (any verdict recorded); the map comes first.
- Judge: every Candidate Gap has a verdict.
- Grow: the optional retrieve stage is done.
- Write: never marked here; nothing on disk yet says a write-up was made.

Like every dashboard read model it imports no pipeline stage (ADR 0002).
"""

from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel

from research_gap_dashboard.dashboard import text
from research_gap_dashboard.dashboard.artifacts import (
  has_candidate_gaps,
  load_candidate_gaps,
)
from research_gap_dashboard.dashboard.attention import build_attention
from research_gap_dashboard.dashboard.corpus_scope import has_scope
from research_gap_dashboard.dashboard.judgments import load_judgments
from research_gap_dashboard.dashboard.pipeline_status import (
  StageState,
  build_pipeline_status,
)


class Step(StrEnum):
  """One step of the guided path, in path order."""

  SCOPE = "scope"
  BUILD = "build"
  TRUST = "trust"
  EXPLORE = "explore"
  JUDGE = "judge"
  GROW = "grow"
  WRITE = "write"


STEPS: tuple[Step, ...] = tuple(Step)

# The sidebar's phase groups, each with its steps in path order.
PHASES: tuple[tuple[str, tuple[Step, ...]], ...] = (
  ("set_up", (Step.SCOPE, Step.BUILD)),
  ("check", (Step.TRUST,)),
  ("analyse", (Step.EXPLORE, Step.JUDGE)),
  ("extend", (Step.GROW, Step.WRITE)),
)

# The stages Candidate Gaps are built from; Build is done when all of them are.
CORE_STAGES = ("ingest", "parse", "extract", "aggregate", "detect")
_GROW_STAGE = "retrieve"


def step_number(step: Step) -> int:
  """Return a step's 1-based position on the path."""
  return STEPS.index(step) + 1


def step_label(step: Step) -> str:
  """Return a step's numbered label, e.g. '3 · Trust'."""
  return text.STEP_LABEL.format(number=step_number(step), name=text.STEP_NAMES[step])


class CorpusHealth(BaseModel):
  """The counts the home screen shows, each with its denominator."""

  total_papers: int
  reached_papers: int
  # Papers that fell out or lost facts: what the Trust step asks to look at.
  attention_papers: int
  stages_done: int
  stages_total: int
  gap_count: int
  accepted: int
  rejected: int
  undecided: int


class FlowProgress(BaseModel):
  """Which steps are complete, the recommended next step and why, and health."""

  completed: list[Step]
  recommended: Step
  reason: str
  health: CorpusHealth


def build_flow_progress(corpus_root: Path) -> FlowProgress:
  """Read a Corpus's place on the guided path from its files on disk."""
  stages = {status.stage: status.state for status in build_pipeline_status(corpus_root)}
  banner = build_attention(corpus_root).banner
  gap_ids = (
    [gap.gap_id for gap in load_candidate_gaps(corpus_root).gaps]
    if has_candidate_gaps(corpus_root)
    else []
  )
  verdicts = load_judgments(corpus_root).by_gap_id()
  decisions = [verdicts[gap_id].decision for gap_id in gap_ids if gap_id in verdicts]
  health = CorpusHealth(
    total_papers=banner.total_papers,
    reached_papers=banner.reached_artifact,
    attention_papers=banner.missing_papers + banner.papers_with_dropped_facts,
    stages_done=sum(1 for state in stages.values() if state == StageState.DONE),
    stages_total=len(stages),
    gap_count=len(gap_ids),
    accepted=decisions.count("accepted"),
    rejected=decisions.count("rejected"),
    undecided=len(gap_ids) - len(decisions),
  )
  done = {
    Step.SCOPE: has_scope(corpus_root),
    Step.BUILD: all(stages[stage] == StageState.DONE for stage in CORE_STAGES),
    Step.TRUST: banner.is_complete,
    Step.EXPLORE: bool(decisions),
    Step.JUDGE: health.gap_count > 0 and health.undecided == 0,
    Step.GROW: stages[_GROW_STAGE] == StageState.DONE,
    Step.WRITE: False,
  }
  recommended, reason = _recommend(done, stages, health)
  return FlowProgress(
    completed=[step for step in STEPS if done[step]],
    recommended=recommended,
    reason=reason,
    health=health,
  )


def _recommend(
  done: dict[Step, bool], stages: dict[str, StageState], health: CorpusHealth
) -> tuple[Step, str]:
  """Pick the first step that still needs work; Grow is optional, never picked."""
  if not done[Step.SCOPE]:
    return Step.SCOPE, text.RECOMMEND_SCOPE
  for stage in CORE_STAGES:
    if stages[stage] == StageState.NOT_RUN:
      return Step.BUILD, text.RECOMMEND_BUILD_NOT_RUN.format(stage=stage)
    if stages[stage] == StageState.STALE:
      return Step.BUILD, text.RECOMMEND_BUILD_STALE.format(stage=stage)
  if not done[Step.TRUST]:
    return Step.TRUST, text.RECOMMEND_TRUST.format(
      reached=health.reached_papers,
      total=health.total_papers,
      attention=health.attention_papers,
    )
  if not done[Step.EXPLORE]:
    return Step.EXPLORE, text.RECOMMEND_EXPLORE.format(gaps=health.gap_count)
  if health.undecided:
    return Step.JUDGE, text.RECOMMEND_JUDGE.format(
      undecided=health.undecided, gaps=health.gap_count
    )
  return Step.WRITE, text.RECOMMEND_WRITE.format(accepted=health.accepted)
