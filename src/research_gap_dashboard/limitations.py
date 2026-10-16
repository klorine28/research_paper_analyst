"""
Limitation grouping and follow-up matching for Unanswered Limitation detection.

Papers state their own limitations and future work in their own words, so "the
same limitation" only emerges once similar statements are grouped across
Papers. This module does the two semantic steps the LLM is needed for, and
nothing else (card wording lives in `detect`):

1. **Group** every limitation and future-work statement in the Corpus by
   meaning, in one call over all statements. When an `Embedder` is available,
   `merge_hints` first lists cross-Paper statement pairs whose vectors are
   close, and the call receives them as hints; the model still decides, so
   without the `embeddings` extra grouping simply runs LLM-only.
2. **Match follow-ups**: for each group, ask which facts of the *later* Papers
   (published in a later year than the group's earliest source Paper) address
   it, in one call per group. A group with no later Paper makes no call.
3. **Tag** every group, in one batched cheap-tier call, with one limitation
   type (`LimitationBucket`) and the Corpus's observed categories it concerns
   (`tag_limitations`), so detect can intersect groups with cells
   deterministically (ADR 0008, two witnesses).

The model only ever proposes statement, fact, group, and category ids; each is
validated against the Extractions or the observed categories, so a
hallucinated id cannot create a group member, a follow-up, or a tag. Every
correction (an unknown id dropped, a statement the model left out given its
own group) is recorded in the group's notes, so the grouping decisions are
inspectable in the artifact. Calls go through the cached
client (ADR 0001), so a rerun on the same Extractions gives the same groups.
"""

import logging
import math
import operator
import re
from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel, Field

from research_gap_dashboard.embeddings import Embedder, Vector
from research_gap_dashboard.extract import (
  Evidence,
  ExtractedFact,
  Extraction,
  ExtractionFields,
)
from research_gap_dashboard.llm import LlmClient, ModelTier, complete_model
from research_gap_dashboard.taxonomy import Axis

logger = logging.getLogger(__name__)

# Bump when a prompt or output shape changes: they key the LLM cache (ADR 0001).
GROUP_PROMPT_VERSION = "limitations-group-v2"
FOLLOW_UP_PROMPT_VERSION = "limitations-follow-up-v1"
TAG_PROMPT_VERSION = "limitations-tag-v1"

ATTEMPTS = 3

StatementKind = Literal["limitation", "future_work"]

# The kinds of limitation a group can be (ADR 0008; adapted from Lan et al.
# 2024 in `docs/research/gap-detection-layers.md` §8).
LimitationBucket = Literal[
  "study_design",
  "population",
  "setting_data",
  "sample_size",
  "measurement",
  "follow_up",
  "analysis",
  "open_question",
  "other",
]

_KIND_FIELDS: dict[StatementKind, str] = {
  "limitation": "limitations",
  "future_work": "future_work",
}

# The Extraction fields that show what a later Paper did, and so whether it
# addressed an earlier limitation: every field except the self-critical ones.
FOLLOW_UP_FIELDS: tuple[str, ...] = tuple(
  name
  for name in ExtractionFields.model_fields  # pylint: disable=not-an-iterable
  if name not in _KIND_FIELDS.values()
)


class LimitationStatement(BaseModel):
  """One limitation or future-work statement a Paper makes about itself."""

  statement_id: str = Field(description="'<citation key>:<field>[<index>]'.")
  citation_key: str
  kind: StatementKind
  statement: str
  evidence: Evidence


class FollowUp(BaseModel):
  """A later Paper's fact that addresses a limitation group, and why."""

  citation_key: str
  fact_id: str = Field(description="'<citation key>:<field>[<index>]'.")
  statement: str
  evidence: Evidence
  reason: str


class TaggedCategory(BaseModel):
  """One observed category a limitation group concerns."""

  axis: Axis
  category_id: str


class LimitationGroup(BaseModel):
  """Semantically similar statements across Papers, and who followed them up."""

  group_id: str
  label: str
  statements: list[LimitationStatement] = Field(min_length=1)
  earliest_year: int | None = Field(
    description="Publication year of the earliest source Paper, if known."
  )
  later_citation_keys: list[str] = Field(
    description="Later Papers checked for a follow-up (the denominator)."
  )
  follow_ups: list[FollowUp]
  notes: list[str] = Field(
    default_factory=list, description="Corrections made to the model's proposal."
  )
  bucket: LimitationBucket | None = Field(
    default=None, description="The limitation type; None if untagged or older."
  )
  categories: list[TaggedCategory] = Field(
    default_factory=list, description="Observed categories the group concerns."
  )

  @property
  def source_citation_keys(self) -> list[str]:
    """Return the distinct Papers whose statements form this group."""
    return sorted({statement.citation_key for statement in self.statements})

  @property
  def addressed(self) -> bool:
    """Report whether any later Paper addressed this group."""
    return bool(self.follow_ups)


class MergeHint(BaseModel):
  """Two statements from different Papers whose embeddings are close."""

  statement_ids: tuple[str, str] = Field(description="Sorted statement ids.")
  cosine: float


class _ProposedGroup(BaseModel):
  """One group the model proposes, before its ids are validated."""

  label: str = Field(description="A short name for the shared limitation.")
  statement_ids: list[str] = Field(
    description="The bracketed ids of the statements in this group."
  )


class _ProposedGrouping(BaseModel):
  """The model's grouping of all statements."""

  groups: list[_ProposedGroup] = Field(default_factory=list)


class _ProposedFollowUp(BaseModel):
  """One fact the model proposes addresses the limitation group."""

  fact_id: str = Field(description="The bracketed id of the addressing fact.")
  reason: str = Field(description="How that fact addresses the limitation.")


class _ProposedFollowUps(BaseModel):
  """The model's follow-up matches for one group (possibly none)."""

  follow_ups: list[_ProposedFollowUp] = Field(default_factory=list)


class _ProposedTag(BaseModel):
  """The model's tag for one group, before its ids are validated."""

  group_id: str = Field(description="The bracketed id of the group.")
  bucket: LimitationBucket = Field(description="The one limitation type.")
  category_ids: list[str] = Field(
    default_factory=list,
    description="Bracketed ids of the listed categories the group is about.",
  )


class _ProposedTags(BaseModel):
  """The model's tags for every group."""

  tags: list[_ProposedTag] = Field(default_factory=list)


def collect_statements(extractions: Sequence[Extraction]) -> list[LimitationStatement]:
  """List every limitation and future-work statement, in a stable order."""
  statements: list[LimitationStatement] = []
  for extraction in sorted(extractions, key=lambda e: e.citation_key):
    for kind, field in _KIND_FIELDS.items():
      for index, fact in enumerate(getattr(extraction.fields, field)):
        statements.append(
          LimitationStatement(
            statement_id=f"{extraction.citation_key}:{field}[{index}]",
            citation_key=extraction.citation_key,
            kind=kind,
            statement=fact.statement,
            evidence=fact.evidence,
          )
        )
  return statements


def merge_hints(
  statements: Sequence[LimitationStatement],
  embedder: Embedder,
  threshold: float,
) -> list[MergeHint]:
  """
  List every cross-Paper statement pair at or above `threshold` cosine.

  Pairs within one Paper are left out: a Paper repeating itself is not the
  cross-Paper recurrence the hints exist to surface (issue #54).
  """
  if len(statements) < 2:
    return []
  vectors = [
    _unit(vector) for vector in embedder.embed([s.statement for s in statements])
  ]
  hints: list[MergeHint] = []
  for i, left in enumerate(statements):
    for j in range(i + 1, len(statements)):
      right = statements[j]
      if left.citation_key == right.citation_key:
        continue
      similarity = sum(map(operator.mul, vectors[i], vectors[j]))
      if similarity >= threshold:
        ids = sorted((left.statement_id, right.statement_id))
        hints.append(
          MergeHint(statement_ids=(ids[0], ids[1]), cosine=round(similarity, 4))
        )
  return sorted(hints, key=lambda hint: (-hint.cosine, hint.statement_ids))


def _unit(vector: Vector) -> Vector:
  """Scale a vector to length 1 so a dot product is its cosine."""
  norm = math.sqrt(math.fsum(value * value for value in vector))
  return [value / norm for value in vector] if norm else list(vector)


def group_limitations(  # pylint: disable=too-many-arguments,too-many-locals
  extractions: Sequence[Extraction],
  years: dict[str, int | None],
  client: LlmClient,
  *,
  tier: ModelTier = "default",
  hints: Sequence[MergeHint] = (),
) -> list[LimitationGroup]:
  """
  Group the Corpus's limitation statements and find each group's follow-ups.

  `years` maps Papers' citation keys to publication years (None when unknown);
  only Papers with an Extraction can count as later, and a Paper with no known
  year never does. `hints` (from `merge_hints`) are offered to the model as
  likely merges; hints naming unknown statements are ignored.
  """
  statements = collect_statements(extractions)
  if not statements:
    return []

  known = {statement.statement_id for statement in statements}
  usable = [hint for hint in hints if set(hint.statement_ids) <= known]
  proposal = complete_model(
    client,
    _grouping_prompt(statements, usable),
    _ProposedGrouping,
    prompt_version=GROUP_PROMPT_VERSION,
    tier=tier,
    attempts=ATTEMPTS,
    accept=lambda grouping: bool(grouping.groups),
  )
  facts = _follow_up_facts(extractions)
  years = {e.citation_key: years.get(e.citation_key) for e in extractions}
  groups: list[LimitationGroup] = []
  for label, members, notes in _validated_groups(statements, proposal):
    groups.append(_with_follow_ups(label, members, notes, years, facts, client, tier))
  groups.sort(key=lambda group: group.statements[0].statement_id)
  for index, group in enumerate(groups, start=1):
    group.group_id = f"limitation-group-{index:02d}"
  return groups


def tag_limitations(
  groups: Sequence[LimitationGroup],
  categories: dict[tuple[Axis, str], str],
  client: LlmClient,
  *,
  tier: ModelTier = "cheap",
) -> tuple[list[LimitationGroup], list[str]]:
  """
  Tag every group with one bucket and the observed categories it concerns.

  `categories` maps each observed (axis, category id) to its label; the model
  may only pick from it. Returns copies of the groups with their tags (unknown
  category ids dropped and noted on the group, an untagged group noted and left
  without a bucket) and notes on tags naming no known group. One call covers
  every group; with no group there is nothing to tag and no call.
  """
  if not groups:
    return [], []
  proposal = complete_model(
    client,
    _tagging_prompt(groups, categories),
    _ProposedTags,
    prompt_version=TAG_PROMPT_VERSION,
    tier=tier,
    attempts=ATTEMPTS,
    accept=lambda tags: bool(tags.tags),
  )
  by_group = {group.group_id: group for group in groups}
  known: dict[str, tuple[Axis, str]] = {
    f"{axis}:{category_id}": (axis, category_id) for axis, category_id in categories
  }
  tagged: dict[str, LimitationGroup] = {}
  run_notes: list[str] = []
  for tag in proposal.tags:
    group_id = _bare(tag.group_id)
    group = by_group.get(group_id)
    if group is None:
      run_notes.append(f"dropped tag for unknown limitation group '{tag.group_id}'")
      continue
    if group_id in tagged:
      tagged[group_id].notes.append("tagged twice by the model; kept the first tag")
      continue
    tagged[group_id] = _tagged(group, tag, known)
  result: list[LimitationGroup] = []
  for group in groups:
    if group.group_id in tagged:
      result.append(tagged[group.group_id])
    else:
      notes = [*group.notes, "not tagged by the model; corroborates no cell"]
      result.append(group.model_copy(update={"notes": notes}))
  return result, run_notes


def _tagged(
  group: LimitationGroup, tag: _ProposedTag, known: dict[str, tuple[Axis, str]]
) -> LimitationGroup:
  """Copy a group with its validated tag; note every unknown category id."""
  notes = list(group.notes)
  concerns: list[TaggedCategory] = []
  for reference in tag.category_ids:
    pair = known.get(_bare(reference))
    if pair is None:
      notes.append(f"dropped unknown category id '{reference}' from its tag")
    elif all((c.axis, c.category_id) != pair for c in concerns):
      concerns.append(TaggedCategory(axis=pair[0], category_id=pair[1]))
  return group.model_copy(
    update={"bucket": tag.bucket, "categories": concerns, "notes": notes}
  )


def _bare(reference: str) -> str:
  """Strip whitespace and the brackets a model copies from a `[id]` rendering."""
  reference = reference.strip()
  if reference.startswith("[") and reference.endswith("]"):
    return reference[1:-1].strip()
  return reference


def _validated_groups(
  statements: list[LimitationStatement], proposal: _ProposedGrouping
) -> list[tuple[str, list[LimitationStatement], list[str]]]:
  """Keep each real statement in exactly one group; note every correction."""
  by_id = {statement.statement_id: statement for statement in statements}
  placed: set[str] = set()
  groups: list[tuple[str, list[LimitationStatement], list[str]]] = []

  for proposed in proposal.groups:
    members: list[LimitationStatement] = []
    notes: list[str] = []
    for statement_id in map(unbracketed, proposed.statement_ids):
      if statement_id not in by_id:
        notes.append(f"dropped unknown statement id '{statement_id}'")
      elif statement_id in placed:
        notes.append(f"'{statement_id}' already placed in an earlier group")
      else:
        placed.add(statement_id)
        members.append(by_id[statement_id])
    if members:
      members.sort(key=lambda statement: statement.statement_id)
      groups.append((proposed.label, members, notes))

  # A statement the model left out still needs a group, or it would silently
  # vanish from the Unanswered Limitation check.
  for statement in statements:
    if statement.statement_id not in placed:
      groups.append(
        (
          statement.statement,
          [statement],
          [f"'{statement.statement_id}' was not grouped by the model; kept alone"],
        )
      )
  return groups


def _with_follow_ups(  # pylint: disable=too-many-arguments,too-many-positional-arguments
  label: str,
  members: list[LimitationStatement],
  notes: list[str],
  years: dict[str, int | None],
  facts: dict[str, tuple[str, ExtractedFact]],
  client: LlmClient,
  tier: ModelTier,
) -> LimitationGroup:
  """Find which later Papers' facts address one group, validating every match."""
  earliest, later = _later_papers(members, years)
  candidates = {
    fact_id: pair for fact_id, pair in facts.items() if pair[0] in set(later)
  }

  follow_ups: list[FollowUp] = []
  if candidates:
    proposal = complete_model(
      client,
      _follow_up_prompt(label, members, candidates),
      _ProposedFollowUps,
      prompt_version=FOLLOW_UP_PROMPT_VERSION,
      tier=tier,
      attempts=ATTEMPTS,
    )
    follow_ups = _validated_follow_ups(proposal, candidates, notes)

  return LimitationGroup(
    group_id="",
    label=label,
    statements=members,
    earliest_year=earliest,
    later_citation_keys=later,
    follow_ups=follow_ups,
    notes=notes,
  )


def _later_papers(
  members: list[LimitationStatement], years: dict[str, int | None]
) -> tuple[int | None, list[str]]:
  """
  Return a group's earliest source year and the Papers published after it.

  A Paper of unknown year is never later, and with no known source year no
  Paper is: the Corpus gives no basis for ordering them.
  """
  sources = {member.citation_key for member in members}
  known = [year for key in sources if (year := years.get(key)) is not None]
  if not known:
    return None, []
  earliest = min(known)
  later = sorted(
    key
    for key, year in years.items()
    if key not in sources and year is not None and year > earliest
  )
  return earliest, later


def _validated_follow_ups(
  proposal: _ProposedFollowUps,
  candidates: dict[str, tuple[str, ExtractedFact]],
  notes: list[str],
) -> list[FollowUp]:
  """Keep only follow-ups citing a real later fact; note every one dropped."""
  follow_ups: list[FollowUp] = []
  for proposed in proposal.follow_ups:
    fact_id = unbracketed(proposed.fact_id)
    pair = candidates.get(fact_id)
    if pair is None:
      notes.append(f"dropped follow-up citing unknown fact '{proposed.fact_id}'")
      continue
    citation_key, fact = pair
    follow_ups.append(
      FollowUp(
        citation_key=citation_key,
        fact_id=fact_id,
        statement=fact.statement,
        evidence=fact.evidence,
        reason=proposed.reason,
      )
    )
  return sorted(follow_ups, key=lambda follow_up: follow_up.fact_id)


def unbracketed(reference: str) -> str:
  """
  Strip the outer brackets a model copies from the prompt's `[id]` rendering.

  Ids are rendered `[key:field[index]]` and the model is told to copy them
  "exactly as shown", so it often returns the bracketed form. Read literally,
  every such id was unknown and every statement ended up alone (issue #54).
  """
  reference = reference.strip()
  outer = re.fullmatch(r"\[(.+:.+\[\d+\])\]", reference)
  return outer.group(1) if outer else reference


def _follow_up_facts(
  extractions: Sequence[Extraction],
) -> dict[str, tuple[str, ExtractedFact]]:
  """Index every Paper's follow-up-relevant facts by id, with their Paper."""
  facts: dict[str, tuple[str, ExtractedFact]] = {}
  for extraction in sorted(extractions, key=lambda e: e.citation_key):
    for field in FOLLOW_UP_FIELDS:
      for index, fact in enumerate(getattr(extraction.fields, field)):
        facts[f"{extraction.citation_key}:{field}[{index}]"] = (
          extraction.citation_key,
          fact,
        )
  return facts


def _grouping_prompt(
  statements: list[LimitationStatement], hints: Sequence[MergeHint]
) -> str:
  """Build the prompt asking the model to group statements by shared meaning."""
  lines = "\n".join(
    f"- [{s.statement_id}] ({s.kind}) {s.statement}" for s in statements
  )
  hint_block = ""
  if hints:
    pairs = "\n".join(
      f"- [{hint.statement_ids[0]}] ~ [{hint.statement_ids[1]}]" for hint in hints
    )
    hint_block = (
      "\n\nSIMILAR PAIRS: a text-similarity model found these statements from "
      "different papers close in meaning. Put each pair in the same group when "
      "both describe the same limitation or open question; ignore a pair whose "
      f"statements only share vocabulary.\n{pairs}"
    )
  return (
    "Below are limitation and future-work statements from several research "
    "papers. Group statements that describe the same underlying limitation or "
    "open question, even when worded differently. A statement that shares its "
    "concern with no other statement forms a group on its own. Put every "
    "statement in exactly one group, citing it by its bracketed id exactly as "
    "shown, and give each group a short label naming the shared concern.\n\n"
    f"STATEMENTS:\n{lines}{hint_block}"
  )


def _follow_up_prompt(
  label: str,
  members: list[LimitationStatement],
  candidates: dict[str, tuple[str, ExtractedFact]],
) -> str:
  """Build the prompt asking which later facts address one limitation group."""
  stated = "\n".join(f"- {member.statement}" for member in members)
  facts = "\n".join(
    f"- [{fact_id}] {fact.statement}" for fact_id, (_, fact) in candidates.items()
  )
  return (
    "An earlier research paper stated the limitation or open question below. "
    "From the facts of later papers listed after it, return only those that "
    "directly address it (for example by studying the missing population, "
    "using the missing design, or answering the open question), each cited by "
    "its bracketed id exactly as shown with a one-sentence reason. Merely "
    "mentioning the topic is not addressing it. Return an empty list if none "
    "do.\n\n"
    f"LIMITATION: {label}\n{stated}\n\n"
    f"LATER PAPER FACTS:\n{facts}"
  )


_BUCKET_GUIDE = (
  "- study_design: the design itself (retrospective, observational, no "
  "randomization, blinding, or control group)\n"
  "- population: who was studied (a narrow or specific population, diagnostic "
  "criteria, generalizability to other people)\n"
  "- setting_data: where the data came from (single centre, one registry, data "
  "source limits)\n"
  "- sample_size: too few patients or events, underpowered\n"
  "- measurement: outcome measures, missing data, what was or was not measured\n"
  "- follow_up: study duration or loss to follow-up\n"
  "- analysis: statistical analysis or confounding\n"
  "- open_question: a question left for future research\n"
  "- other: anything else (funding, and so on)"
)


def _tagging_prompt(
  groups: Sequence[LimitationGroup], categories: dict[tuple[Axis, str], str]
) -> str:
  """Build the prompt asking the model to tag each group with a type and categories."""
  group_lines = "\n".join(
    f"- [{group.group_id}] {group.label}: "
    + " | ".join(statement.statement for statement in group.statements)
    for group in groups
  )
  category_lines = "\n".join(
    f"- [{axis}:{category_id}] ({axis}) {label}"
    for (axis, category_id), label in sorted(categories.items())
  )
  return (
    "Below are limitation and open-question groups stated by the authors of "
    "research papers, and the categories those papers were indexed under. For "
    "every group, give exactly one limitation type from the list, and the ids "
    "of the categories the group is specifically about (for example the "
    "population it says is missing, or the topic it says is unexplored). Cite "
    "groups and categories by their bracketed ids exactly as shown. Give no "
    "category when the group is generic, and never invent an id.\n\n"
    f"LIMITATION TYPES:\n{_BUCKET_GUIDE}\n\n"
    f"GROUPS:\n{group_lines}\n\n"
    f"CATEGORIES:\n{category_lines}"
  )
