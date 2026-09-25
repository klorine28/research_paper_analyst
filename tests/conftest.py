"""Shared fixtures: the committed cardiology Corpus that every stage is tested on."""

import json
from pathlib import Path
from typing import Any

import pytest

FIXTURE_CORPUS_DIR = Path(__file__).parent / "fixtures" / "cardiology-corpus"


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
