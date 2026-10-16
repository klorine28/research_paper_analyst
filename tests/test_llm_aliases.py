"""Behavior of LLM alias suggestions for thin categories (ADR 0010)."""

import json
from pathlib import Path
from typing import Any

import pytest

from research_gap_dashboard import cli
from research_gap_dashboard.llm import ModelTier
from research_gap_dashboard.llm_aliases import (
  ALIAS_PROMPT_VERSION,
  AliasSuggestionModel,
  suggest_llm_aliases,
  thin_categories,
)
from research_gap_dashboard.taxonomy import Taxonomy, load_taxonomy
from research_gap_dashboard.vocabulary import (
  AliasOverlay,
  AliasProvenance,
  AliasSuggestion,
  apply_alias_overlay,
  approve_alias,
  propose_aliases,
  read_alias_overlay,
  reject_alias,
)

TAXONOMY = """
[meta]
domain = "cardiology"
source = "MeSH 2025, trimmed for these tests"

[[topics]]
id = "cardiomyopathies"
label = "Cardiomyopathies"
mesh_id = "D009202"
aliases = ["Myocardial Diseases", "Myocardiopathies"]

[[topics]]
id = "takotsubo-cardiomyopathy"
label = "Takotsubo Cardiomyopathy"
mesh_id = "D054549"
parent = "cardiomyopathies"
aliases = ["Broken Heart Syndrome"]

[[topics]]
id = "myocarditis"
label = "Myocarditis"
mesh_id = "D009205"
aliases = []
"""

# What the fake model answers, per category label in the prompt.
ANSWERS: dict[str, list[str]] = {
  "Takotsubo Cardiomyopathy": [
    "Stress Cardiomyopathy",
    "Apical Ballooning Syndrome",
    # Another category's alias: never offered (it would be ambiguous).
    "Myocardial Diseases",
    # Already this category's alias, in another case.
    "broken heart syndrome",
    "  ",
  ],
  "Myocarditis": ["Inflammatory Cardiomyopathy", "Stress Cardiomyopathy"],
  "Cardiomyopathies": ["Should Never Be Asked"],
}


class FakeClient:  # pylint: disable=too-few-public-methods
  """Answers alias prompts from `ANSWERS` and records which categories it was asked."""

  name = "fake"

  def __init__(self) -> None:
    self.asked: list[str] = []
    self.versions: list[str] = []

  def complete(  # pylint: disable=unused-argument
    self,
    prompt: str,
    schema: dict[str, Any],
    *,
    prompt_version: str,
    tier: ModelTier = "default",
    refresh: bool = False,
  ) -> dict[str, Any]:
    """Return the canned aliases for the category the prompt names."""
    label = next(label for label in ANSWERS if f'Category: "{label}"' in prompt)
    self.asked.append(label)
    self.versions.append(prompt_version)
    return {"aliases": ANSWERS[label]}


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


def model(client: FakeClient) -> AliasSuggestionModel:
  """Wrap the fake client as the suggestion model, with a recorded model id."""
  return AliasSuggestionModel(client=client, model="fake-model", tier="cheap")


def test_only_thin_categories_trigger_a_call(taxonomy: Taxonomy):
  """A category with enough aliases is never sent to the LLM."""
  client = FakeClient()

  suggest_llm_aliases(taxonomy, AliasOverlay(), model(client))

  assert sorted(client.asked) == ["Myocarditis", "Takotsubo Cardiomyopathy"]
  assert [t.id for t in thin_categories(taxonomy, AliasOverlay())] == [
    "takotsubo-cardiomyopathy",
    "myocarditis",
  ]


def test_approved_overlay_aliases_count_toward_thinness(taxonomy: Taxonomy):
  """A category the Corpus's approved aliases already filled is not thin."""
  mesh = AliasSuggestion(
    axis="topic",
    category_id="takotsubo-cardiomyopathy",
    alias="Stress Cardiomyopathy",
    provenance=AliasProvenance(source="mesh_entry_term", mesh_id="D054549"),
  )
  overlay = propose_aliases(AliasOverlay(), [mesh])
  # Proposed only: it maps nothing yet, so the category is still thin.
  assert "takotsubo-cardiomyopathy" in {
    t.id for t in thin_categories(taxonomy, overlay)
  }

  overlay = approve_alias(
    overlay, [taxonomy], "topic", "takotsubo-cardiomyopathy", "Stress Cardiomyopathy"
  )
  client = FakeClient()
  suggest_llm_aliases(taxonomy, overlay, model(client))

  assert client.asked == ["Myocarditis"]


def test_suggestions_are_proposed_with_provenance_and_map_nothing(
  taxonomy: Taxonomy,
):
  """Nothing the LLM says enters the taxonomy until a human approves it."""
  client = FakeClient()
  suggestions = suggest_llm_aliases(taxonomy, AliasOverlay(), model(client))
  overlay = propose_aliases(AliasOverlay(), suggestions)

  found = {(s.category_id, s.alias) for s in suggestions}
  # "Stress Cardiomyopathy" was offered to two categories: ambiguous, dropped.
  assert found == {
    ("takotsubo-cardiomyopathy", "Apical Ballooning Syndrome"),
    ("myocarditis", "Inflammatory Cardiomyopathy"),
  }
  for suggestion in suggestions:
    provenance = suggestion.provenance
    assert provenance.source == "llm_suggestion"
    assert provenance.model == "fake-model"
    assert provenance.prompt_version == ALIAS_PROMPT_VERSION
    assert "fake-model" in provenance.describe()
  assert {e.status for e in overlay.entries} == {"proposed"}
  [applied] = apply_alias_overlay([taxonomy], overlay)
  assert applied.resolve("Inflammatory Cardiomyopathy") is None

  overlay = approve_alias(
    overlay, [taxonomy], "topic", "myocarditis", "Inflammatory Cardiomyopathy"
  )
  [applied] = apply_alias_overlay([taxonomy], overlay)
  topic = applied.resolve("Inflammatory Cardiomyopathy")
  assert topic is not None and topic.id == "myocarditis"


def test_a_category_is_asked_once(taxonomy: Taxonomy):
  """Once a category has LLM suggestions logged, it is never asked again."""
  first = FakeClient()
  overlay = propose_aliases(
    AliasOverlay(), suggest_llm_aliases(taxonomy, AliasOverlay(), model(first))
  )
  overlay = reject_alias(overlay, "topic", "myocarditis", "Inflammatory Cardiomyopathy")

  second = FakeClient()
  again = suggest_llm_aliases(taxonomy, overlay, model(second))

  assert not second.asked
  assert not again
  # The prompt version recorded with the cache entry pins the model, too.
  assert all("fake-model" in version for version in first.versions)


def test_cli_logs_llm_suggestions_as_proposed(
  tmp_path: Path, taxonomy_path: Path, monkeypatch: pytest.MonkeyPatch
):
  """`aliases suggest-llm` writes proposals only; approval is a separate step."""
  client = FakeClient()
  monkeypatch.setattr(cli, "_alias_suggestion_model", lambda _arguments: model(client))
  corpus = tmp_path / "corpus"

  code = cli.main(
    ["aliases", "suggest-llm", str(corpus), "--taxonomy", str(taxonomy_path)]
  )

  assert code == 0
  assert sorted(client.asked) == ["Myocarditis", "Takotsubo Cardiomyopathy"]
  raw = json.loads((corpus / "judgments" / "alias_overlay.json").read_text())
  assert {e["status"] for e in raw["entries"]} == {"proposed"}
  assert read_alias_overlay(corpus).entries[0].provenance.source == "llm_suggestion"


def test_cli_without_an_api_key_fails_clearly(
  tmp_path: Path, taxonomy_path: Path, monkeypatch: pytest.MonkeyPatch
):
  """No key means no suggestions, and nothing is written."""
  monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
  monkeypatch.setattr(
    "research_gap_dashboard.llm.LlmConfig.from_env",
    classmethod(lambda cls, env=None: cls()),
  )
  corpus = tmp_path / "corpus"

  code = cli.main(
    ["aliases", "suggest-llm", str(corpus), "--taxonomy", str(taxonomy_path)]
  )

  assert code != 0
  assert not (corpus / "judgments" / "alias_overlay.json").exists()
