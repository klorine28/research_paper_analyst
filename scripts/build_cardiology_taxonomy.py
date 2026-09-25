# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx>=0.27"]
# ///
"""
Derive the cardiology topic-taxonomy seed from MeSH.

Run manually (`uv run --script scripts/build_cardiology_taxonomy.py`) to
rebuild `taxonomies/cardiology.toml`; the test suite never touches the network.
The seed is the direct children of "Heart Diseases" (MeSH tree C14.280) plus
the "Cardiomyopathies" subtree, so a researcher scoping a cardiology corpus
starts from a real controlled vocabulary and prunes or extends it by hand.

Each Topic keeps its MeSH descriptor id and tree number as provenance, and the
descriptor's entry terms become aliases the Aggregate stage can map raw
extracted phrases onto.
"""

import sys
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Any

import httpx

ENDPOINT = "https://id.nlm.nih.gov/mesh/sparql"
CONTACT = "research-gap-dashboard (https://github.com/klorine28/research_paper_analyst)"
MESH_VERSION = "2025"

# Heart Diseases and everything under it; we keep the first level plus the
# Cardiomyopathies subtree, which is where the Takotsubo fixture corpus lives.
TREE_ROOT = "C14.280"
KEEP_SUBTREES = ("C14.280.238",)  # Cardiomyopathies

OUTPUT = Path(__file__).resolve().parents[1] / "taxonomies" / "cardiology.toml"


def _run_query(query: str) -> list[dict[str, Any]]:
  """Run one SPARQL query against the MeSH endpoint, paging past its row cap."""
  # The endpoint caps each response (1000 rows); page with LIMIT/OFFSET and
  # keep asserted triples only (inference balloons rows via inherited ancestry).
  page_size = 1000
  offset = 0
  bindings: list[dict[str, Any]] = []
  while True:
    paged = f"{query}\nLIMIT {page_size} OFFSET {offset}"
    response = httpx.get(
      ENDPOINT,
      params={"query": paged, "format": "JSON", "inference": "false"},
      headers={"Accept": "application/json", "User-Agent": CONTACT},
      timeout=60.0,
    )
    response.raise_for_status()
    page = response.json()["results"]["bindings"]
    bindings.extend(page)
    if len(page) < page_size:
      return bindings
    offset += page_size


def _local_name(uri: str) -> str:
  """Return the MeSH id at the end of a descriptor URI (.../D009202 -> D009202)."""
  return uri.rsplit("/", 1)[-1]


def fetch_descriptors() -> list[dict[str, Any]]:
  """Return each in-scope descriptor with its label and shallowest tree number."""
  query = f"""
  PREFIX meshv: <http://id.nlm.nih.gov/mesh/vocab#>
  PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
  SELECT ?d ?label ?tn WHERE {{
    ?d a meshv:TopicalDescriptor .
    ?d rdfs:label ?label .
    ?d meshv:treeNumber ?t .
    ?t rdfs:label ?tn .
    FILTER(LANG(?label) = "en")
    FILTER(STRSTARTS(STR(?tn), "{TREE_ROOT}."))
  }}
  ORDER BY ?tn
  """
  by_id: dict[str, dict[str, Any]] = {}
  for binding in _run_query(query):
    descriptor_id = _local_name(binding["d"]["value"])
    tree_number = binding["tn"]["value"]
    depth = tree_number.count(".")
    first_level = depth == TREE_ROOT.count(".") + 1
    in_kept_subtree = any(tree_number.startswith(f"{s}.") for s in KEEP_SUBTREES)
    if not (first_level or in_kept_subtree):
      continue
    existing = by_id.get(descriptor_id)
    if existing is None or tree_number < existing["tree_number"]:
      by_id[descriptor_id] = {
        "id": descriptor_id,
        "label": binding["label"]["value"],
        "tree_number": tree_number,
      }
  return sorted(by_id.values(), key=lambda d: d["tree_number"])


def fetch_entry_terms() -> dict[str, list[str]]:
  """Map each descriptor id to its MeSH entry terms (synonyms), preferred first."""
  # A descriptor's synonyms live on its Terms, reached through both the
  # preferred concept and any narrower concepts, so union both edges.
  query = f"""
  PREFIX meshv: <http://id.nlm.nih.gov/mesh/vocab#>
  PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
  SELECT ?d ?term WHERE {{
    ?d a meshv:TopicalDescriptor .
    ?d meshv:treeNumber ?tnum .
    ?tnum rdfs:label ?tn .
    FILTER(STRSTARTS(STR(?tn), "{TREE_ROOT}."))
    ?d (meshv:preferredConcept|meshv:concept) ?c .
    ?c meshv:term ?termObj .
    ?termObj meshv:prefLabel ?term .
  }}
  """
  terms: dict[str, list[str]] = defaultdict(list)
  seen: dict[str, set[str]] = defaultdict(set)
  for binding in _run_query(query):
    descriptor_id = _local_name(binding["d"]["value"])
    term = binding["term"]["value"]
    key = term.casefold()
    if key in seen[descriptor_id]:
      continue
    seen[descriptor_id].add(key)
    terms[descriptor_id].append(term)
  return terms


def _slug(label: str) -> str:
  """Build a stable, human-readable Topic id from the MeSH label."""
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


def build_toml(descriptors: list[dict[str, Any]], terms: dict[str, list[str]]) -> str:
  """Render the taxonomy seed as documented, human-editable TOML."""
  id_by_mesh = {d["id"]: _slug(d["label"]) for d in descriptors}
  tree_to_slug = {d["tree_number"]: id_by_mesh[d["id"]] for d in descriptors}

  lines = [
    "# Cardiology topic taxonomy (seed).",
    "#",
    "# The controlled vocabulary of Topics for the Coverage Matrix. Seeded from",
    f"# MeSH {MESH_VERSION} (Heart Diseases, tree {TREE_ROOT}) by",
    "# scripts/build_cardiology_taxonomy.py. This is a starting point: prune",
    "# Topics you do not care about and add your own. Every Topic keeps its MeSH",
    "# descriptor id and tree number as provenance; a hand-added Topic may set",
    '# mesh_id = "" and source = "manual".',
    "",
    "[meta]",
    'domain = "cardiology"',
    f'source = "MeSH {MESH_VERSION}"',
    'source_url = "https://meshb.nlm.nih.gov/treeView"',
    f'mesh_tree_root = "{TREE_ROOT}"',
    "",
  ]

  for descriptor in descriptors:
    slug = id_by_mesh[descriptor["id"]]
    tree_number = descriptor["tree_number"]
    aliases = [t for t in terms.get(descriptor["id"], []) if t != descriptor["label"]]
    parent_tree = tree_number.rsplit(".", 1)[0]
    parent = tree_to_slug.get(parent_tree)

    lines.append("[[topics]]")
    lines.append(f"id = {_toml_string(slug)}")
    lines.append(f"label = {_toml_string(descriptor['label'])}")
    lines.append(f"mesh_id = {_toml_string(descriptor['id'])}")
    lines.append(f"mesh_tree = {_toml_string(tree_number)}")
    if parent is not None:
      lines.append(f"parent = {_toml_string(parent)}")
    lines.append(f"aliases = {_toml_array(aliases)}")
    lines.append("")

  return "\n".join(lines)


def main() -> int:
  """Fetch MeSH descriptors and write the taxonomy seed."""
  descriptors = fetch_descriptors()
  if not descriptors:
    print("No descriptors returned from MeSH; aborting.", file=sys.stderr)
    return 1
  terms = fetch_entry_terms()
  OUTPUT.parent.mkdir(parents=True, exist_ok=True)
  OUTPUT.write_text(build_toml(descriptors, terms), encoding="utf-8")
  print(f"Wrote {len(descriptors)} Topics to {OUTPUT}")
  return 0


if __name__ == "__main__":
  sys.exit(main())
