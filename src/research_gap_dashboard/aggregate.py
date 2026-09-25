"""
The aggregate stage: normalize each Paper's free-text facts onto the taxonomies.

Extraction leaves every fact in the Paper's own words ("randomised trial",
"broken heart syndrome"). The Coverage Matrix can only compare Papers once those
phrases collapse onto shared controlled vocabularies, so this stage maps each
Extraction onto every loaded Taxonomy, one per Coverage Matrix axis (Topic,
Method, Population, Dataset), via the LLM client, and writes the NormalizedFacts
artifact.

Every normalization decision is inspectable: each assignment records its axis,
the original phrase, the assigned category, and the extracted fact it came from
together with that fact's Evidence, so a later stage can cite the passage behind
any matrix cell. Any phrase the model cannot place on a taxonomy is surfaced as
an unmapped term (for taxonomy editing) instead of being silently dropped
(CODING_STANDARDS > Research integrity).

The model only ever proposes category ids and fact references; this stage
validates both against the loaded Taxonomy and the Paper's Extraction, so a
hallucinated category or fact becomes an unmapped term rather than an invented
one. LLM calls go through the cached client (ADR 0001), so reruns are free and
repeatable.
"""

import logging
from collections.abc import Iterable, Sequence
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

from research_gap_dashboard.corpus_layout import inspect_corpus_layout
from research_gap_dashboard.extract import (
  Evidence,
  ExtractedFact,
  Extraction,
  ExtractionFields,
  read_extractions,
)
from research_gap_dashboard.llm import (
  LlmClient,
  LlmResponseError,
  ModelTier,
  complete_model,
)
from research_gap_dashboard.taxonomy import (
  AXES,
  SEED_PATHS,
  Axis,
  Taxonomy,
  TaxonomyError,
  load_taxonomy,
)

logger = logging.getLogger(__name__)

NORMALIZED_FACTS_NAME = "normalized_facts.json"

# Bump when the aggregate prompt or output shape changes: it keys the LLM cache
# (ADR 0001), so a bump reruns aggregation instead of serving stale answers.
PROMPT_VERSION = "aggregate-v2"

# Forced structured-output calls occasionally misfire (an empty object, or the
# schema echoed back). Retry a few times, bypassing the cache, before accepting
# an axis that placed no phrase on the taxonomy and surfaced none as unmapped.
AGGREGATE_ATTEMPTS = 3

# Which Extraction fields feed each axis. Topics are what a Paper is about, which
# any field may say; the other axes each have one dedicated Extraction field.
AXIS_FIELDS: dict[Axis, tuple[str, ...]] = {
  "topic": tuple(ExtractionFields.model_fields),
  "method": ("methods",),
  "population": ("populations",),
  "dataset": ("datasets",),
}

# What the model is asked to place on each axis's categories.
_AXIS_SUBJECTS: dict[Axis, str] = {
  "topic": "every disease, condition, or subject the paper studies",
  "method": "every study design or research method the paper uses",
  "population": (
    "every population or sample the paper studies (age group, sex, species, "
    "care setting); one phrase may map onto several categories"
  ),
  "dataset": "every dataset or data source the paper uses",
}


class AggregateError(Exception):
  """Raised when one axis of a Paper's normalization misfires past its retries."""

  def __init__(self, axis: Axis, reason: str) -> None:
    self.axis: Axis = axis
    self.reason: str = reason
    super().__init__(f"{axis} axis: {reason}")


class CategoryAssignment(BaseModel):
  """One Paper phrase placed onto a category of one axis, with its Evidence."""

  axis: Axis = Field(description="The Coverage Matrix axis the category belongs to.")
  original_term: str = Field(description="The phrase as written in the Paper.")
  category_id: str = Field(description="The assigned category's id in its Taxonomy.")
  category_label: str = Field(description="The assigned category's readable label.")
  fact_ref: str = Field(
    description="The Extraction fact the phrase came from, e.g. 'methods[0]'."
  )
  evidence: Evidence = Field(description="The Evidence behind that Extraction fact.")


class UnmappedTerm(BaseModel):
  """A Paper phrase that fit no category on one axis, surfaced for editing."""

  axis: Axis = Field(description="The axis whose taxonomy had no fitting category.")
  original_term: str = Field(description="The phrase as written in the Paper.")
  reason: str = Field(description="Why the phrase could not be placed on the taxonomy.")


class NormalizedFacts(BaseModel):
  """One Paper's phrases mapped onto every axis's Taxonomy, with the misses kept."""

  citation_key: str
  assignments: list[CategoryAssignment]
  unmapped: list[UnmappedTerm]

  def category_ids(self, axis: Axis) -> list[str]:
    """Return the distinct category ids this Paper was placed on for one axis."""
    return sorted({a.category_id for a in self.assignments if a.axis == axis})


class AggregateFailure(BaseModel):
  """A Paper whose normalization was rejected, and on which axis."""

  citation_key: str
  axis: Axis
  reason: str


class TaxonomyProvenance(BaseModel):
  """Which vocabulary an axis was normalized onto, recorded with the artifact."""

  axis: Axis
  domain: str
  source: str
  category_count: int


class AggregateReport(BaseModel):
  """What the aggregate stage produced: the NormalizedFacts and provenance."""

  corpus_root: Path
  prompt_version: str
  tier: ModelTier
  taxonomies: list[TaxonomyProvenance]
  normalized: list[NormalizedFacts]
  failures: list[AggregateFailure]


class _ProposedAssignment(BaseModel):
  """One mapping the model proposes, before it is checked against taxonomy and facts."""

  original_term: str = Field(description="A short phrase copied from the fact.")
  fact_ref: str = Field(
    description="The bracketed reference of the fact the phrase came from."
  )
  category_id: str = Field(description="The id of the category it maps onto.")


class _ProposedUnmapped(BaseModel):
  """A phrase the model proposes to surface as fitting no category."""

  original_term: str = Field(description="A short phrase copied from the fact.")
  reason: str = Field(description="Why no listed category fits the phrase.")


class _ProposedNormalization(BaseModel):
  """The model's raw proposal for one Paper and axis, before validation."""

  assignments: list[_ProposedAssignment] = Field(
    default_factory=list,
    description="Phrases from the facts, each mapped onto a category id.",
  )
  unmapped: list[_ProposedUnmapped] = Field(
    default_factory=list,
    description="Phrases about a subject with no matching category.",
  )


def aggregate_corpus(
  root: Path,
  client: LlmClient,
  taxonomies: Sequence[Taxonomy],
  *,
  prompt_version: str = PROMPT_VERSION,
  tier: ModelTier = "default",
) -> AggregateReport:
  """
  Normalize every Paper's Extraction onto each Taxonomy and write the artifact.

  Each Paper is normalized independently: a Paper whose normalization misfires
  past its retries on any axis is recorded in the report's failures (so no
  Paper enters the Coverage Matrix half-normalized) and the run carries on.
  """
  check_distinct_axes(taxonomies)
  extractions = read_extractions(root).extractions
  normalized: list[NormalizedFacts] = []
  failures: list[AggregateFailure] = []

  for extraction in extractions:
    try:
      facts = aggregate_paper(
        extraction, taxonomies, client, prompt_version=prompt_version, tier=tier
      )
    except AggregateError as error:
      logger.error("Rejected normalization for %s: %s", extraction.citation_key, error)
      failures.append(
        AggregateFailure(
          citation_key=extraction.citation_key, axis=error.axis, reason=error.reason
        )
      )
      continue
    normalized.append(facts)

  report = AggregateReport(
    corpus_root=root,
    prompt_version=prompt_version,
    tier=tier,
    taxonomies=[
      TaxonomyProvenance(
        axis=taxonomy.meta.axis,
        domain=taxonomy.meta.domain,
        source=taxonomy.meta.source,
        category_count=len(taxonomy.topics),
      )
      for taxonomy in _in_axis_order(taxonomies)
    ],
    normalized=normalized,
    failures=failures,
  )
  _write_report(root, report)
  logger.info(
    "Normalized %d of %d Papers onto %d axes (%d failed).",
    len(normalized),
    len(extractions),
    len(taxonomies),
    len(failures),
  )
  return report


def aggregate_paper(
  extraction: Extraction,
  taxonomies: Sequence[Taxonomy],
  client: LlmClient,
  *,
  prompt_version: str = PROMPT_VERSION,
  tier: ModelTier = "default",
) -> NormalizedFacts:
  """
  Map one Paper's facts onto each Taxonomy, validating every proposal.

  An axis whose Extraction fields are all empty makes no LLM call: there is
  nothing to place, so it contributes no assignments and no unmapped terms.
  """
  check_distinct_axes(taxonomies)
  assignments: list[CategoryAssignment] = []
  unmapped: list[UnmappedTerm] = []

  for taxonomy in _in_axis_order(taxonomies):
    axis = taxonomy.meta.axis
    facts = _axis_facts(extraction.fields, axis)
    if not facts:
      continue
    try:
      proposal = complete_model(
        client,
        _build_prompt(taxonomy, facts),
        _ProposedNormalization,
        prompt_version=prompt_version,
        tier=tier,
        attempts=AGGREGATE_ATTEMPTS,
        accept=_has_any_decision,
      )
    except (ValidationError, LlmResponseError) as error:
      raise AggregateError(axis, str(error)) from error
    axis_assignments, axis_unmapped = _validate_proposal(taxonomy, facts, proposal)
    assignments.extend(axis_assignments)
    unmapped.extend(axis_unmapped)

  return NormalizedFacts(
    citation_key=extraction.citation_key, assignments=assignments, unmapped=unmapped
  )


def check_distinct_axes(taxonomies: Iterable[Taxonomy]) -> None:
  """Raise TaxonomyError unless there is at most one Taxonomy per axis."""
  seen: set[Axis] = set()
  problems: list[str] = []
  for taxonomy in taxonomies:
    axis = taxonomy.meta.axis
    if axis in seen:
      problems.append(f"More than one taxonomy covers the '{axis}' axis.")
    seen.add(axis)
  if problems:
    raise TaxonomyError(problems)


def _in_axis_order(taxonomies: Iterable[Taxonomy]) -> list[Taxonomy]:
  """Order taxonomies Topic, Method, Population, Dataset for a stable artifact."""
  return sorted(taxonomies, key=lambda taxonomy: AXES.index(taxonomy.meta.axis))


def _axis_facts(fields: ExtractionFields, axis: Axis) -> dict[str, ExtractedFact]:
  """Index the Extraction facts that feed one axis by their reference."""
  facts: dict[str, ExtractedFact] = {}
  for name in AXIS_FIELDS[axis]:
    for index, fact in enumerate(getattr(fields, name)):
      facts[f"{name}[{index}]"] = fact
  return facts


def _has_any_decision(proposal: _ProposedNormalization) -> bool:
  """Report whether a proposal placed or explicitly surfaced at least one phrase."""
  return bool(proposal.assignments or proposal.unmapped)


def _validate_proposal(
  taxonomy: Taxonomy,
  facts: dict[str, ExtractedFact],
  proposal: _ProposedNormalization,
) -> tuple[list[CategoryAssignment], list[UnmappedTerm]]:
  """Keep only assignments to real categories and facts; surface everything else."""
  axis = taxonomy.meta.axis
  assignments: list[CategoryAssignment] = []
  unmapped = [
    UnmappedTerm(axis=axis, original_term=u.original_term, reason=u.reason)
    for u in proposal.unmapped
  ]

  for proposed in proposal.assignments:
    fact = facts.get(proposed.fact_ref)
    category = taxonomy.topic_by_id(proposed.category_id) or taxonomy.resolve(
      proposed.category_id
    )
    # A category or fact absent from the inputs is a hallucination; keep the
    # phrase for review rather than inventing a category or an Evidence link.
    if fact is None:
      reason = (
        f"model cited fact '{proposed.fact_ref}', which is not one of this "
        f"Paper's {axis} facts"
      )
    elif category is None:
      reason = (
        f"model proposed category '{proposed.category_id}', "
        "which is not in the taxonomy"
      )
    else:
      assignments.append(
        CategoryAssignment(
          axis=axis,
          original_term=proposed.original_term,
          category_id=category.id,
          category_label=category.label,
          fact_ref=proposed.fact_ref,
          evidence=fact.evidence,
        )
      )
      continue
    unmapped.append(
      UnmappedTerm(axis=axis, original_term=proposed.original_term, reason=reason)
    )

  return assignments, unmapped


def read_normalized_facts(root: Path) -> AggregateReport:
  """Read the NormalizedFacts artifact a previous aggregate run wrote."""
  path = inspect_corpus_layout(root).layout.artifacts_dir / NORMALIZED_FACTS_NAME
  return AggregateReport.model_validate_json(path.read_text(encoding="utf-8"))


def load_pipeline_taxonomies(
  paths: Iterable[Path] = SEED_PATHS.values(),
) -> list[Taxonomy]:
  """
  Load the Taxonomies the aggregate stage normalizes onto (the seeds by default).

  Every file's problems are collected, prefixed with its path, and raised
  together, as are two files claiming the same axis.
  """
  taxonomies: list[Taxonomy] = []
  problems: list[str] = []
  for path in paths:
    try:
      taxonomies.append(load_taxonomy(path))
    except TaxonomyError as error:
      problems.extend(f"{path}: {problem}" for problem in error.problems)
  try:
    check_distinct_axes(taxonomies)
  except TaxonomyError as error:
    problems.extend(error.problems)
  if problems:
    raise TaxonomyError(problems)
  return taxonomies


def _build_prompt(taxonomy: Taxonomy, facts: dict[str, ExtractedFact]) -> str:
  """Build one axis's mapping prompt from its categories and the Paper's facts."""
  axis = taxonomy.meta.axis
  return (
    f"You normalize one research paper's facts onto a controlled vocabulary of "
    f"{axis} categories. Only use the categories listed below; never invent a "
    "category or an id that is not in the list.\n"
    f"For {_AXIS_SUBJECTS[axis]}, add an assignment whose original_term is a "
    "short phrase copied from one fact, whose fact_ref is that fact's bracketed "
    "reference exactly as shown, and whose category_id is the id of the "
    "best-matching category. If a fact names a subject that no category covers, "
    "put that phrase in unmapped with a short reason instead of forcing it onto "
    "a category.\n\n"
    f"CATEGORIES:\n{_render_categories(taxonomy)}\n\n"
    f"PAPER FACTS:\n{_render_facts(facts)}"
  )


def _render_categories(taxonomy: Taxonomy) -> str:
  """List each category's id, label, and aliases for the model to match against."""
  lines: list[str] = []
  for topic in taxonomy.topics:
    aliases = f" (aliases: {', '.join(topic.aliases)})" if topic.aliases else ""
    lines.append(f"- id={topic.id} | {topic.label}{aliases}")
  return "\n".join(lines)


def _render_facts(facts: dict[str, ExtractedFact]) -> str:
  """Render the facts one per line, each behind the reference the model cites."""
  return "\n".join(f"- [{ref}] {fact.statement}" for ref, fact in facts.items())


def _write_report(root: Path, report: AggregateReport) -> None:
  """Write the NormalizedFacts artifact under artifacts/."""
  artifacts_dir = inspect_corpus_layout(root).layout.artifacts_dir
  artifacts_dir.mkdir(parents=True, exist_ok=True)
  path = artifacts_dir / NORMALIZED_FACTS_NAME
  path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
