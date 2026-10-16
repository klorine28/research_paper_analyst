"""
What a pipeline run is estimated to cost before it starts, and what it cost.

The estimate is a deliberately rough upper bound: Papers in the Corpus times a
per-Paper token allowance for the stage, priced at the default model's list
price, as if no completion were served from the cache. It exists to decide
whether to ask "are you sure?" (`LLM_COST_CONFIRM_ABOVE`), not to bill anyone.
The actual cost prices the tokens the pipeline recorded for the stage's last
run (`artifacts/llm-usage.json`, written by the CLI).

Prices live here, not in the pipeline: the pipeline records tokens, which don't
change, and the dashboard prices them. A model with no price here has no
estimate, and a run on it always asks for confirmation, since it can't be shown
to be cheap. The dashboard reads the usage log as a file and imports no
pipeline module (ADR 0002).
"""

import json
import math
import os
from collections.abc import Mapping
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from research_gap_dashboard.dashboard.artifacts import (
  ARTIFACTS_DIR,
  ArtifactNotFoundError,
  load_manifest,
)

COST_CONFIRM_ENV = "LLM_COST_CONFIRM_ABOVE"
DEFAULT_COST_CONFIRM_ABOVE = 1.00
MODEL_ENV = "LLM_DEFAULT_MODEL"
# Mirrors the pipeline's default model (llm.DEFAULT_MODEL); restated, not imported.
DEFAULT_MODEL = "claude-sonnet-4-5"
USAGE_LOG_NAME = "llm-usage.json"

# US dollars per million (input, output) tokens, Anthropic list prices. A model
# ID matches the longest entry it starts with, so dated IDs
# (`claude-sonnet-4-5-20250929`) price like their family.
PRICES_PER_MILLION: dict[str, tuple[float, float]] = {
  "claude-sonnet-4": (3.0, 15.0),
  "claude-haiku-4": (1.0, 5.0),
  "claude-opus-4-5": (5.0, 25.0),
  "claude-opus-4": (15.0, 75.0),
}

# Per-Paper (input, output) token allowance for each stage that calls the LLM.
# Extract and explain send a whole Paper (~15k tokens) and get back an
# Extraction (~5k, issue #124) or two short explanations; aggregate makes one
# small call per axis (four axes); detect's limitation grouping scales with the
# statements, about one per Paper.
_TOKENS_PER_PAPER: dict[str, tuple[int, int]] = {
  "extract": (15_000, 5_000),
  "explain": (15_000, 2_000),
  "aggregate": (8_000, 2_000),
  "detect": (1_000, 500),
}


# Per-gap (input, output) token allowance for a saturation check, which scales
# with the gaps checked, not the Papers: LLM screening of up to ten hits (~800
# tokens in, ~100 out each) plus one query-variant call on zero hits. Priced at
# the default model as an upper bound, though the check runs on the cheap tier.
_TOKENS_PER_GAP: dict[str, tuple[int, int]] = {
  "saturate": (9_500, 1_300),
}


class CostConfigError(ValueError):
  """Raised when `LLM_COST_CONFIRM_ABOVE` isn't a non-negative number."""


class CostEstimate(BaseModel):
  """A stage's estimated cost on this Corpus; `usd` is None when unpriceable."""

  stage: str
  paper_count: int
  model: str
  usd: float | None
  gap_count: int | None = None


class ActualCost(BaseModel):
  """What a stage's last run spent: billed and cached calls, tokens, dollars."""

  calls: int
  cached_calls: int
  input_tokens: int
  output_tokens: int
  usd: float | None


class _ModelUsageRecord(BaseModel):
  model_config = ConfigDict(extra="ignore")

  calls: int = 0
  input_tokens: int = 0
  output_tokens: int = 0


class _UsageRecord(BaseModel):
  model_config = ConfigDict(extra="ignore")

  calls: int = 0
  cached_calls: int = 0
  by_model: dict[str, _ModelUsageRecord] = {}


def uses_llm(stage: str) -> bool:
  """Whether a stage calls the LLM, and so has a cost at all."""
  return stage in _TOKENS_PER_PAPER or stage in _TOKENS_PER_GAP


def cost_confirm_threshold(env: Mapping[str, str] | None = None) -> float:
  """Read the dollar amount above which a run asks for confirmation."""
  env = os.environ if env is None else env
  raw = (env.get(COST_CONFIRM_ENV) or "").strip()
  if not raw:
    return DEFAULT_COST_CONFIRM_ABOVE
  try:
    value = float(raw)
  except ValueError as error:
    raise CostConfigError(_threshold_problem(raw)) from error
  if not math.isfinite(value) or value < 0:
    raise CostConfigError(_threshold_problem(raw))
  return value


def _threshold_problem(raw: str) -> str:
  return (
    f"{COST_CONFIRM_ENV}={raw!r} is not a dollar amount; set it to a number "
    f"such as 1.00 (default {DEFAULT_COST_CONFIRM_ABOVE:.2f})."
  )


def estimate_stage_cost(
  corpus_root: Path, stage: str, env: Mapping[str, str] | None = None
) -> CostEstimate:
  """Estimate a stage's cost on a Corpus as an upper bound with no cache hits."""
  env = os.environ if env is None else env
  model = env.get(MODEL_ENV) or DEFAULT_MODEL
  paper_count = _paper_count(corpus_root)
  if stage in _TOKENS_PER_GAP:
    return estimate_gap_checks_cost(corpus_root, 1, env)
  if not uses_llm(stage):
    return CostEstimate(stage=stage, paper_count=paper_count, model=model, usd=0.0)
  input_tokens, output_tokens = _TOKENS_PER_PAPER[stage]
  return CostEstimate(
    stage=stage,
    paper_count=paper_count,
    model=model,
    usd=_price(model, input_tokens * paper_count, output_tokens * paper_count),
  )


def estimate_gap_checks_cost(
  corpus_root: Path, gap_count: int, env: Mapping[str, str] | None = None
) -> CostEstimate:
  """Estimate checking `gap_count` gaps against the wider literature (ADR 0009)."""
  env = os.environ if env is None else env
  model = env.get(MODEL_ENV) or DEFAULT_MODEL
  input_tokens, output_tokens = _TOKENS_PER_GAP["saturate"]
  return CostEstimate(
    stage="saturate",
    paper_count=_paper_count(corpus_root),
    model=model,
    usd=_price(model, input_tokens * gap_count, output_tokens * gap_count),
    gap_count=gap_count,
  )


def needs_confirmation(estimate: CostEstimate, threshold: float) -> bool:
  """Whether a run with this estimate must be confirmed before it launches."""
  if not uses_llm(estimate.stage):
    return False
  return estimate.usd is None or estimate.usd > threshold


def actual_stage_cost(corpus_root: Path, stage: str) -> ActualCost | None:
  """Return what the stage's last recorded run cost, or None if none is recorded."""
  path = corpus_root / ARTIFACTS_DIR / USAGE_LOG_NAME
  if not path.is_file():
    return None
  raw = json.loads(path.read_text(encoding="utf-8")).get(stage)
  if raw is None:
    return None
  record = _UsageRecord.model_validate(raw)
  prices = [
    _price(model, usage.input_tokens, usage.output_tokens)
    for model, usage in record.by_model.items()
  ]
  return ActualCost(
    calls=record.calls,
    cached_calls=record.cached_calls,
    input_tokens=sum(u.input_tokens for u in record.by_model.values()),
    output_tokens=sum(u.output_tokens for u in record.by_model.values()),
    usd=None if None in prices else sum(p for p in prices if p is not None),
  )


def _price(model: str, input_tokens: int, output_tokens: int) -> float | None:
  matches = [prefix for prefix in PRICES_PER_MILLION if model.startswith(prefix)]
  if not matches:
    return None
  input_price, output_price = PRICES_PER_MILLION[max(matches, key=len)]
  return (input_tokens * input_price + output_tokens * output_price) / 1_000_000


def _paper_count(corpus_root: Path) -> int:
  try:
    return len(load_manifest(corpus_root).papers)
  except ArtifactNotFoundError:
    return 0
