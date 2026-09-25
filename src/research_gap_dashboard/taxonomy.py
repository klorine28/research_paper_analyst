"""
The taxonomies: the controlled vocabularies the Coverage Matrix hangs off.

The Coverage Matrix has four axes (Topic, Method, Population, Dataset), each
with its own taxonomy file in the same format; `[meta] axis` says which axis a
file covers (Topic when omitted). Entries are `[[topics]]` on every axis, so one
loader and one set of validation rules serve all four.

A taxonomy is a human-editable TOML file (see `taxonomies/cardiology.toml`),
seeded from MeSH so a researcher starts from a real vocabulary and prunes or
extends it. Each Topic keeps its MeSH descriptor id and tree number as
provenance, and carries the descriptor's entry terms as aliases so the Aggregate
stage can map raw extracted phrases onto a canonical Topic.

This module only loads and validates that file. It never reads a Paper or maps
Extractions; that is the Aggregate stage's job (a later issue). The loader
collects every problem it can find and raises them together, so a hand-edit is
corrected in one pass rather than one error at a time.
"""

import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

Axis = Literal["topic", "method", "population", "dataset"]

# Every axis, in the order the Coverage Matrix and the artifact present them.
AXES: tuple[Axis, ...] = ("topic", "method", "population", "dataset")

_TAXONOMIES_DIR = Path(__file__).resolve().parents[2] / "taxonomies"

# The committed MeSH-seeded starting vocabularies, at the repository root.
CARDIOLOGY_SEED_PATH = _TAXONOMIES_DIR / "cardiology.toml"
SEED_PATHS: dict[Axis, Path] = {
  "topic": CARDIOLOGY_SEED_PATH,
  "method": _TAXONOMIES_DIR / "methods.toml",
  "population": _TAXONOMIES_DIR / "populations.toml",
  "dataset": _TAXONOMIES_DIR / "datasets.toml",
}


class TaxonomyError(Exception):
  """
  Raised when a taxonomy file cannot be loaded or fails validation.

  Carries every problem found, so the whole file can be fixed at once.
  """

  def __init__(self, problems: list[str]) -> None:
    self.problems = problems
    super().__init__("Taxonomy file has problems:\n  - " + "\n  - ".join(problems))


class TaxonomyMeta(BaseModel):
  """Provenance for the whole taxonomy: where the vocabulary came from."""

  model_config = ConfigDict(extra="allow")

  axis: Axis = Field(
    default="topic", description="The Coverage Matrix axis this vocabulary covers."
  )
  domain: str = Field(description="The field this vocabulary covers, e.g. cardiology.")
  source: str = Field(description="Where the seed came from, e.g. 'MeSH 2025'.")


class Topic(BaseModel):
  """
  One category in the controlled vocabulary, with its MeSH provenance.

  `extra='forbid'` turns a typo'd key (e.g. `alias` for `aliases`) into a
  reported problem instead of silently dropped data.
  """

  model_config = ConfigDict(extra="forbid")

  id: str = Field(description="Stable slug used as this Topic's key.")
  label: str = Field(description="Human-readable Topic name.")
  mesh_id: str = Field(
    description="MeSH descriptor id (e.g. D009202), or '' for a hand-added Topic."
  )
  mesh_tree: str | None = Field(
    default=None, description="MeSH tree number (e.g. C14.280.238), if from MeSH."
  )
  parent: str | None = Field(
    default=None, description="id of the broader Topic, for a shallow hierarchy."
  )
  aliases: list[str] = Field(
    default_factory=list,
    description="Synonyms an extracted phrase may match on (MeSH entry terms).",
  )
  description: str | None = Field(default=None, description="Optional scope note.")


class Taxonomy(BaseModel):
  """A validated controlled vocabulary of Topics and its provenance."""

  meta: TaxonomyMeta
  # Defaulted so an empty or missing table yields the clear "no topics" problem
  # from semantic validation rather than a bare pydantic "field required".
  topics: list[Topic] = Field(default_factory=list)

  def topic_by_id(self, topic_id: str) -> Topic | None:
    """Return the Topic with this id, or None."""
    for topic in self.topics:
      if topic.id == topic_id:
        return topic
    return None

  def resolve(self, phrase: str) -> Topic | None:
    """Map a raw phrase onto a Topic by id, label, or alias (case-insensitive)."""
    key = _normalize(phrase)
    if not key:
      return None
    for topic in self.topics:
      candidates = [topic.id, topic.label, *topic.aliases]
      if any(_normalize(candidate) == key for candidate in candidates):
        return topic
    return None


def _normalize(value: str) -> str:
  """Fold case and collapse internal whitespace for tolerant matching."""
  return " ".join(value.split()).casefold()


def load_taxonomy(path: Path) -> Taxonomy:
  """Load and validate a taxonomy file, raising TaxonomyError on any problem."""
  if not path.is_file():
    raise TaxonomyError([f"Taxonomy file '{path}' does not exist."])

  try:
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
  except tomllib.TOMLDecodeError as error:
    raise TaxonomyError([f"Could not parse '{path}' as TOML: {error}"]) from error

  try:
    taxonomy = Taxonomy.model_validate(raw)
  except ValidationError as error:
    raise TaxonomyError(_format_validation_errors(error)) from error

  problems = _semantic_problems(taxonomy)
  if problems:
    raise TaxonomyError(problems)
  return taxonomy


def _format_validation_errors(error: ValidationError) -> list[str]:
  """Turn pydantic errors into messages that point at the offending topic."""
  problems: list[str] = []
  for detail in error.errors():
    location = ".".join(str(part) for part in detail["loc"])
    problems.append(f"{location or '<root>'}: {detail['msg']}")
  return problems


def _semantic_problems(taxonomy: Taxonomy) -> list[str]:
  """Collect problems beyond field shape: emptiness, uniqueness, hierarchy."""
  problems: list[str] = []
  if not taxonomy.topics:
    problems.append("Taxonomy has no topics; the vocabulary would detect nothing.")
    return problems

  problems.extend(_duplicate_id_problems(taxonomy))
  problems.extend(_alias_problems(taxonomy))
  problems.extend(_parent_problems(taxonomy))
  return problems


def _duplicate_id_problems(taxonomy: Taxonomy) -> list[str]:
  """Report any Topic id used by more than one Topic."""
  seen: set[str] = set()
  duplicates: set[str] = set()
  for topic in taxonomy.topics:
    if topic.id in seen:
      duplicates.add(topic.id)
    seen.add(topic.id)
  return [f"Duplicate topic id '{dup}'." for dup in sorted(duplicates)]


def _alias_problems(taxonomy: Taxonomy) -> list[str]:
  """Report aliases that map one phrase onto more than one Topic."""
  owners: dict[str, list[str]] = {}
  for topic in taxonomy.topics:
    for name in (topic.id, topic.label, *topic.aliases):
      owners.setdefault(_normalize(name), []).append(topic.id)

  problems: list[str] = []
  for name, topic_ids in owners.items():
    distinct = sorted(set(topic_ids))
    if len(distinct) > 1:
      problems.append(
        f"Alias '{name}' is ambiguous: it maps to topics {', '.join(distinct)}."
      )
  return problems


def _parent_problems(taxonomy: Taxonomy) -> list[str]:
  """Report parent references that do not resolve or that form a cycle."""
  by_id = {topic.id: topic for topic in taxonomy.topics}
  problems: list[str] = []

  for topic in taxonomy.topics:
    if topic.parent is not None and topic.parent not in by_id:
      problems.append(
        f"Topic '{topic.id}' names parent '{topic.parent}', which does not exist."
      )

  for topic in taxonomy.topics:
    if _has_parent_cycle(topic, by_id):
      problems.append(f"Topic '{topic.id}' is part of a parent cycle.")
  return problems


def _has_parent_cycle(topic: Topic, by_id: dict[str, Topic]) -> bool:
  """Report whether following `parent` links from this Topic loops on itself."""
  seen: set[str] = set()
  current: Topic | None = topic
  while current is not None and current.parent is not None:
    if current.parent == topic.id:
      return True
    if current.parent in seen:
      return False
    seen.add(current.parent)
    current = by_id.get(current.parent)
  return False
