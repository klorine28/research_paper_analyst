"""Shared fixtures: the committed cardiology Corpus that every stage is tested on."""

import json
import sys
from pathlib import Path
from typing import Any

import pytest

import research_gap_dashboard.env as env_module

FIXTURE_CORPUS_DIR = Path(__file__).parent / "fixtures" / "cardiology-corpus"


@pytest.fixture(autouse=True)
def _hermetic_env(monkeypatch: pytest.MonkeyPatch) -> None:
  """
  Keep the suite independent of any developer's `.env` (and its real API key).

  Entry points call `env.load_env()` to populate the environment from `.env`, so
  without this a local key would leak into tests \u2014 including the ones that
  assert the CLI fails loudly when no key is set. `load_env` resolves
  `load_dotenv` from this module's globals at call time, so neutralising it here
  intercepts every caller (CLI, dashboard) however it imported `load_env`. Tests
  stay offline and keyless (CODING_STANDARDS.md > Test with fixtures, not live
  services).
  """
  monkeypatch.setattr(env_module, "load_dotenv", lambda *args, **kwargs: False)
  # Behave as if the optional `embeddings` extra were absent, so no test loads
  # or downloads a model; tests of the embedder seam install a fake module.
  monkeypatch.setitem(sys.modules, "sentence_transformers", None)


class StubLlmClient:  # pylint: disable=too-few-public-methods
  """A keyless LlmClient returning a fixed result and counting its calls."""

  name = "stub"

  def __init__(self, result: dict[str, Any]):
    self.result = result
    self.calls = 0

  def complete(  # pylint: disable=unused-argument
    self,
    prompt: str,
    schema: dict[str, Any],
    *,
    prompt_version: str,
    tier: str = "default",
    refresh: bool = False,
  ) -> dict[str, Any]:
    """Count the call and return the canned result."""
    self.calls += 1
    return self.result


class ScriptedLlmClient(StubLlmClient):  # pylint: disable=too-few-public-methods
  """A keyless LlmClient returning its scripted results in turn, cycling."""

  def __init__(self, results: list[dict[str, Any]]):
    super().__init__(results[0])
    self.results = results

  def complete(
    self,
    prompt: str,
    schema: dict[str, Any],
    *,
    prompt_version: str,
    tier: str = "default",
    refresh: bool = False,
  ) -> dict[str, Any]:
    """Count the call and return the next scripted result."""
    result = self.results[self.calls % len(self.results)]
    self.calls += 1
    return result


@pytest.fixture(name="cardiology_corpus")
def cardiology_corpus_fixture() -> Path:
  """Return the root of the committed cardiology fixture Corpus."""
  return FIXTURE_CORPUS_DIR


@pytest.fixture(name="cardiology_provenance")
def cardiology_provenance_fixture(cardiology_corpus: Path) -> list[dict[str, Any]]:
  """Return the recorded licence and source of every fixture Paper."""
  return json.loads((cardiology_corpus / "provenance.json").read_text(encoding="utf-8"))
