"""Behavior of the topic-taxonomy loader and its validation reporting."""

from pathlib import Path

import pytest

from research_gap_dashboard.taxonomy import (
  CARDIOLOGY_SEED_PATH,
  SEED_PATHS,
  Axis,
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


@pytest.mark.parametrize(
  ("phrase", "expected"),
  [
    ("Transthoracic Echocardiography", "echocardiography"),
    ("Coronary Angiography", "coronary-angiography"),
    ("ECG", "electrocardiography"),
    ("Percutaneous Coronary Intervention", "percutaneous-coronary-intervention"),
    ("Extracorporeal Life Support", "extracorporeal-membrane-oxygenation"),
  ],
)
def test_shipped_cardiology_seed_holds_procedures_as_topics(phrase: str, expected: str):
  """Procedures are Topics (ADR 0011), each with MeSH E01/E02/E04 provenance."""
  taxonomy = load_taxonomy(CARDIOLOGY_SEED_PATH)

  resolved = taxonomy.resolve(phrase)
  assert resolved is not None
  assert resolved.id == expected
  assert resolved.mesh_id.startswith("D")
  assert resolved.mesh_tree is not None
  assert resolved.mesh_tree.startswith(("E01.", "E02.", "E04."))


def test_shipped_method_seed_holds_no_procedure():
  """No cardiology procedure Topic is also offered as a Method (ADR 0011)."""
  topics = load_taxonomy(CARDIOLOGY_SEED_PATH)
  methods = load_taxonomy(SEED_PATHS["method"])

  procedures = [t for t in topics.topics if (t.mesh_tree or "").startswith("E0")]
  assert procedures
  assert all(methods.resolve(p.label) is None for p in procedures)


def test_axis_defaults_to_topic(tmp_path: Path):
  """A file that names no axis is a Topic taxonomy."""
  taxonomy = load_taxonomy(write_taxonomy(tmp_path, VALID_TAXONOMY))

  assert taxonomy.meta.axis == "topic"


def test_unknown_axis_is_reported(tmp_path: Path):
  """An axis outside Topic, Method, Population, Dataset is a reported problem."""
  text = VALID_TAXONOMY.replace("[meta]\n", '[meta]\naxis = "outcome"\n')
  with pytest.raises(TaxonomyError) as caught:
    load_taxonomy(write_taxonomy(tmp_path, text))

  assert any("axis" in problem for problem in caught.value.problems)


@pytest.mark.parametrize(
  ("axis", "phrase", "expected"),
  [
    ("method", "RCT", "randomized-controlled-trial"),
    ("method", "Retrospective Study", "retrospective-studies"),
    ("population", "Elderly", "aged"),
    ("dataset", "Electronic Medical Records", "electronic-health-records"),
  ],
)
def test_shipped_axis_seeds_are_valid(axis: Axis, phrase: str, expected: str):
  """Each committed MeSH-curated axis seed loads, declares its axis, and resolves."""
  taxonomy = load_taxonomy(SEED_PATHS[axis])

  assert taxonomy.meta.axis == axis
  assert all(topic.mesh_id for topic in taxonomy.topics)
  resolved = taxonomy.resolve(phrase)
  assert resolved is not None
  assert resolved.id == expected


def test_taxonomy_doc_lists_the_procedure_seed():
  """`docs/taxonomy.md` lists every seeded procedure with its MeSH provenance."""
  doc = (CARDIOLOGY_SEED_PATH.parents[1] / "docs" / "taxonomy.md").read_text(
    encoding="utf-8"
  )
  topics = load_taxonomy(CARDIOLOGY_SEED_PATH).topics

  for topic in (t for t in topics if (t.mesh_tree or "").startswith("E0")):
    assert f"| `{topic.id}` | {topic.mesh_id} | {topic.mesh_tree} |" in doc


def test_alias_source_for_unknown_alias_is_reported(tmp_path: Path):
  """Provenance recorded for a phrase that is not an alias is a reported problem."""
  text = VALID_TAXONOMY.replace(
    'aliases = ["Myocardial Diseases"]',
    'aliases = ["Myocardial Diseases"]\n'
    'alias_sources = { "Myocardiopathies" = "MeSH entry term of D009202" }',
  )

  with pytest.raises(TaxonomyError, match="Myocardiopathies"):
    load_taxonomy(write_taxonomy(tmp_path, text))


def test_alias_source_for_an_alias_loads(tmp_path: Path):
  """An alias may carry where it came from."""
  text = VALID_TAXONOMY.replace(
    'aliases = ["Myocardial Diseases"]',
    'aliases = ["Myocardial Diseases"]\n'
    'alias_sources = { "myocardial diseases" = "MeSH entry term of D009202" }',
  )

  taxonomy = load_taxonomy(write_taxonomy(tmp_path, text))

  topic = taxonomy.topic_by_id("cardiomyopathies")
  assert topic is not None
  assert topic.alias_sources == {"myocardial diseases": "MeSH entry term of D009202"}
