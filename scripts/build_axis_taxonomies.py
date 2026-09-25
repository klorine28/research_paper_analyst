# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx>=0.27"]
# ///
"""
Derive the Method, Population, and Dataset taxonomy seeds from MeSH.

Run manually (`uv run --script scripts/build_axis_taxonomies.py`) to rebuild
`taxonomies/methods.toml`, `taxonomies/populations.toml`, and
`taxonomies/datasets.toml`; the test suite never touches the network.

Unlike the Topic seed (a whole MeSH subtree), these axes are small curated
picks: MeSH has no single subtree that is "study designs" or "data sources", so
each axis lists the descriptor labels (topical descriptors, check tags, and
publication types) worth a Coverage Matrix column. Every label is resolved
against MeSH by exact match, so each category keeps a real descriptor id, tree
number, and the descriptor's entry terms as aliases. An unresolvable label
aborts the build rather than writing a category with invented provenance.
"""

import sys
import time
import unicodedata
from pathlib import Path
from typing import Any

import httpx

LOOKUP = "https://id.nlm.nih.gov/mesh/lookup"
RECORD = "https://id.nlm.nih.gov/mesh"
CONTACT = "research-gap-dashboard (https://github.com/klorine28/research_paper_analyst)"
MESH_VERSION = "2025"
# The NLM endpoint drops idle keep-alive connections; retry with backoff.
RETRIES = 5

TAXONOMIES_DIR = Path(__file__).resolve().parents[1] / "taxonomies"

# axis -> (output file, one-line scope, curated MeSH descriptor labels).
AXES: dict[str, tuple[str, str, list[str]]] = {
  "method": (
    "methods.toml",
    "study designs and research methods (MeSH study characteristics and "
    "publication types)",
    [
      "Randomized Controlled Trial",
      "Controlled Clinical Trial",
      "Observational Study",
      "Cohort Studies",
      "Prospective Studies",
      "Retrospective Studies",
      "Case-Control Studies",
      "Cross-Sectional Studies",
      "Case Reports",
      "Systematic Review",
      "Meta-Analysis",
      "Surveys and Questionnaires",
      "Qualitative Research",
      "Mendelian Randomization Analysis",
      "Models, Animal",
      "In Vitro Techniques",
      "Computer Simulation",
      "Machine Learning",
    ],
  ),
  "population": (
    "populations.toml",
    "populations and samples studied (MeSH age groups, sex, species, care setting)",
    [
      "Humans",
      "Infant, Newborn",
      "Infant",
      "Child",
      "Adolescent",
      "Young Adult",
      "Adult",
      "Middle Aged",
      "Aged",
      "Aged, 80 and over",
      "Female",
      "Male",
      "Pregnant People",
      "Inpatients",
      "Outpatients",
      "Animals",
      "Mice",
      "Rats",
      "Swine",
    ],
  ),
  "dataset": (
    "datasets.toml",
    "datasets and data sources used (MeSH records, registries, databases)",
    [
      "Registries",
      "Electronic Health Records",
      "Medical Records",
      "Hospital Records",
      "Administrative Claims, Healthcare",
      "Databases, Factual",
      "Health Surveys",
      "Population Surveillance",
      "Biological Specimen Banks",
      "Vital Statistics",
    ],
  ),
}


# MeSH publication types carry no entry terms, so the commonest spellings of
# those designs are hand-added here (the only aliases not taken from MeSH).
EXTRA_ALIASES: dict[str, list[str]] = {
  "Randomized Controlled Trial": [
    "RCT",
    "Randomised Controlled Trial",
    "Randomized Trial",
    "Randomised Trial",
  ],
  "Controlled Clinical Trial": ["Non-Randomized Controlled Trial"],
  "Meta-Analysis": ["Meta-analyses", "Meta analysis"],
  "Prospective Studies": ["Prospective Study", "Prospective Cohort"],
}


def _get(client: httpx.Client, url: str, **params: str) -> Any:
  """GET one MeSH API resource as JSON, retrying the server's dropped connections."""
  for attempt in range(RETRIES):
    try:
      response = client.get(url, params=params)
      response.raise_for_status()
      return response.json()
    except (httpx.TransportError, httpx.HTTPStatusError):
      if attempt == RETRIES - 1:
        raise
      time.sleep(2**attempt)
  raise AssertionError("unreachable")


def _local_name(uri: str) -> str:
  """Return the id at the end of a MeSH URI (.../D015331 -> D015331)."""
  return uri.rsplit("/", 1)[-1]


def resolve_descriptor(client: httpx.Client, label: str) -> dict[str, Any]:
  """Resolve one exact MeSH label to its id, tree numbers, and entry terms."""
  matches = _get(client, f"{LOOKUP}/descriptor", label=label, match="exact")
  if len(matches) != 1:
    raise LookupError(f"MeSH label {label!r} matched {len(matches)} descriptors")
  descriptor_id = _local_name(matches[0]["resource"])

  record = _get(client, f"{RECORD}/{descriptor_id}.json")
  raw_trees = record.get("treeNumber", [])
  trees = [raw_trees] if isinstance(raw_trees, str) else list(raw_trees)
  tree_numbers = sorted(_local_name(tree) for tree in trees)

  details = _get(client, f"{LOOKUP}/details", descriptor=descriptor_id)
  terms = [term["label"] for term in details.get("terms", [])]
  terms.extend(EXTRA_ALIASES.get(label, []))
  return {
    "id": descriptor_id,
    "label": matches[0]["label"],
    "tree_numbers": tree_numbers,
    "terms": terms,
  }


def _slug(label: str) -> str:
  """Build a stable, human-readable category id from the MeSH label."""
  normalised = unicodedata.normalize("NFKD", label).encode("ascii", "ignore").decode()
  slug = "".join(c if c.isalnum() else "-" for c in normalised.lower())
  return "-".join(part for part in slug.split("-") if part)


def _toml_string(value: str) -> str:
  """Quote a string as a TOML basic string."""
  escaped = value.replace("\\", "\\\\").replace('"', '\\"')
  return f'"{escaped}"'


def _toml_array(values: list[str]) -> str:
  """Render a list of strings as a single-line TOML array."""
  return "[" + ", ".join(_toml_string(value) for value in values) + "]"


def _unambiguous_aliases(descriptors: list[dict[str, Any]]) -> dict[str, list[str]]:
  """Keep each descriptor's entry terms, dropping any shared with another one."""
  owners: dict[str, set[str]] = {}
  for descriptor in descriptors:
    for name in (_slug(descriptor["label"]), descriptor["label"], *descriptor["terms"]):
      owners.setdefault(" ".join(name.split()).casefold(), set()).add(descriptor["id"])

  aliases: dict[str, list[str]] = {}
  for descriptor in descriptors:
    seen: set[str] = {descriptor["label"].casefold()}
    kept: list[str] = []
    for term in descriptor["terms"]:
      key = " ".join(term.split()).casefold()
      # The loader rejects a phrase that maps onto two categories, so an entry
      # term shared between picks is dropped rather than failing the seed.
      if key in seen or len(owners[key]) > 1:
        continue
      seen.add(key)
      kept.append(term)
    aliases[descriptor["id"]] = kept
  return aliases


def build_toml(axis: str, scope: str, descriptors: list[dict[str, Any]]) -> str:
  """Render one axis seed as documented, human-editable TOML."""
  aliases = _unambiguous_aliases(descriptors)
  slug_by_tree = {
    tree: _slug(d["label"]) for d in descriptors for tree in d["tree_numbers"]
  }

  lines = [
    f"# {axis.capitalize()} taxonomy (seed).",
    "#",
    f"# The controlled vocabulary of the Coverage Matrix's {axis} axis: {scope}.",
    f"# Curated from MeSH {MESH_VERSION} by scripts/build_axis_taxonomies.py. This",
    "# is a starting point: prune categories you do not care about and add your",
    "# own. Every category keeps its MeSH descriptor id and tree number as",
    '# provenance; a hand-added category sets mesh_id = "".',
    "",
    "[meta]",
    f"axis = {_toml_string(axis)}",
    'domain = "biomedical"',
    f'source = "MeSH {MESH_VERSION}"',
    'source_url = "https://meshb.nlm.nih.gov/"',
    "",
  ]
  for descriptor in descriptors:
    tree = descriptor["tree_numbers"][0] if descriptor["tree_numbers"] else None
    parent = None
    for candidate in descriptor["tree_numbers"]:
      parent = parent or slug_by_tree.get(candidate.rsplit(".", 1)[0])

    lines.append("[[topics]]")
    lines.append(f"id = {_toml_string(_slug(descriptor['label']))}")
    lines.append(f"label = {_toml_string(descriptor['label'])}")
    lines.append(f"mesh_id = {_toml_string(descriptor['id'])}")
    if tree is not None:
      lines.append(f"mesh_tree = {_toml_string(tree)}")
    if parent is not None:
      lines.append(f"parent = {_toml_string(parent)}")
    lines.append(f"aliases = {_toml_array(aliases[descriptor['id']])}")
    lines.append("")
  return "\n".join(lines)


def main() -> int:
  """Resolve every curated label against MeSH and write the three axis seeds."""
  with httpx.Client(headers={"User-Agent": CONTACT}, timeout=60.0) as client:
    for axis, (file_name, scope, labels) in AXES.items():
      try:
        descriptors = [resolve_descriptor(client, label) for label in labels]
      except LookupError as error:
        print(f"{axis}: {error}; aborting.", file=sys.stderr)
        return 1
      output = TAXONOMIES_DIR / file_name
      output.write_text(build_toml(axis, scope, descriptors), encoding="utf-8")
      print(f"Wrote {len(descriptors)} {axis} categories to {output}")
  return 0


if __name__ == "__main__":
  sys.exit(main())
