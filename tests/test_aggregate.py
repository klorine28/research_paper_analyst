"""Behavior of the aggregate stage: free-text facts in, axis-normalized out."""

import shutil
from pathlib import Path
from typing import Any

import pytest

from conftest import ScriptedLlmClient, StubLlmClient

from research_gap_dashboard import cli
from research_gap_dashboard.aggregate import (
  aggregate_corpus,
  aggregate_paper,
  load_pipeline_taxonomies,
  read_normalized_facts,
)
from research_gap_dashboard.cli import main
from research_gap_dashboard.extract import (
  Evidence,
  ExtractedFact,
  Extraction,
  ExtractionFields,
  ExtractReport,
)
from research_gap_dashboard.ingest import ingest_corpus
from research_gap_dashboard.llm import CachingLlmClient, DiskCache
from research_gap_dashboard.taxonomy import Taxonomy, TaxonomyError, load_taxonomy

_TOPIC_TOML = """
[meta]
domain = "cardiology"
source = "MeSH 2025"

[[topics]]
id = "takotsubo-cardiomyopathy"
label = "Takotsubo Cardiomyopathy"
mesh_id = "D054549"
aliases = ["Broken Heart Syndrome", "Stress Cardiomyopathy"]

[[topics]]
id = "heart-failure"
label = "Heart Failure"
mesh_id = "D006333"
aliases = ["Cardiac Failure"]
"""

_METHOD_TOML = """
[meta]
axis = "method"
domain = "biomedical"
source = "MeSH 2025"

[[topics]]
id = "retrospective-studies"
label = "Retrospective Studies"
mesh_id = "D012189"
aliases = ["Retrospective Study"]
"""

_DATASET_TOML = """
[meta]
axis = "dataset"
domain = "biomedical"
source = "MeSH 2025"

[[topics]]
id = "registries"
label = "Registries"
mesh_id = "D012042"
"""


def _load(tmp_path: Path, name: str, text: str) -> Taxonomy:
  """Load one taxonomy from TOML written to a temp file."""
  path = tmp_path / f"{name}.toml"
  path.write_text(text, encoding="utf-8")
  return load_taxonomy(path)


def _taxonomies(tmp_path: Path) -> list[Taxonomy]:
  """Load a small Topic taxonomy and a small Method taxonomy."""
  return [
    _load(tmp_path, "topic", _TOPIC_TOML),
    _load(tmp_path, "method", _METHOD_TOML),
  ]


def _extraction(citation_key: str = "hanna2019") -> Extraction:
  """Build one Paper's Extraction with grounded topic and method facts."""
  return Extraction(
    citation_key=citation_key,
    fields=ExtractionFields(
      research_question=[
        ExtractedFact(
          # Vary the statement per Paper so prompts (and cache keys) differ.
          statement=f"Outcomes in broken heart syndrome for {citation_key}.",
          evidence=Evidence(passage="broken heart syndrome", section="abstract"),
        )
      ],
      methods=[
        ExtractedFact(
          statement=f"A retrospective chart review in {citation_key}.",
          evidence=Evidence(passage="we retrospectively reviewed", section="methods"),
        )
      ],
    ),
  )


_TOPIC_PROPOSAL: dict[str, Any] = {
  "assignments": [
    {
      "original_term": "broken heart syndrome",
      "fact_ref": "research_question[0]",
      "category_id": "takotsubo-cardiomyopathy",
    }
  ],
  "unmapped": [],
}

_METHOD_PROPOSAL: dict[str, Any] = {
  "assignments": [
    {
      "original_term": "retrospective chart review",
      "fact_ref": "methods[0]",
      "category_id": "retrospective-studies",
    }
  ],
  "unmapped": [],
}


def _grounded_client() -> ScriptedLlmClient:
  """Build a fake LLM answering the Topic axis, then the Method axis, per Paper."""
  return ScriptedLlmClient([_TOPIC_PROPOSAL, _METHOD_PROPOSAL])


def test_each_axis_records_term_category_and_evidence(tmp_path: Path) -> None:
  """Every mapping keeps its axis, phrase, category, and the fact's Evidence."""
  facts = aggregate_paper(_extraction(), _taxonomies(tmp_path), _grounded_client())

  by_axis = {a.axis: a for a in facts.assignments}
  topic = by_axis["topic"]
  assert topic.original_term == "broken heart syndrome"
  assert topic.category_id == "takotsubo-cardiomyopathy"
  assert topic.category_label == "Takotsubo Cardiomyopathy"
  assert topic.evidence.passage == "broken heart syndrome"

  method = by_axis["method"]
  assert method.category_id == "retrospective-studies"
  assert method.fact_ref == "methods[0]"
  assert method.evidence.passage == "we retrospectively reviewed"
  assert facts.category_ids("method") == ["retrospective-studies"]
  assert facts.unmapped == []


def test_invented_category_becomes_unmapped_not_a_new_category(tmp_path: Path) -> None:
  """A proposed id absent from the taxonomy is surfaced, never invented."""
  hallucinated = {
    "assignments": [
      {
        "original_term": "myocarditis",
        "fact_ref": "research_question[0]",
        "category_id": "myocarditis-not-in-taxonomy",
      }
    ],
    "unmapped": [],
  }
  topic_only = [_load(tmp_path, "topic", _TOPIC_TOML)]

  facts = aggregate_paper(_extraction(), topic_only, StubLlmClient(hallucinated))

  assert facts.assignments == []
  assert len(facts.unmapped) == 1
  assert facts.unmapped[0].axis == "topic"
  assert facts.unmapped[0].original_term == "myocarditis"
  assert "not in the taxonomy" in facts.unmapped[0].reason


def test_invented_fact_reference_becomes_unmapped(tmp_path: Path) -> None:
  """An assignment citing a fact the Paper lacks never gains an Evidence link."""
  wrong_fact = {
    "assignments": [
      {
        "original_term": "retrospective chart review",
        "fact_ref": "methods[7]",
        "category_id": "retrospective-studies",
      }
    ],
    "unmapped": [],
  }
  method_only = [_load(tmp_path, "method", _METHOD_TOML)]

  facts = aggregate_paper(_extraction(), method_only, StubLlmClient(wrong_fact))

  assert facts.assignments == []
  assert facts.unmapped[0].axis == "method"
  assert "methods[7]" in facts.unmapped[0].reason


def test_a_category_alias_resolves_to_its_category(tmp_path: Path) -> None:
  """A category named by label or alias, not id, still resolves."""
  by_alias = {
    "assignments": [
      {
        "original_term": "stress cardiomyopathy",
        "fact_ref": "research_question[0]",
        "category_id": "Stress Cardiomyopathy",
      }
    ],
    "unmapped": [],
  }
  topic_only = [_load(tmp_path, "topic", _TOPIC_TOML)]

  facts = aggregate_paper(_extraction(), topic_only, StubLlmClient(by_alias))

  assert facts.assignments[0].category_id == "takotsubo-cardiomyopathy"


def test_unmappable_terms_are_surfaced_with_their_axis(tmp_path: Path) -> None:
  """A phrase the model could not place is kept, per axis, for taxonomy editing."""
  with_miss = {
    "assignments": [],
    "unmapped": [{"original_term": "chart review", "reason": "no matching design"}],
  }
  method_only = [_load(tmp_path, "method", _METHOD_TOML)]

  facts = aggregate_paper(_extraction(), method_only, StubLlmClient(with_miss))

  assert [(u.axis, u.original_term) for u in facts.unmapped] == [
    ("method", "chart review")
  ]


def test_an_axis_with_no_extracted_facts_makes_no_call(tmp_path: Path) -> None:
  """A Paper with no dataset facts is not sent to the LLM for the Dataset axis."""
  client = StubLlmClient(_TOPIC_PROPOSAL)
  taxonomies = [
    _load(tmp_path, "topic", _TOPIC_TOML),
    _load(tmp_path, "dataset", _DATASET_TOML),
  ]

  facts = aggregate_paper(_extraction(), taxonomies, client)

  assert client.calls == 1
  assert facts.category_ids("dataset") == []


def test_two_taxonomies_on_one_axis_are_rejected(tmp_path: Path) -> None:
  """Normalizing onto two vocabularies for the same axis is refused."""
  twice = [_load(tmp_path, "a", _TOPIC_TOML), _load(tmp_path, "b", _TOPIC_TOML)]

  with pytest.raises(TaxonomyError, match="'topic' axis"):
    aggregate_paper(_extraction(), twice, _grounded_client())


def test_shipped_seeds_load_one_taxonomy_per_axis() -> None:
  """The default pipeline taxonomies cover all four Coverage Matrix axes."""
  axes = {taxonomy.meta.axis for taxonomy in load_pipeline_taxonomies()}

  assert axes == {"topic", "method", "population", "dataset"}


@pytest.fixture(name="extracted_corpus")
def extracted_corpus_fixture(cardiology_corpus: Path, tmp_path: Path) -> Path:
  """Copy the fixture Corpus, ingest it, and write a two-Paper Extractions artifact."""
  target = tmp_path / "corpus"
  shutil.copytree(cardiology_corpus, target)
  ingest_corpus(target)
  report = ExtractReport(
    corpus_root=target,
    prompt_version="extract-v1",
    tier="default",
    extractions=[_extraction("hanna2019"), _extraction("lee2018")],
    failures=[],
  )
  artifacts = target / "artifacts"
  artifacts.mkdir(parents=True, exist_ok=True)
  (artifacts / "extractions.json").write_text(
    report.model_dump_json(indent=2), encoding="utf-8"
  )
  return target


def test_aggregate_corpus_writes_artifact_for_every_paper(
  extracted_corpus: Path, tmp_path: Path
) -> None:
  """Every extracted Paper becomes a NormalizedFacts entry in the one artifact."""
  report = aggregate_corpus(extracted_corpus, _grounded_client(), _taxonomies(tmp_path))

  assert {n.citation_key for n in report.normalized} == {"hanna2019", "lee2018"}
  assert report.failures == []
  assert [(t.axis, t.domain) for t in report.taxonomies] == [
    ("topic", "cardiology"),
    ("method", "biomedical"),
  ]

  reread = read_normalized_facts(extracted_corpus)
  assert reread.prompt_version == "aggregate-v2"
  assert reread.normalized[0].category_ids("method") == ["retrospective-studies"]


def test_a_misfiring_axis_fails_the_paper_and_names_the_axis(
  extracted_corpus: Path, tmp_path: Path
) -> None:
  """A Paper whose Method axis never validates is recorded, not half-normalized."""
  garbage = {"assignments": [{"not": "an assignment"}], "unmapped": []}
  client = ScriptedLlmClient([_TOPIC_PROPOSAL, garbage, garbage, garbage])

  report = aggregate_corpus(extracted_corpus, client, _taxonomies(tmp_path))

  assert report.normalized == []
  assert {(f.citation_key, f.axis) for f in report.failures} == {
    ("hanna2019", "method"),
    ("lee2018", "method"),
  }


def test_reruns_hit_the_cache_and_make_no_new_calls(
  extracted_corpus: Path, tmp_path: Path
) -> None:
  """A second aggregate run serves from the disk cache without calling the LLM."""
  inner = _grounded_client()
  client = CachingLlmClient(inner, DiskCache(extracted_corpus / ".llm-cache"))
  taxonomies = _taxonomies(tmp_path)

  aggregate_corpus(extracted_corpus, client, taxonomies)
  first = inner.calls
  assert first == 4

  aggregate_corpus(extracted_corpus, client, taxonomies)
  assert inner.calls == first


def test_cli_aggregate_requires_an_api_key(extracted_corpus: Path, monkeypatch) -> None:
  """Without an API key the aggregate command fails loudly rather than degrading."""
  monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

  assert main(["aggregate", str(extracted_corpus)]) == cli.EXIT_ERROR


def test_cli_aggregate_rejects_two_files_for_one_axis(
  extracted_corpus: Path, tmp_path: Path
) -> None:
  """Passing two Topic taxonomies is a clear error before any LLM call."""
  path = tmp_path / "topic.toml"
  path.write_text(_TOPIC_TOML, encoding="utf-8")

  argv = ["aggregate", str(extracted_corpus), "--taxonomy", str(path)]
  assert main([*argv, "--taxonomy", str(path)]) == cli.EXIT_ERROR
