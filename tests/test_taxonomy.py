"""Behavior of the topic-taxonomy loader and its validation reporting."""

from pathlib import Path

import pytest

from research_gap_dashboard.taxonomy import (
  CARDIOLOGY_SEED_PATH,
  TaxonomyError,
  load_taxonomy,
)

VALID_TAXONOMY = """
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
aliases = ["Broken Heart Syndrome", "Stress Cardiomyopathy"]
"""


def write_taxonomy(root: Path, text: str) -> Path:
  """Write taxonomy TOML to a temp file and return its path."""
  path = root / "taxonomy.toml"
  path.write_text(text, encoding="utf-8")
  return path


def test_valid_taxonomy_loads(tmp_path: Path):
  """A well-formed file yields a taxonomy with its topics and metadata."""
  taxonomy = load_taxonomy(write_taxonomy(tmp_path, VALID_TAXONOMY))

  assert taxonomy.meta.domain == "cardiology"
  assert [topic.id for topic in taxonomy.topics] == [
    "cardiomyopathies",
    "takotsubo-cardiomyopathy",
  ]


def test_alias_resolves_to_topic(tmp_path: Path):
  """An extracted phrase maps onto its Topic via label, id, or alias."""
  taxonomy = load_taxonomy(write_taxonomy(tmp_path, VALID_TAXONOMY))

  resolved = taxonomy.resolve("broken heart syndrome")

  assert resolved is not None
  assert resolved.id == "takotsubo-cardiomyopathy"


def test_unknown_phrase_resolves_to_none(tmp_path: Path):
  """A phrase outside the vocabulary does not resolve to a Topic."""
  taxonomy = load_taxonomy(write_taxonomy(tmp_path, VALID_TAXONOMY))

  assert taxonomy.resolve("quantum entanglement") is None


def test_missing_file_is_reported(tmp_path: Path):
  """Loading a path that does not exist names the missing file."""
  with pytest.raises(TaxonomyError) as caught:
    load_taxonomy(tmp_path / "nope.toml")

  assert any("does not exist" in problem for problem in caught.value.problems)


def test_invalid_toml_is_reported(tmp_path: Path):
  """A syntactically broken file is reported as unparseable, not crashed on."""
  with pytest.raises(TaxonomyError) as caught:
    load_taxonomy(write_taxonomy(tmp_path, "this is = = not toml"))

  assert any("parse" in problem.lower() for problem in caught.value.problems)


def test_empty_taxonomy_is_reported(tmp_path: Path):
  """A file with no topics is rejected: an empty vocabulary detects nothing."""
  text = '[meta]\ndomain = "cardiology"\nsource = "MeSH 2025"\n'
  with pytest.raises(TaxonomyError) as caught:
    load_taxonomy(write_taxonomy(tmp_path, text))

  assert any("no topics" in problem.lower() for problem in caught.value.problems)


def test_missing_required_topic_field_is_reported(tmp_path: Path):
  """A topic missing its MeSH provenance is reported with its position."""
  text = (
    '[meta]\ndomain = "cardiology"\nsource = "MeSH 2025"\n\n'
    '[[topics]]\nid = "heart-failure"\nlabel = "Heart Failure"\n'
  )
  with pytest.raises(TaxonomyError) as caught:
    load_taxonomy(write_taxonomy(tmp_path, text))

  assert any("mesh_id" in problem for problem in caught.value.problems)


def test_unknown_topic_field_is_reported(tmp_path: Path):
  """A typo'd key is flagged so a hand-edit does not silently lose data."""
  text = (
    '[meta]\ndomain = "cardiology"\nsource = "MeSH 2025"\n\n'
    '[[topics]]\nid = "heart-failure"\nlabel = "Heart Failure"\n'
    'mesh_id = "D006333"\nalias = ["cardiac failure"]\n'
  )
  with pytest.raises(TaxonomyError) as caught:
    load_taxonomy(write_taxonomy(tmp_path, text))

  assert any("alias" in problem for problem in caught.value.problems)


def test_duplicate_ids_are_reported(tmp_path: Path):
  """Two topics sharing an id would make the vocabulary ambiguous."""
  text = (
    '[meta]\ndomain = "cardiology"\nsource = "MeSH 2025"\n\n'
    '[[topics]]\nid = "dup"\nlabel = "One"\nmesh_id = "D1"\n\n'
    '[[topics]]\nid = "dup"\nlabel = "Two"\nmesh_id = "D2"\n'
  )
  with pytest.raises(TaxonomyError) as caught:
    load_taxonomy(write_taxonomy(tmp_path, text))

  assert any("dup" in problem and "id" in problem for problem in caught.value.problems)


def test_ambiguous_alias_is_reported(tmp_path: Path):
  """The same alias on two topics cannot map an extracted phrase to one Topic."""
  text = (
    '[meta]\ndomain = "cardiology"\nsource = "MeSH 2025"\n\n'
    '[[topics]]\nid = "a"\nlabel = "Alpha"\nmesh_id = "D1"\naliases = ["shared"]\n\n'
    '[[topics]]\nid = "b"\nlabel = "Beta"\nmesh_id = "D2"\naliases = ["Shared"]\n'
  )
  with pytest.raises(TaxonomyError) as caught:
    load_taxonomy(write_taxonomy(tmp_path, text))

  assert any("shared" in problem.lower() for problem in caught.value.problems)


def test_unresolved_parent_is_reported(tmp_path: Path):
  """A parent reference to a missing Topic is a broken hierarchy."""
  text = (
    '[meta]\ndomain = "cardiology"\nsource = "MeSH 2025"\n\n'
    '[[topics]]\nid = "child"\nlabel = "Child"\nmesh_id = "D1"\n'
    'parent = "ghost"\n'
  )
  with pytest.raises(TaxonomyError) as caught:
    load_taxonomy(write_taxonomy(tmp_path, text))

  assert any("ghost" in problem for problem in caught.value.problems)


def test_parent_cycle_is_reported(tmp_path: Path):
  """A parent cycle has no root and would loop any tree walk."""
  text = (
    '[meta]\ndomain = "cardiology"\nsource = "MeSH 2025"\n\n'
    '[[topics]]\nid = "a"\nlabel = "A"\nmesh_id = "D1"\nparent = "b"\n\n'
    '[[topics]]\nid = "b"\nlabel = "B"\nmesh_id = "D2"\nparent = "a"\n'
  )
  with pytest.raises(TaxonomyError) as caught:
    load_taxonomy(write_taxonomy(tmp_path, text))

  assert any("cycle" in problem.lower() for problem in caught.value.problems)


def test_shipped_cardiology_seed_is_valid():
  """The committed MeSH-seeded cardiology taxonomy loads and validates."""
  taxonomy = load_taxonomy(CARDIOLOGY_SEED_PATH)

  assert taxonomy.meta.domain == "cardiology"
  assert len(taxonomy.topics) >= 20
  assert taxonomy.resolve("Broken Heart Syndrome") is not None
