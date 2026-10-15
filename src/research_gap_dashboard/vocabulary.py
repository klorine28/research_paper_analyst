"""
Vocabulary expansion: MeSH alias suggestions and the per-Corpus Alias Overlay.

An empty Coverage Matrix cell only means something if the taxonomy knows the
words the field uses (ADR 0010). This module widens the taxonomies' aliases
from MeSH, the free public-domain NLM vocabulary: each category's descriptor
entry terms, and the labels of its MeSH child descriptors that are not
categories themselves (so a Paper about a narrower descriptor rolls up onto its
parent category).

Nothing it finds maps a phrase on its own. Every suggestion enters a Corpus's
Alias Overlay as *proposed*, with its MeSH provenance, and only a human
approval makes it apply, and then only to that Corpus. Every decision is
logged and reversible. Copying an approved alias into the shared taxonomy file
is a separate, deliberate act (`promote_alias`) that keeps the provenance.

Other alias sources ADR 0010 names (the Corpus's mapped terms, promoted
unmapped terms, LLM suggestions) are not here yet; they join the same overlay
as further provenance kinds.
"""

import json
import logging
import tomllib
from collections.abc import Callable, Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlencode

import httpx
from pydantic import BaseModel, Field

from research_gap_dashboard.corpus_layout import CorpusLayout
from research_gap_dashboard.taxonomy import (
  Axis,
  Taxonomy,
  TaxonomyError,
  Topic,
  load_taxonomy,
  normalize_phrase,
)

logger = logging.getLogger(__name__)

ALIAS_OVERLAY_NAME = "alias_overlay.json"

MESH_BASE_URL = "https://id.nlm.nih.gov/mesh"
MESH_CONTACT = (
  "research-gap-dashboard (https://github.com/klorine28/research_paper_analyst)"
)

# A JSON fetch: given a URL, return the parsed body, or None for a 404. Tests
# inject recorded MeSH responses through it.
JsonFetch = Callable[[str], dict[str, Any] | None]

AliasSource = Literal["mesh_entry_term", "mesh_child_descriptor"]
AliasStatus = Literal["proposed", "approved", "rejected", "promoted"]

# The statuses under which an alias maps phrases for its Corpus.
_ACTIVE: frozenset[AliasStatus] = frozenset({"approved", "promoted"})


class AliasOverlayError(Exception):
  """Raised when an overlay decision would break the controlled vocabulary."""


class MeshDescriptor(BaseModel):
  """One MeSH descriptor as the child-descriptor query reports it."""

  mesh_id: str
  label: str
  mesh_tree: str


class AliasProvenance(BaseModel):
  """Where a suggested alias came from, so it can be traced back to MeSH."""

  source: AliasSource
  mesh_id: str = Field(description="The MeSH descriptor the phrase came from.")
  mesh_label: str = Field(description="That descriptor's preferred label.")
  mesh_tree: str | None = Field(
    default=None, description="The child descriptor's tree number, if a child."
  )

  def describe(self) -> str:
    """Render the provenance as one line for the shared taxonomy file."""
    if self.source == "mesh_entry_term":
      return f"MeSH entry term of {self.mesh_id} ({self.mesh_label})"
    return f"MeSH child descriptor {self.mesh_id} ({self.mesh_label}, {self.mesh_tree})"


class AliasSuggestion(BaseModel):
  """A phrase offered as an alias of one category, with its provenance."""

  axis: Axis
  category_id: str
  alias: str
  provenance: AliasProvenance


class AliasDecision(BaseModel):
  """One logged step in an alias's life: proposed, approved, rejected, promoted."""

  action: AliasStatus
  at: datetime


class OverlayAlias(AliasSuggestion):
  """A suggestion in a Corpus's overlay, with its current status and history."""

  status: AliasStatus
  history: list[AliasDecision]


class AliasOverlay(BaseModel):
  """One Corpus's logged alias suggestions and the human decisions on them."""

  entries: list[OverlayAlias] = Field(default_factory=list)


class MeshVocabulary:
  """Reads entry terms and child descriptors from the NLM MeSH linked-data API."""

  def __init__(self, fetch: JsonFetch | None = None):
    """Wrap the MeSH API; pass a fake `fetch` to run offline."""
    self._fetch = fetch or _httpx_fetch

  def entry_terms(self, mesh_id: str) -> list[str]:
    """Return a descriptor's entry terms (its preferred label among them)."""
    url = f"{MESH_BASE_URL}/lookup/details?{urlencode({'descriptor': mesh_id})}"
    payload = self._fetch(url) or {}
    return [term["label"] for term in payload.get("terms") or [] if term.get("label")]

  def child_descriptors(self, mesh_tree: str) -> list[MeshDescriptor]:
    """Return the descriptors filed directly under a tree number."""
    query = (
      "PREFIX meshv: <http://id.nlm.nih.gov/mesh/vocab#>\n"
      "PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>\n"
      "SELECT ?d ?label ?tn WHERE {\n"
      f"  ?t meshv:parentTreeNumber <http://id.nlm.nih.gov/mesh/{mesh_tree}> .\n"
      "  ?d meshv:treeNumber ?t .\n"
      "  ?d rdfs:label ?label .\n"
      "  ?t rdfs:label ?tn .\n"
      '  FILTER(LANG(?label) = "en")\n'
      "}"
    )
    params = {"query": query, "format": "JSON", "inference": "false"}
    payload = self._fetch(f"{MESH_BASE_URL}/sparql?{urlencode(params)}") or {}
    bindings = (payload.get("results") or {}).get("bindings") or []
    return [
      MeshDescriptor(
        mesh_id=binding["d"]["value"].rsplit("/", 1)[-1],
        label=binding["label"]["value"],
        mesh_tree=binding["tn"]["value"],
      )
      for binding in bindings
    ]


def suggest_mesh_aliases(
  taxonomy: Taxonomy, mesh: MeshVocabulary
) -> list[AliasSuggestion]:
  """
  Suggest new aliases for every MeSH-backed category of one taxonomy.

  A phrase the taxonomy already resolves is skipped (redundant, or owned by
  another category), as is one MeSH would hand to two categories at once:
  either would make the vocabulary ambiguous.
  """
  axis = taxonomy.meta.axis
  category_mesh_ids = {topic.mesh_id for topic in taxonomy.topics if topic.mesh_id}
  raw: list[AliasSuggestion] = []
  for topic in taxonomy.topics:
    if topic.mesh_id:
      for term in mesh.entry_terms(topic.mesh_id):
        provenance = AliasProvenance(
          source="mesh_entry_term", mesh_id=topic.mesh_id, mesh_label=topic.label
        )
        raw.append(_suggestion(axis, topic, term, provenance))
    if topic.mesh_tree:
      for child in mesh.child_descriptors(topic.mesh_tree):
        if child.mesh_id in category_mesh_ids:
          continue
        provenance = AliasProvenance(
          source="mesh_child_descriptor",
          mesh_id=child.mesh_id,
          mesh_label=child.label,
          mesh_tree=child.mesh_tree,
        )
        raw.append(_suggestion(axis, topic, child.label, provenance))

  claimants: dict[str, set[str]] = {}
  for suggestion in raw:
    key = normalize_phrase(suggestion.alias)
    claimants.setdefault(key, set()).add(suggestion.category_id)

  kept: list[AliasSuggestion] = []
  seen: set[str] = set()
  for suggestion in raw:
    key = normalize_phrase(suggestion.alias)
    if key in seen or len(claimants[key]) > 1:
      continue
    if taxonomy.resolve(suggestion.alias) is not None:
      continue
    seen.add(key)
    kept.append(suggestion)
  return kept


def _suggestion(
  axis: Axis, topic: Topic, alias: str, provenance: AliasProvenance
) -> AliasSuggestion:
  """Build one suggestion for a category."""
  return AliasSuggestion(
    axis=axis, category_id=topic.id, alias=alias, provenance=provenance
  )


def alias_overlay_path(root: Path) -> Path:
  """Return where a Corpus keeps its Alias Overlay (with the other judgments)."""
  return CorpusLayout(root=root).judgments_dir / ALIAS_OVERLAY_NAME


def read_alias_overlay(root: Path) -> AliasOverlay:
  """Read a Corpus's Alias Overlay; a Corpus without one has an empty overlay."""
  path = alias_overlay_path(root)
  if not path.is_file():
    return AliasOverlay()
  return AliasOverlay.model_validate_json(path.read_text(encoding="utf-8"))


def write_alias_overlay(root: Path, overlay: AliasOverlay) -> Path:
  """Write a Corpus's Alias Overlay and return its path."""
  path = alias_overlay_path(root)
  path.parent.mkdir(parents=True, exist_ok=True)
  path.write_text(overlay.model_dump_json(indent=2), encoding="utf-8")
  return path


def propose_aliases(
  overlay: AliasOverlay, suggestions: Iterable[AliasSuggestion]
) -> AliasOverlay:
  """
  Log new suggestions as proposed; a phrase already in the overlay is left alone.

  Re-running a suggestion source therefore never re-offers an alias a human
  already rejected, nor resets one already approved.
  """
  updated = overlay.model_copy(deep=True)
  known = {_entry_key(e.axis, e.category_id, e.alias) for e in updated.entries}
  for suggestion in suggestions:
    key = _entry_key(suggestion.axis, suggestion.category_id, suggestion.alias)
    if key in known:
      continue
    known.add(key)
    updated.entries.append(
      OverlayAlias(
        **suggestion.model_dump(),
        status="proposed",
        history=[AliasDecision(action="proposed", at=_now())],
      )
    )
  return updated


def approve_alias(
  overlay: AliasOverlay,
  taxonomies: Sequence[Taxonomy],
  axis: Axis,
  category_id: str,
  alias: str,
) -> AliasOverlay:
  """
  Approve a logged suggestion, so it maps phrases for this Corpus.

  Refused when the phrase already maps to another category (shared taxonomy or
  an approved overlay alias), so the overlay can never make a phrase ambiguous.
  """
  updated = overlay.model_copy(deep=True)
  entry = _find_entry(updated, axis, category_id, alias)
  if entry.status in _ACTIVE:
    return updated
  taxonomy = _taxonomy_for(apply_alias_overlay(taxonomies, updated), axis)
  if taxonomy.topic_by_id(category_id) is None:
    raise AliasOverlayError(f"The {axis} taxonomy has no category '{category_id}'.")
  owner = taxonomy.resolve(alias)
  if owner is not None and owner.id != category_id:
    raise AliasOverlayError(
      f"'{alias}' already maps to {axis} category '{owner.id}'; approving it for "
      f"'{category_id}' would make it ambiguous."
    )
  _decide(entry, "approved")
  return updated


def reject_alias(
  overlay: AliasOverlay, axis: Axis, category_id: str, alias: str
) -> AliasOverlay:
  """Reject a suggestion, or reverse an approval; the alias stops mapping."""
  updated = overlay.model_copy(deep=True)
  entry = _find_entry(updated, axis, category_id, alias)
  if entry.status == "promoted":
    raise AliasOverlayError(
      f"'{alias}' was promoted to the shared taxonomy; remove it from that file."
    )
  if entry.status != "rejected":
    _decide(entry, "rejected")
  return updated


def apply_alias_overlay(
  taxonomies: Sequence[Taxonomy], overlay: AliasOverlay
) -> list[Taxonomy]:
  """
  Return copies of the taxonomies with the overlay's approved aliases added.

  The shared taxonomies are not modified. An approved alias whose category has
  since left the taxonomy, or whose phrase another category now owns, is
  skipped with a warning rather than breaking validation.
  """
  applied = [taxonomy.model_copy(deep=True) for taxonomy in taxonomies]
  by_axis = {taxonomy.meta.axis: taxonomy for taxonomy in applied}
  for entry in overlay.entries:
    if entry.status not in _ACTIVE:
      continue
    taxonomy = by_axis.get(entry.axis)
    if taxonomy is None:
      continue
    topic = taxonomy.topic_by_id(entry.category_id)
    if topic is None:
      logger.warning(
        "Overlay alias '%s' names %s category '%s', which is not in the taxonomy.",
        entry.alias,
        entry.axis,
        entry.category_id,
      )
      continue
    owner = taxonomy.resolve(entry.alias)
    if owner is topic:
      continue
    if owner is not None:
      logger.warning(
        "Overlay alias '%s' for '%s' is skipped: category '%s' now owns it.",
        entry.alias,
        entry.category_id,
        owner.id,
      )
      continue
    topic.aliases.append(entry.alias)
  return applied


def promote_alias(
  overlay: AliasOverlay,
  taxonomy_path: Path,
  axis: Axis,
  category_id: str,
  alias: str,
) -> AliasOverlay:
  """
  Copy an approved alias, with its provenance, into a shared taxonomy file.

  The file is edited in place (comments and layout kept) and re-validated; if
  the result does not load, the original text is restored and the overlay is
  unchanged.
  """
  updated = overlay.model_copy(deep=True)
  entry = _find_entry(updated, axis, category_id, alias)
  if entry.status != "approved":
    raise AliasOverlayError(
      f"Only an approved alias can be promoted; '{alias}' is {entry.status}."
    )
  try:
    taxonomy = load_taxonomy(taxonomy_path)
  except TaxonomyError as error:
    raise AliasOverlayError(str(error)) from error
  if taxonomy.meta.axis != axis:
    raise AliasOverlayError(
      f"'{taxonomy_path}' is the {taxonomy.meta.axis} taxonomy, not the {axis} one."
    )

  original = taxonomy_path.read_text(encoding="utf-8")
  promoted = _promoted_toml(
    original, entry.category_id, entry.alias, entry.provenance.describe()
  )
  taxonomy_path.write_text(promoted, encoding="utf-8")
  try:
    load_taxonomy(taxonomy_path)
  except TaxonomyError as error:
    taxonomy_path.write_text(original, encoding="utf-8")
    raise AliasOverlayError(str(error)) from error
  _decide(entry, "promoted")
  return updated


def _promoted_toml(text: str, category_id: str, alias: str, source: str) -> str:
  """Add an alias and its source to one `[[topics]]` block of a taxonomy file."""
  lines = text.splitlines()
  block = _topic_block(lines, category_id)
  aliases_at = _key_line(lines, block, "aliases")
  sources_at = _key_line(lines, block, "alias_sources")

  aliases: list[str] = [] if aliases_at is None else aliases_at[1]
  if not any(normalize_phrase(a) == normalize_phrase(alias) for a in aliases):
    aliases = [*aliases, alias]
  sources: dict[str, str] = {} if sources_at is None else sources_at[1]
  sources = {**sources, alias: source}

  aliases_line = "aliases = [" + ", ".join(_toml_string(a) for a in aliases) + "]"
  sources_line = (
    "alias_sources = { "
    + ", ".join(f"{_toml_string(k)} = {_toml_string(v)}" for k, v in sources.items())
    + " }"
  )
  if aliases_at is None:
    aliases_index = block.start
    lines.insert(aliases_index, aliases_line)
  else:
    aliases_index = aliases_at[0]
    lines[aliases_index] = aliases_line
  if sources_at is None:
    lines.insert(aliases_index + 1, sources_line)
  else:
    lines[sources_at[0]] = sources_line
  return "\n".join(lines) + "\n"


def _topic_block(lines: list[str], category_id: str) -> range:
  """Return the line range of the `[[topics]]` entry with this id."""
  starts = [i for i, line in enumerate(lines) if line.strip() == "[[topics]]"]
  for start in starts:
    end = next(
      (i for i in range(start + 1, len(lines)) if lines[i].lstrip().startswith("[")),
      len(lines),
    )
    block = range(start + 1, end)
    found = _key_line(lines, block, "id")
    if found is not None and found[1] == category_id:
      return block
  raise AliasOverlayError(f"No [[topics]] entry with id '{category_id}' in the file.")


def _key_line(lines: list[str], block: range, key: str) -> tuple[int, Any] | None:
  """Find a single-line `key = value` in a block and parse its value."""
  for index in block:
    stripped = lines[index].strip()
    if not (stripped.startswith(f"{key} ") or stripped.startswith(f"{key}=")):
      continue
    try:
      return index, tomllib.loads(stripped)[key]
    except tomllib.TOMLDecodeError as error:
      raise AliasOverlayError(
        f"'{key}' spans several lines in this entry; add the alias by hand."
      ) from error
  return None


def _toml_string(value: str) -> str:
  """Quote a string as a TOML basic string (JSON escapes are valid TOML)."""
  return json.dumps(value, ensure_ascii=False)


def _taxonomy_for(taxonomies: Sequence[Taxonomy], axis: Axis) -> Taxonomy:
  """Return the taxonomy covering this axis."""
  for taxonomy in taxonomies:
    if taxonomy.meta.axis == axis:
      return taxonomy
  raise AliasOverlayError(f"No {axis} taxonomy is loaded.")


def _find_entry(
  overlay: AliasOverlay, axis: Axis, category_id: str, alias: str
) -> OverlayAlias:
  """Return the overlay entry for this alias, or explain that there is none."""
  key = _entry_key(axis, category_id, alias)
  for entry in overlay.entries:
    if _entry_key(entry.axis, entry.category_id, entry.alias) == key:
      return entry
  raise AliasOverlayError(
    f"There is no suggested alias '{alias}' for {axis} category '{category_id}'."
  )


def _entry_key(axis: str, category_id: str, alias: str) -> tuple[str, str, str]:
  """Key an overlay entry by axis, category, and folded phrase."""
  return axis, category_id, normalize_phrase(alias)


def _decide(entry: OverlayAlias, action: AliasStatus) -> None:
  """Move an entry to a new status and log the step."""
  entry.status = action
  entry.history.append(AliasDecision(action=action, at=_now()))


def _now() -> datetime:
  """Return the current time, in UTC, for the decision log."""
  return datetime.now(UTC)


def _httpx_fetch(url: str) -> dict[str, Any] | None:
  """Fetch JSON from the MeSH API, treating 404 as 'no such resource'."""
  response = httpx.get(
    url,
    headers={"Accept": "application/json", "User-Agent": MESH_CONTACT},
    follow_redirects=True,
    timeout=60.0,
  )
  if response.status_code == httpx.codes.NOT_FOUND:
    return None
  response.raise_for_status()
  return response.json()
