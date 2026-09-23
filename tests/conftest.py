"""Shared fixtures: the committed cardiology Corpus that every stage is tested on."""

import json
from pathlib import Path
from typing import Any

import pytest

FIXTURE_CORPUS_DIR = Path(__file__).parent / "fixtures" / "cardiology-corpus"


@pytest.fixture(name="cardiology_corpus")
def cardiology_corpus_fixture() -> Path:
  """Return the root of the committed cardiology fixture Corpus."""
  return FIXTURE_CORPUS_DIR


@pytest.fixture(name="cardiology_provenance")
def cardiology_provenance_fixture(cardiology_corpus: Path) -> list[dict[str, Any]]:
  """Return the recorded licence and source of every fixture Paper."""
  return json.loads((cardiology_corpus / "provenance.json").read_text(encoding="utf-8"))
