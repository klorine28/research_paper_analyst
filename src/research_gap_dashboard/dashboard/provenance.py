"""
Provenance lines for LLM-generated text the dashboard shows (UI audit I7).

A shown explanation or comparison is only reproducible if the reader can see
what produced it: the model, the prompt version, and whether the cache served
it (CODING_STANDARDS.md > Research integrity: stages are reproducible). Every
value here is read from the artifact that holds the text, or from the usage log
the CLI writes beside it, never from the current configuration, which may have
changed since the text was generated. A field an older artifact lacks is said
to be "not recorded" rather than guessed.

Plain functions with no Streamlit dependency, importing no pipeline stage
(ADR 0002).
"""

from pathlib import Path

from research_gap_dashboard.dashboard import text
from research_gap_dashboard.dashboard.artifacts import (
  ExtractionsArtifact,
  NormalizedFactsArtifact,
  PaperExplanationArtifact,
)
from research_gap_dashboard.dashboard.run_cost import ActualCost, actual_stage_cost


def explanation_provenance(explanation: PaperExplanationArtifact) -> str:
  """Describe the model, prompt version, and cache status behind an explanation."""
  if explanation.cached is None:
    cache = text.PROVENANCE_CACHE_UNRECORDED
  elif explanation.cached:
    cache = text.PROVENANCE_CACHED
  else:
    cache = text.PROVENANCE_FRESH
  details = _details(
    explanation.model, explanation.tier, explanation.prompt_version, cache
  )
  return text.EXPLAINER_PROVENANCE.format(details=details)


def comparison_provenance(
  corpus_root: Path,
  extractions: ExtractionsArtifact,
  normalized: NormalizedFactsArtifact,
) -> list[str]:
  """
  Describe what produced a comparison's statements and categories, line by line.

  The comparison compares Extraction statements and Normalized Facts categories
  and makes no LLM call itself, so its provenance is the extract and aggregate
  stages' records, with each stage's cache status from its last recorded run.
  """
  extract_details = _details(
    extractions.model,
    extractions.tier,
    extractions.prompt_version,
    _stage_cache(actual_stage_cost(corpus_root, "extract")),
  )
  aggregate_details = _details(
    normalized.model,
    normalized.tier,
    normalized.prompt_version,
    _stage_cache(actual_stage_cost(corpus_root, "aggregate")),
  )
  return [
    text.COMPARISON_PROVENANCE_EXTRACT.format(details=extract_details),
    text.COMPARISON_PROVENANCE_AGGREGATE.format(details=aggregate_details),
    text.COMPARISON_PROVENANCE_NOTE,
  ]


def _stage_cache(usage: ActualCost | None) -> str:
  """Summarize a stage's last run as how many completions the cache served."""
  if usage is None:
    return text.PROVENANCE_CACHE_UNRECORDED
  return text.PROVENANCE_STAGE_CACHE.format(
    cached=usage.cached_calls, total=usage.calls + usage.cached_calls
  )


def _details(model: str, tier: str, prompt_version: str, cache: str) -> str:
  """Join model, prompt version, and cache status into one provenance clause."""
  if model and tier:
    who = text.PROVENANCE_MODEL.format(model=model, tier=tier)
  elif model:
    who = model
  elif tier:
    who = text.PROVENANCE_MODEL_UNRECORDED.format(tier=tier)
  else:
    who = text.PROVENANCE_MODEL_AND_TIER_UNRECORDED
  prompt = (
    text.PROVENANCE_PROMPT.format(version=prompt_version)
    if prompt_version
    else text.PROVENANCE_PROMPT_UNRECORDED
  )
  return text.PROVENANCE_SEPARATOR.join([who, prompt, cache])
