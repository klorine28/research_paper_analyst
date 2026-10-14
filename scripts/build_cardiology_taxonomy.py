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

Procedures and treatments are Topics too (ADR 0011): after the disease subtree
the seed lists a curated set of cardiac diagnostic and therapeutic procedures
from MeSH's procedure branches (E01 Diagnosis, E02 Therapeutics, E04 Surgical
Procedures). Those are picks, not a subtree, so each label is resolved against
MeSH by exact match; an unresolvable label aborts the build rather than writing
a Topic with invented provenance.
"""

import sys
import time
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Any

import httpx

ENDPOINT = "https://id.nlm.nih.gov/mesh/sparql"
LOOKUP = "https://id.nlm.nih.gov/mesh/lookup"
RECORD = "https://id.nlm.nih.gov/mesh"
# The NLM endpoint drops idle keep-alive connections; retry with backoff.
RETRIES = 5
CONTACT = "research-gap-dashboard (https://github.com/klorine28/research_paper_analyst)"
MESH_VERSION = "2025"

# Heart Diseases and everything under it; we keep the first level plus the
# Cardiomyopathies subtree, which is where the Takotsubo fixture corpus lives.
TREE_ROOT = "C14.280"
KEEP_SUBTREES = ("C14.280.238",)  # Cardiomyopathies

OUTPUT = Path(__file__).resolve().parents[1] / "taxonomies" / "cardiology.toml"

# The MeSH procedure branches a procedure Topic's tree number is taken from.
PROCEDURE_BRANCHES = ("E01.", "E02.", "E04.")

# Curated cardiac diagnostic and therapeutic procedures (exact MeSH labels).
PROCEDURES: tuple[str, ...] = (
  # E01: diagnostic.
  "Heart Function Tests",
  "Cardiac Imaging Techniques",
  "Echocardiography",
  "Echocardiography, Doppler",
  "Echocardiography, Transesophageal",
  "Echocardiography, Stress",
  "Electrocardiography",
  "Electrocardiography, Ambulatory",
  "Coronary Angiography",
  "Cardiac Catheterization",
  "Computed Tomography Angiography",
  "Magnetic Resonance Imaging, Cine",
  "Myocardial Perfusion Imaging",
  "Positron-Emission Tomography",
  "Exercise Test",
  # E02: therapeutic.
  "Thrombolytic Therapy",
  "Cardiac Pacing, Artificial",
  "Cardiac Resynchronization Therapy",
  "Catheter Ablation",
  "Electric Countershock",
  "Cardiopulmonary Resuscitation",
  "Extracorporeal Membrane Oxygenation",
  "Cardiac Rehabilitation",
  "Left Atrial Appendage Closure",
  # E04: surgical and percutaneous.
  "Assisted Circulation",
  "Intra-Aortic Balloon Pumping",
  "Percutaneous Coronary Intervention",
  "Angioplasty, Balloon, Coronary",
  "Coronary Artery Bypass",
  "Heart Transplantation",
  "Heart Valve Prosthesis Implantation",
  "Transcatheter Aortic Valve Replacement",
  "Pericardiocentesis",
)


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


def resolve_procedure(client: httpx.Client, label: str) -> dict[str, Any]:
  """Resolve one exact MeSH procedure label to its id, tree numbers, and terms."""
  matches = _get(client, f"{LOOKUP}/descriptor", label=label, match="exact")
  if len(matches) != 1:
    raise LookupError(f"MeSH label {label!r} matched {len(matches)} descriptors")
  descriptor_id = _local_name(matches[0]["resource"])

  record = _get(client, f"{RECORD}/{descriptor_id}.json")
  raw_trees = record.get("treeNumber", [])
  trees = [raw_trees] if isinstance(raw_trees, str) else list(raw_trees)
  tree_numbers = sorted(
    tree
    for tree in (_local_name(t) for t in trees)
    if tree.startswith(PROCEDURE_BRANCHES)
  )
  if not tree_numbers:
    raise LookupError(f"MeSH label {label!r} has no E01/E02/E04 tree number")

  details = _get(client, f"{LOOKUP}/details", descriptor=descriptor_id)
  return {
    "id": descriptor_id,
    "label": matches[0]["label"],
    "tree_numbers": tree_numbers,
    "terms": [term["label"] for term in details.get("terms", [])],
  }


def fetch_procedures() -> list[dict[str, Any]]:
  """Resolve every curated procedure label against MeSH, in the curated order."""
  with httpx.Client(headers={"User-Agent": CONTACT}, timeout=60.0) as client:
    return [resolve_procedure(client, label) for label in PROCEDURES]


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


def _key(name: str) -> str:
  """Fold a phrase the way the taxonomy loader matches aliases."""
  return " ".join(name.split()).casefold()


def build_toml(
  descriptors: list[dict[str, Any]],
  terms: dict[str, list[str]],
  procedures: list[dict[str, Any]],
) -> str:
  """Render the taxonomy seed as documented, human-editable TOML."""
  id_by_mesh = {d["id"]: _slug(d["label"]) for d in descriptors}
  tree_to_slug = {d["tree_number"]: id_by_mesh[d["id"]] for d in descriptors}

  lines = [
    "# Cardiology topic taxonomy (seed).",
    "#",
    "# The controlled vocabulary of Topics for the Coverage Matrix. Seeded from",
    f"# MeSH {MESH_VERSION} (Heart Diseases, tree {TREE_ROOT}, plus curated cardiac "
    "procedures",
    "# from E01/E02/E04, ADR 0011) by scripts/build_cardiology_taxonomy.py.",
    "# This is a starting point: prune Topics you do not care about and add your",
    "# own. Every Topic keeps its MeSH descriptor id and tree number as provenance;",
    '# a hand-added Topic may set mesh_id = "" and source = "manual".',
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

  lines.extend(_procedure_lines(descriptors, terms, procedures))
  return "\n".join(lines)


def _procedure_lines(
  descriptors: list[dict[str, Any]],
  terms: dict[str, list[str]],
  procedures: list[dict[str, Any]],
) -> list[str]:
  """Render the curated procedure Topics, after the disease subtree."""
  # The loader rejects a phrase that maps onto two Topics, so a procedure entry
  # term already claimed by a disease or by another procedure is dropped.
  owners: dict[str, set[str]] = {}
  for descriptor in descriptors:
    for name in (_slug(descriptor["label"]), descriptor["label"]):
      owners.setdefault(_key(name), set()).add(descriptor["id"])
    for term in terms.get(descriptor["id"], []):
      owners.setdefault(_key(term), set()).add(descriptor["id"])
  for procedure in procedures:
    for name in (_slug(procedure["label"]), procedure["label"], *procedure["terms"]):
      owners.setdefault(_key(name), set()).add(procedure["id"])

  slug_by_tree = {
    tree: _slug(p["label"]) for p in procedures for tree in p["tree_numbers"]
  }
  lines = [
    "# Diagnostic and therapeutic procedures (ADR 0011): curated from MeSH",
    "# E01 (Diagnosis), E02 (Therapeutics), and E04 (Surgical Procedures).",
    "# Procedures are Topics, never Methods; prune any you do not need.",
    "",
  ]
  for procedure in procedures:
    parent = None
    for tree in procedure["tree_numbers"]:
      parent = parent or slug_by_tree.get(tree.rsplit(".", 1)[0])
    seen = {_key(procedure["label"])}
    aliases: list[str] = []
    for term in procedure["terms"]:
      key = _key(term)
      if key in seen or len(owners[key]) > 1:
        continue
      seen.add(key)
      aliases.append(term)

    lines.append("[[topics]]")
    lines.append(f"id = {_toml_string(_slug(procedure['label']))}")
    lines.append(f"label = {_toml_string(procedure['label'])}")
    lines.append(f"mesh_id = {_toml_string(procedure['id'])}")
    lines.append(f"mesh_tree = {_toml_string(procedure['tree_numbers'][0])}")
    if parent is not None:
      lines.append(f"parent = {_toml_string(parent)}")
    lines.append(f"aliases = {_toml_array(aliases)}")
    lines.append("")
  return lines


def main() -> int:
  """Fetch MeSH descriptors and write the taxonomy seed."""
  descriptors = fetch_descriptors()
  if not descriptors:
    print("No descriptors returned from MeSH; aborting.", file=sys.stderr)
    return 1
  terms = fetch_entry_terms()
  try:
    procedures = fetch_procedures()
  except LookupError as error:
    print(f"procedures: {error}; aborting.", file=sys.stderr)
    return 1
  OUTPUT.parent.mkdir(parents=True, exist_ok=True)
  OUTPUT.write_text(build_toml(descriptors, terms, procedures), encoding="utf-8")
  print(
    f"Wrote {len(descriptors)} disease and {len(procedures)} procedure Topics "
    f"to {OUTPUT}"
  )
  return 0


if __name__ == "__main__":
  sys.exit(main())
