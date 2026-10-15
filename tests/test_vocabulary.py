"""Behavior of MeSH alias suggestions and the per-Corpus Alias Overlay."""

import json
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest

from research_gap_dashboard.aggregate import load_pipeline_taxonomies
from research_gap_dashboard.cli import main
from research_gap_dashboard.taxonomy import Taxonomy, load_taxonomy
from research_gap_dashboard.vocabulary import (
  AliasProvenance,
  AliasSuggestion,
  AliasOverlayError,
  MeshVocabulary,
  apply_alias_overlay,
  approve_alias,
  promote_alias,
  propose_aliases,
  read_alias_overlay,
  reject_alias,
  suggest_mesh_aliases,
  write_alias_overlay,
)

TAXONOMY = """
[meta]
domain = "cardiology"
source = "MeSH 2025"

[[topics]]
id = "cardiomyopathies"
label = "Cardiomyopathies"
mesh_id = "D009202"
mesh_tree = "C14.280.238"
aliases = ["Myocardial Diseases"]

[[topics]]
id = "takotsubo-cardiomyopathy"
label = "Takotsubo Cardiomyopathy"
mesh_id = "D054549"
mesh_tree = "C14.280.238.906"
parent = "cardiomyopathies"
aliases = ["Broken Heart Syndrome"]

[[topics]]
id = "hand-added"
label = "Hand Added"
mesh_id = ""
aliases = []
"""

# Recorded shapes of the NLM MeSH lookup/details and SPARQL JSON responses.
DETAILS: dict[str, dict[str, Any]] = {
  "D009202": {
    "terms": [
      {"label": "Cardiomyopathies", "preferred": True},
      {"label": "Myocardial Diseases", "preferred": False},
      {"label": "Myocardiopathies", "preferred": False},
      # Already the label of another Topic: suggesting it would be ambiguous.
      {"label": "Takotsubo Cardiomyopathy", "preferred": False},
    ]
  },
  "D054549": {
    "terms": [
      {"label": "Takotsubo Cardiomyopathy", "preferred": True},
      {"label": "Broken Heart Syndrome", "preferred": False},
      {"label": "Stress Cardiomyopathy", "preferred": False},
      {"label": "Apical Ballooning Syndrome", "preferred": False},
    ]
  },
}

CHILDREN: dict[str, list[tuple[str, str, str]]] = {
  "C14.280.238": [
    # Its own Topic already: not an alias.
    ("D054549", "Takotsubo Cardiomyopathy", "C14.280.238.906"),
    ("D002311", "Cardiomyopathy, Dilated", "C14.280.238.070"),
  ],
  "C14.280.238.906": [],
}


def fake_fetch(url: str) -> dict[str, Any] | None:
  """Answer MeSH lookup and SPARQL URLs from the recorded responses."""
  parsed = urlparse(url)
  params = parse_qs(parsed.query)
  if parsed.path.endswith("/lookup/details"):
    return DETAILS.get(params["descriptor"][0], {"terms": []})
  if parsed.path.endswith("/sparql"):
    query = params["query"][0]
    tree = next(t for t in CHILDREN if f"/mesh/{t}>" in query)
    return {
      "results": {
        "bindings": [
          {
            "d": {"value": f"http://id.nlm.nih.gov/mesh/{mesh_id}"},
            "label": {"value": label},
            "tn": {"value": tree_number},
          }
          for mesh_id, label, tree_number in CHILDREN[tree]
        ]
      }
    }
  raise AssertionError(f"unexpected URL {url}")


@pytest.fixture(name="taxonomy_path")
def fixture_taxonomy_path(tmp_path: Path) -> Path:
  """Write the shared taxonomy file and return its path."""
  path = tmp_path / "topics.toml"
  path.write_text(TAXONOMY, encoding="utf-8")
  return path


@pytest.fixture(name="taxonomy")
def fixture_taxonomy(taxonomy_path: Path) -> Taxonomy:
  """Load the shared taxonomy."""
  return load_taxonomy(taxonomy_path)


def suggestions(taxonomy: Taxonomy):
  """Suggest MeSH aliases for the taxonomy from the recorded responses."""
  return suggest_mesh_aliases(taxonomy, MeshVocabulary(fetch=fake_fetch))


def test_mesh_suggestions_carry_provenance(taxonomy: Taxonomy):
  """Entry terms and child descriptors are suggested with where they came from."""
  found = {(s.category_id, s.alias): s.provenance for s in suggestions(taxonomy)}

  assert set(found) == {
    ("cardiomyopathies", "Myocardiopathies"),
    ("cardiomyopathies", "Cardiomyopathy, Dilated"),
    ("takotsubo-cardiomyopathy", "Stress Cardiomyopathy"),
    ("takotsubo-cardiomyopathy", "Apical Ballooning Syndrome"),
  }
  entry = found[("cardiomyopathies", "Myocardiopathies")]
  assert (entry.source, entry.mesh_id) == ("mesh_entry_term", "D009202")
  child = found[("cardiomyopathies", "Cardiomyopathy, Dilated")]
  assert (child.source, child.mesh_id, child.mesh_tree) == (
    "mesh_child_descriptor",
    "D002311",
    "C14.280.238.070",
  )


def test_suggestions_start_proposed_and_do_not_change_matching(
  tmp_path: Path, taxonomy: Taxonomy
):
  """A proposed alias is logged in the overlay but maps nothing until approved."""
  overlay = propose_aliases(read_alias_overlay(tmp_path), suggestions(taxonomy))
  write_alias_overlay(tmp_path, overlay)

  reread = read_alias_overlay(tmp_path)
  assert {entry.status for entry in reread.entries} == {"proposed"}
  [applied] = apply_alias_overlay([taxonomy], reread)
  assert applied.resolve("Myocardiopathies") is None


def test_proposing_twice_keeps_one_entry_and_respects_rejection(taxonomy: Taxonomy):
  """Re-running the suggestion never re-offers an alias a human already decided."""
  overlay = propose_aliases(read_alias_overlay(Path("/nonexistent")), [])
  overlay = propose_aliases(overlay, suggestions(taxonomy))
  overlay = reject_alias(overlay, "topic", "cardiomyopathies", "Myocardiopathies")
  overlay = propose_aliases(overlay, suggestions(taxonomy))

  matching = [e for e in overlay.entries if e.alias == "Myocardiopathies"]
  assert [e.status for e in matching] == ["rejected"]
  assert len(overlay.entries) == 4


def test_approved_alias_applies_and_can_be_reversed(taxonomy: Taxonomy):
  """Approval makes the alias map; reversing it removes it again, logged."""
  overlay = propose_aliases(read_alias_overlay(Path("/nonexistent")), [])
  overlay = propose_aliases(overlay, suggestions(taxonomy))
  overlay = approve_alias(
    overlay, [taxonomy], "topic", "cardiomyopathies", "myocardiopathies"
  )

  [applied] = apply_alias_overlay([taxonomy], overlay)
  topic = applied.resolve("Myocardiopathies")
  assert topic is not None and topic.id == "cardiomyopathies"
  # The shared taxonomy object is untouched.
  assert taxonomy.resolve("Myocardiopathies") is None

  overlay = reject_alias(overlay, "topic", "cardiomyopathies", "Myocardiopathies")
  [applied] = apply_alias_overlay([taxonomy], overlay)
  assert applied.resolve("Myocardiopathies") is None
  [entry] = [e for e in overlay.entries if e.alias == "Myocardiopathies"]
  assert [d.action for d in entry.history] == ["proposed", "approved", "rejected"]


def test_approving_an_alias_another_category_owns_is_refused(taxonomy: Taxonomy):
  """The overlay can never make a phrase ambiguous across categories."""
  # Logged before another category came to own the phrase.
  stale = AliasSuggestion(
    axis="topic",
    category_id="cardiomyopathies",
    alias="Broken Heart Syndrome",
    provenance=AliasProvenance(
      source="mesh_entry_term", mesh_id="D009202", mesh_label="Cardiomyopathies"
    ),
  )
  overlay = propose_aliases(read_alias_overlay(Path("/nonexistent")), [stale])
  with pytest.raises(AliasOverlayError, match="takotsubo-cardiomyopathy"):
    approve_alias(
      overlay, [taxonomy], "topic", "cardiomyopathies", "Broken Heart Syndrome"
    )


def test_approving_an_unknown_suggestion_is_refused(taxonomy: Taxonomy):
  """Only a logged suggestion can be approved, so every alias has provenance."""
  overlay = propose_aliases(read_alias_overlay(Path("/nonexistent")), [])
  with pytest.raises(AliasOverlayError, match="no suggested alias"):
    approve_alias(overlay, [taxonomy], "topic", "cardiomyopathies", "Made Up")


def test_overlay_applies_only_to_its_own_corpus(tmp_path: Path, taxonomy_path: Path):
  """The aggregate stage reads a Corpus's overlay; another Corpus is unaffected."""
  corpus_a, corpus_b = tmp_path / "a", tmp_path / "b"
  taxonomy = load_taxonomy(taxonomy_path)
  overlay = propose_aliases(read_alias_overlay(corpus_a), suggestions(taxonomy))
  overlay = approve_alias(
    overlay, [taxonomy], "topic", "cardiomyopathies", "Myocardiopathies"
  )
  write_alias_overlay(corpus_a, overlay)

  [in_a] = load_pipeline_taxonomies([taxonomy_path], corpus=corpus_a)
  [in_b] = load_pipeline_taxonomies([taxonomy_path], corpus=corpus_b)
  [shared] = load_pipeline_taxonomies([taxonomy_path])

  assert in_a.resolve("Myocardiopathies") is not None
  assert in_b.resolve("Myocardiopathies") is None
  assert shared.resolve("Myocardiopathies") is None


def test_promotion_writes_alias_and_provenance_to_the_shared_file(
  tmp_path: Path, taxonomy_path: Path
):
  """Promoting is a separate act that edits the shared file, which stays valid."""
  taxonomy = load_taxonomy(taxonomy_path)
  overlay = propose_aliases(read_alias_overlay(tmp_path), suggestions(taxonomy))
  overlay = approve_alias(
    overlay, [taxonomy], "topic", "cardiomyopathies", "Cardiomyopathy, Dilated"
  )

  overlay = promote_alias(
    overlay, taxonomy_path, "topic", "cardiomyopathies", "Cardiomyopathy, Dilated"
  )

  promoted = load_taxonomy(taxonomy_path)
  topic = promoted.topic_by_id("cardiomyopathies")
  assert topic is not None
  assert "Cardiomyopathy, Dilated" in topic.aliases
  assert "D002311" in topic.alias_sources["Cardiomyopathy, Dilated"]
  [entry] = [e for e in overlay.entries if e.alias == "Cardiomyopathy, Dilated"]
  assert entry.status == "promoted"


def test_only_an_approved_alias_can_be_promoted(tmp_path: Path, taxonomy_path: Path):
  """A merely proposed alias never reaches the shared taxonomy."""
  taxonomy = load_taxonomy(taxonomy_path)
  overlay = propose_aliases(read_alias_overlay(tmp_path), suggestions(taxonomy))
  before = taxonomy_path.read_text(encoding="utf-8")

  with pytest.raises(AliasOverlayError, match="approved"):
    promote_alias(
      overlay, taxonomy_path, "topic", "cardiomyopathies", "Myocardiopathies"
    )
  assert taxonomy_path.read_text(encoding="utf-8") == before


def test_cli_approve_and_list(tmp_path: Path, taxonomy_path: Path):
  """The CLI approves a logged suggestion and records it in the Corpus overlay."""
  taxonomy = load_taxonomy(taxonomy_path)
  write_alias_overlay(
    tmp_path, propose_aliases(read_alias_overlay(tmp_path), suggestions(taxonomy))
  )

  code = main(
    [
      "aliases",
      "approve",
      str(tmp_path),
      "topic",
      "cardiomyopathies",
      "Myocardiopathies",
      "--taxonomy",
      str(taxonomy_path),
    ]
  )

  assert code == 0
  raw = json.loads((tmp_path / "judgments" / "alias_overlay.json").read_text())
  statuses = {e["alias"]: e["status"] for e in raw["entries"]}
  assert statuses["Myocardiopathies"] == "approved"
  assert main(["aliases", "list", str(tmp_path)]) == 0
