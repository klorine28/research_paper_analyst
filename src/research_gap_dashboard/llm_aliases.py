"""
LLM alias suggestions for thin categories, the last alias source of ADR 0010.

MeSH and the Corpus's own unmapped terms widen most of the vocabulary, but a
category with almost no aliases (a hand-added Method, a narrow descriptor with
no entry terms) stays easy to miss. For those **thin** categories only, a model
is asked once for the other names the literature uses.

A category is thin when its shared aliases plus the Corpus's approved overlay
aliases number fewer than `MIN_ALIASES`; a proposal still awaiting a decision
does not count, since it maps nothing. A category that already has LLM
suggestions logged in the overlay is never asked again, whatever was decided
on them, and every call goes through the cached LLM client, so a re-run is free.

Nothing here maps a phrase. Each suggestion is returned for `propose_aliases`
to log as *proposed*, with the model and prompt version as provenance; only a
human approval (`approve_alias`) makes it apply, and only to that Corpus.
"""

import logging
from dataclasses import dataclass

from pydantic import BaseModel, Field

from research_gap_dashboard.llm import LlmClient, ModelTier, complete_model
from research_gap_dashboard.taxonomy import Taxonomy, Topic, normalize_phrase
from research_gap_dashboard.term_promotion import noise_class
from research_gap_dashboard.vocabulary import (
  AliasOverlay,
  AliasProvenance,
  AliasSuggestion,
  apply_alias_overlay,
)

logger = logging.getLogger(__name__)

ALIAS_PROMPT_VERSION = "thin-category-aliases-v1"

# A category with fewer aliases than this (shared plus approved) is thin.
MIN_ALIASES = 2

# The most aliases one call may return; more is noise to review, not vocabulary.
MAX_SUGGESTIONS = 8

_PROMPT = """\
You help widen a controlled vocabulary used to map phrases from research \
papers onto categories. The vocabulary covers the {axis} axis of the field \
"{domain}".

Category: "{label}"
{context}
List up to {limit} other names researchers in this field use in papers for \
exactly this category: synonyms, spelling variants, and established \
abbreviations. Do not list broader or narrower concepts, related conditions, \
or phrases that would also fit another category. Return an empty list if you \
know of none.
"""


@dataclass(frozen=True)
class AliasSuggestionModel:
  """The LLM thin categories are sent to, and the model id to record with it."""

  client: LlmClient
  model: str
  tier: ModelTier = "cheap"


class _ProposedAliases(BaseModel):
  """The model's other names for one category."""

  aliases: list[str] = Field(
    default_factory=list, description="Other names for exactly this category."
  )


def thin_categories(taxonomy: Taxonomy, overlay: AliasOverlay) -> list[Topic]:
  """
  Return the categories with fewer than `MIN_ALIASES` aliases, in file order.

  Counted on the taxonomy with the Corpus's approved overlay aliases applied.
  """
  [applied] = apply_alias_overlay([taxonomy], overlay)
  return [topic for topic in applied.topics if len(topic.aliases) < MIN_ALIASES]


def suggest_llm_aliases(
  taxonomy: Taxonomy, overlay: AliasOverlay, llm: AliasSuggestionModel
) -> list[AliasSuggestion]:
  """
  Ask the LLM for aliases of each thin category not yet asked, and filter them.

  One call per such category. A phrase the taxonomy (with the Corpus's approved
  aliases) already resolves, one offered to two categories at once, or one the
  unmapped-term stoplist rejects is dropped, so a suggestion can never make the
  vocabulary ambiguous or noisy.
  """
  axis = taxonomy.meta.axis
  asked = {
    entry.category_id
    for entry in overlay.entries
    if entry.axis == axis and entry.provenance.source == "llm_suggestion"
  }
  [applied] = apply_alias_overlay([taxonomy], overlay)
  to_ask = [t for t in thin_categories(taxonomy, overlay) if t.id not in asked]
  raw = [suggestion for topic in to_ask for suggestion in _ask(applied, topic, llm)]
  kept = _unambiguous(applied, raw)
  logger.info(
    "%s taxonomy: %d thin categories asked, %d aliases suggested.",
    axis,
    len(to_ask),
    len(kept),
  )
  return kept


def _ask(
  taxonomy: Taxonomy, topic: Topic, llm: AliasSuggestionModel
) -> list[AliasSuggestion]:
  """Ask for one category's other names; keep those past the stoplist."""
  answer = complete_model(
    llm.client,
    _prompt(taxonomy, topic),
    _ProposedAliases,
    prompt_version=f"{ALIAS_PROMPT_VERSION}@{llm.model}",
    tier=llm.tier,
  )
  provenance = AliasProvenance(
    source="llm_suggestion", model=llm.model, prompt_version=ALIAS_PROMPT_VERSION
  )
  phrases = (" ".join(alias.split()) for alias in answer.aliases[:MAX_SUGGESTIONS])
  return [
    AliasSuggestion(
      axis=taxonomy.meta.axis,
      category_id=topic.id,
      alias=phrase,
      provenance=provenance,
    )
    for phrase in phrases
    if phrase and noise_class(phrase) is None
  ]


def _unambiguous(
  taxonomy: Taxonomy, raw: list[AliasSuggestion]
) -> list[AliasSuggestion]:
  """Drop repeats, phrases the taxonomy resolves, and phrases two categories got."""
  claimants: dict[str, set[str]] = {}
  for suggestion in raw:
    claimants.setdefault(normalize_phrase(suggestion.alias), set()).add(
      suggestion.category_id
    )
  kept: list[AliasSuggestion] = []
  seen: set[tuple[str, str]] = set()
  for suggestion in raw:
    key = normalize_phrase(suggestion.alias)
    if (suggestion.category_id, key) in seen or len(claimants[key]) > 1:
      continue
    if taxonomy.resolve(suggestion.alias) is not None:
      continue
    seen.add((suggestion.category_id, key))
    kept.append(suggestion)
  return kept


def _prompt(taxonomy: Taxonomy, topic: Topic) -> str:
  """Build the prompt for one category, with what the vocabulary knows of it."""
  context: list[str] = []
  if topic.description:
    context.append(f"Scope note: {topic.description}")
  if topic.parent:
    parent = taxonomy.topic_by_id(topic.parent)
    if parent is not None:
      context.append(f'Broader category: "{parent.label}"')
  if topic.mesh_id:
    context.append(f"MeSH descriptor: {topic.mesh_id}")
  if topic.aliases:
    context.append("Known names: " + ", ".join(f'"{a}"' for a in topic.aliases))
  return _PROMPT.format(
    axis=taxonomy.meta.axis,
    domain=taxonomy.meta.domain,
    label=topic.label,
    context="\n".join(context) + ("\n" if context else ""),
    limit=MAX_SUGGESTIONS,
  )
