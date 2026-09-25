"""Behavior of the aggregate stage: free-text facts in, taxonomy-normalized out."""

import shutil
from pathlib import Path
from typing import Any

import pytest

from conftest import StubLlmClient

from research_gap_dashboard import cli
from research_gap_dashboard.aggregate import (
  aggregate_corpus,
  aggregate_paper,
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
from research_gap_dashboard.taxonomy import Taxonomy, load_taxonomy

_TAXONOMY_TOML = """
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


def _taxonomy(tmp_path: Path) -> Taxonomy:
  """Load a small two-Topic taxonomy from a temp file."""
  path = tmp_path / "taxonomy.toml"
  path.write_text(_TAXONOMY_TOML, encoding="utf-8")
  return load_taxonomy(path)


def _extraction(citation_key: str = "hanna2019") -> Extraction:
  """Build one Paper's Extraction with a couple of grounded facts."""
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
      key_findings=[
        ExtractedFact(
          statement="Cardiac failure was common.",
          evidence=Evidence(passage="cardiac failure", section="results"),
        )
      ],
    ),
  )


_GROUNDED_PROPOSAL: dict[str, Any] = {
  "assignments": [
    {
      "original_term": "broken heart syndrome",
      "category_id": "takotsubo-cardiomyopathy",
    },
    {"original_term": "cardiac failure", "category_id": "heart-failure"},
  ],
  "unmapped": [],
}


def test_assignment_records_original_term_and_category(tmp_path: Path) -> None:
  """Each mapping keeps the Paper's phrase and the Topic it was placed on."""
  facts = aggregate_paper(
    _extraction(), _taxonomy(tmp_path), StubLlmClient(_GROUNDED_PROPOSAL)
  )

  by_term = {a.original_term: a for a in facts.assignments}
  assert by_term["broken heart syndrome"].category_id == "takotsubo-cardiomyopathy"
  assert by_term["broken heart syndrome"].category_label == "Takotsubo Cardiomyopathy"
  assert by_term["cardiac failure"].category_id == "heart-failure"
  assert facts.unmapped == []


def test_invented_category_becomes_unmapped_not_a_new_topic(tmp_path: Path) -> None:
  """A proposed id absent from the taxonomy is surfaced, never invented as a Topic."""
  hallucinated = {
    "assignments": [
      {"original_term": "myocarditis", "category_id": "myocarditis-not-in-taxonomy"}
    ],
    "unmapped": [],
  }

  facts = aggregate_paper(
    _extraction(), _taxonomy(tmp_path), StubLlmClient(hallucinated)
  )

  assert facts.assignments == []
  assert len(facts.unmapped) == 1
  assert facts.unmapped[0].original_term == "myocarditis"
  assert "not in the taxonomy" in facts.unmapped[0].reason


def test_a_phrase_alias_resolves_to_its_topic(tmp_path: Path) -> None:
  """A category named by label or alias, not id, still resolves to its Topic."""
  by_label = {
    "assignments": [
      {"original_term": "stress cardiomyopathy", "category_id": "Stress Cardiomyopathy"}
    ],
    "unmapped": [],
  }

  facts = aggregate_paper(_extraction(), _taxonomy(tmp_path), StubLlmClient(by_label))

  assert facts.assignments[0].category_id == "takotsubo-cardiomyopathy"


def test_unmappable_terms_are_surfaced_not_dropped(tmp_path: Path) -> None:
  """A phrase the model could not place is kept for taxonomy editing."""
  with_miss = {
    "assignments": [
      {
        "original_term": "broken heart syndrome",
        "category_id": "takotsubo-cardiomyopathy",
      }
    ],
    "unmapped": [{"original_term": "arrhythmia", "reason": "no matching category"}],
  }

  facts = aggregate_paper(_extraction(), _taxonomy(tmp_path), StubLlmClient(with_miss))

  assert [u.original_term for u in facts.unmapped] == ["arrhythmia"]


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
  report = aggregate_corpus(
    extracted_corpus, StubLlmClient(_GROUNDED_PROPOSAL), _taxonomy(tmp_path)
  )

  assert {n.citation_key for n in report.normalized} == {"hanna2019", "lee2018"}
  assert report.failures == []
  assert report.taxonomy_domain == "cardiology"

  reread = read_normalized_facts(extracted_corpus)
  assert reread.prompt_version == "aggregate-v1"
  assert (extracted_corpus / "artifacts" / "normalized_facts.json").is_file()


def test_reruns_hit_the_cache_and_make_no_new_calls(
  extracted_corpus: Path, tmp_path: Path
) -> None:
  """A second aggregate run serves from the disk cache without calling the LLM."""
  inner = StubLlmClient(_GROUNDED_PROPOSAL)
  client = CachingLlmClient(inner, DiskCache(extracted_corpus / ".llm-cache"))
  taxonomy = _taxonomy(tmp_path)

  aggregate_corpus(extracted_corpus, client, taxonomy)
  first = inner.calls
  assert first == 2

  aggregate_corpus(extracted_corpus, client, taxonomy)
  assert inner.calls == first


def test_cli_aggregate_requires_an_api_key(extracted_corpus: Path, monkeypatch) -> None:
  """Without an API key the aggregate command fails loudly rather than degrading."""
  monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

  assert main(["aggregate", str(extracted_corpus)]) == cli.EXIT_ERROR
