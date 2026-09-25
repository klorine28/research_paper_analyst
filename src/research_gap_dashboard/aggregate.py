"""
The aggregate stage: normalize each Paper's free-text facts onto the taxonomy.

Extraction leaves every fact in the Paper's own words ("randomised trial",
"broken heart syndrome"). The Coverage Matrix can only compare Papers once those
phrases collapse onto a shared controlled vocabulary, so this stage maps each
Extraction's facts onto Topics in the loaded Taxonomy via the LLM client and
writes the NormalizedFacts artifact.

Every normalization decision is inspectable: each assignment records the
original phrase and the assigned category, and any phrase the model cannot place
on the taxonomy is surfaced as an unmapped term (for taxonomy editing) instead
of being silently dropped (CODING_STANDARDS > Research integrity).

The model only ever proposes category ids; this stage validates every proposed
id against the loaded Taxonomy, so a hallucinated category becomes an unmapped
term rather than an invented one. LLM calls go through the cached client
(ADR 0001), so reruns are free and repeatable.
"""

import logging
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

from research_gap_dashboard.corpus_layout import inspect_corpus_layout
from research_gap_dashboard.extract import (
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
  CARDIOLOGY_SEED_PATH,
  Taxonomy,
  load_taxonomy,
)

logger = logging.getLogger(__name__)

NORMALIZED_FACTS_NAME = "normalized_facts.json"

# Bump when the aggregate prompt or output shape changes: it keys the LLM cache
# (ADR 0001), so a bump reruns aggregation instead of serving stale answers.
PROMPT_VERSION = "aggregate-v1"

# Forced structured-output calls occasionally misfire (an empty object, or the
# schema echoed back). Retry a few times, bypassing the cache, before accepting
# a Paper that placed no phrase on the taxonomy and surfaced none as unmapped.
AGGREGATE_ATTEMPTS = 3


class TopicAssignment(BaseModel):
  """One Paper phrase placed onto a Taxonomy Topic, kept for audit."""

  original_term: str = Field(description="The phrase as written in the Paper.")
  category_id: str = Field(description="The assigned Topic's id in the Taxonomy.")
  category_label: str = Field(description="The assigned Topic's human-readable label.")


class UnmappedTerm(BaseModel):
  """A Paper phrase that fit no Taxonomy Topic, surfaced for taxonomy editing."""

  original_term: str = Field(description="The phrase as written in the Paper.")
  reason: str = Field(description="Why the phrase could not be placed on the taxonomy.")


class NormalizedFacts(BaseModel):
  """One Paper's phrases mapped onto the Taxonomy, with the misses kept."""

  citation_key: str
  assignments: list[TopicAssignment]
  unmapped: list[UnmappedTerm]


class AggregateFailure(BaseModel):
  """A Paper whose normalization was rejected, and why."""

  citation_key: str
  reason: str


class AggregateReport(BaseModel):
  """What the aggregate stage produced: the NormalizedFacts and provenance."""

  corpus_root: Path
  prompt_version: str
  tier: ModelTier
  taxonomy_domain: str
  taxonomy_source: str
  normalized: list[NormalizedFacts]
  failures: list[AggregateFailure]


class _ProposedAssignment(BaseModel):
  """One mapping the model proposes, before its id is checked against the taxonomy."""

  original_term: str = Field(description="A phrase copied verbatim from the Paper.")
  category_id: str = Field(description="The id of the Taxonomy category it maps onto.")


class _ProposedNormalization(BaseModel):
  """The model's raw proposal for one Paper, before taxonomy validation."""

  assignments: list[_ProposedAssignment] = Field(
    default_factory=list,
    description="Phrases from the Paper, each mapped onto a Taxonomy category id.",
  )
  unmapped: list[UnmappedTerm] = Field(
    default_factory=list,
    description="Phrases about a subject with no matching Taxonomy category.",
  )


def aggregate_corpus(
  root: Path,
  client: LlmClient,
  taxonomy: Taxonomy,
  *,
  prompt_version: str = PROMPT_VERSION,
  tier: ModelTier = "default",
) -> AggregateReport:
  """
  Normalize every Paper's Extraction onto the Taxonomy and write the artifact.

  Each Paper is normalized independently: a Paper whose Extraction is missing or
  whose normalization misfires past its retries is recorded in the report's
  failures and the run carries on with the rest of the Corpus.
  """
  extractions = read_extractions(root).extractions
  normalized: list[NormalizedFacts] = []
  failures: list[AggregateFailure] = []

  for extraction in extractions:
    try:
      facts = aggregate_paper(
        extraction, taxonomy, client, prompt_version=prompt_version, tier=tier
      )
    except (ValidationError, LlmResponseError) as error:
      logger.error("Rejected normalization for %s: %s", extraction.citation_key, error)
      failures.append(
        AggregateFailure(citation_key=extraction.citation_key, reason=str(error))
      )
      continue
    normalized.append(facts)

  report = AggregateReport(
    corpus_root=root,
    prompt_version=prompt_version,
    tier=tier,
    taxonomy_domain=taxonomy.meta.domain,
    taxonomy_source=taxonomy.meta.source,
    normalized=normalized,
    failures=failures,
  )
  _write_report(root, report)
  logger.info(
    "Normalized %d of %d Papers onto %d Topics (%d failed).",
    len(normalized),
    len(extractions),
    len(taxonomy.topics),
    len(failures),
  )
  return report


def aggregate_paper(
  extraction: Extraction,
  taxonomy: Taxonomy,
  client: LlmClient,
  *,
  prompt_version: str = PROMPT_VERSION,
  tier: ModelTier = "default",
) -> NormalizedFacts:
  """Map one Paper's facts onto the Taxonomy, validating every proposed category."""
  proposal = complete_model(
    client,
    _build_prompt(taxonomy, extraction.fields),
    _ProposedNormalization,
    prompt_version=prompt_version,
    tier=tier,
    attempts=AGGREGATE_ATTEMPTS,
    accept=_has_any_decision,
  )
  return _validate_against_taxonomy(extraction.citation_key, taxonomy, proposal)


def _has_any_decision(proposal: _ProposedNormalization) -> bool:
  """Report whether a proposal placed or explicitly surfaced at least one phrase."""
  return bool(proposal.assignments or proposal.unmapped)


def _validate_against_taxonomy(
  citation_key: str, taxonomy: Taxonomy, proposal: _ProposedNormalization
) -> NormalizedFacts:
  """Keep only assignments to real Topics; surface every other phrase as unmapped."""
  assignments: list[TopicAssignment] = []
  unmapped: list[UnmappedTerm] = list(proposal.unmapped)

  for proposed in proposal.assignments:
    topic = taxonomy.topic_by_id(proposed.category_id) or taxonomy.resolve(
      proposed.category_id
    )
    if topic is None:
      # A proposed id absent from the taxonomy is a hallucinated category; keep
      # the phrase for taxonomy editing rather than inventing a Topic for it.
      unmapped.append(
        UnmappedTerm(
          original_term=proposed.original_term,
          reason=(
            f"model proposed category '{proposed.category_id}', "
            "which is not in the taxonomy"
          ),
        )
      )
      continue
    assignments.append(
      TopicAssignment(
        original_term=proposed.original_term,
        category_id=topic.id,
        category_label=topic.label,
      )
    )

  return NormalizedFacts(
    citation_key=citation_key, assignments=assignments, unmapped=unmapped
  )


def read_normalized_facts(root: Path) -> AggregateReport:
  """Read the NormalizedFacts artifact a previous aggregate run wrote."""
  path = inspect_corpus_layout(root).layout.artifacts_dir / NORMALIZED_FACTS_NAME
  return AggregateReport.model_validate_json(path.read_text(encoding="utf-8"))


def load_pipeline_taxonomy(path: Path = CARDIOLOGY_SEED_PATH) -> Taxonomy:
  """Load the Taxonomy the aggregate stage normalizes onto (the seed by default)."""
  return load_taxonomy(path)


def _build_prompt(taxonomy: Taxonomy, fields: ExtractionFields) -> str:
  """Build the mapping prompt from the Taxonomy's Topics and a Paper's facts."""
  return (
    "You normalize one research paper's facts onto a controlled vocabulary of "
    "topic categories. Only use the categories listed below; never invent a "
    "category or an id that is not in the list.\n"
    "For every subject the paper studies, add an assignment whose original_term "
    "is a short phrase copied from the paper's facts and whose category_id is the "
    "id of the single best-matching category. If the paper studies a subject that "
    "no category covers, put that phrase in unmapped with a short reason instead "
    "of forcing it onto a category.\n\n"
    f"CATEGORIES:\n{_render_categories(taxonomy)}\n\n"
    f"PAPER FACTS:\n{_render_facts(fields)}"
  )


def _render_categories(taxonomy: Taxonomy) -> str:
  """List each Topic's id, label, and aliases for the model to match against."""
  lines: list[str] = []
  for topic in taxonomy.topics:
    aliases = f" (aliases: {', '.join(topic.aliases)})" if topic.aliases else ""
    lines.append(f"- id={topic.id} | {topic.label}{aliases}")
  return "\n".join(lines)


def _render_facts(fields: ExtractionFields) -> str:
  """Render a Paper's extracted statements, grouped by field, for the prompt."""
  lines: list[str] = []
  # Iterate the seven fields in declaration order; owning the order here would
  # duplicate extract's tuple, so borrow the model's own field order instead.
  for name in ExtractionFields.model_fields:  # pylint: disable=not-an-iterable
    statements = [fact.statement for fact in getattr(fields, name)]
    if statements:
      lines.append(f"{name}:")
      lines.extend(f"  - {statement}" for statement in statements)
  return "\n".join(lines)


def _write_report(root: Path, report: AggregateReport) -> None:
  """Write the NormalizedFacts artifact under artifacts/."""
  artifacts_dir = inspect_corpus_layout(root).layout.artifacts_dir
  artifacts_dir.mkdir(parents=True, exist_ok=True)
  path = artifacts_dir / NORMALIZED_FACTS_NAME
  path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
