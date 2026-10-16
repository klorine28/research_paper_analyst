"""
Behavior of the embedder seam and the merge hints it gives limitation grouping.

Vectors are recorded from the default model (`NeuML/pubmedbert-base-embeddings`
at its pinned revision) into a fixture, so no test loads or downloads a model;
`conftest` makes the `embeddings` extra look absent for every test.
"""

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from conftest import StubLlmClient
from test_detect import _write_corpus, _fact

from research_gap_dashboard import cli
from research_gap_dashboard.cli import EXIT_ERROR, EXIT_OK, main
from research_gap_dashboard.detect import detect_corpus, read_candidate_gaps
from research_gap_dashboard.embeddings import (
  DEFAULT_EMBEDDING_MODEL,
  DEFAULT_EMBEDDING_REVISION,
  DEFAULT_MERGE_THRESHOLD,
  EmbeddingConfig,
  EmbeddingConfigError,
  RecordedEmbedder,
  SentenceTransformerEmbedder,
  build_embedder,
)
from research_gap_dashboard.extract import Extraction, ExtractionFields
from research_gap_dashboard.limitations import collect_statements, merge_hints

_RECORDED = json.loads(
  (
    Path(__file__).parent / "fixtures" / "embeddings" / "limitation-statements.json"
  ).read_text(encoding="utf-8")
)

_POWER_LIMITED = "The small sample size limited statistical power."
_POWER_REDUCED = "The small sample size reduced statistical power."
_FEW_PATIENTS = "Our study was limited by the small number of patients enrolled."
_SEX = "Future studies should examine sex differences in outcomes."
_FOLLOW_UP = "Long-term follow-up data were not available."


def _recorded_embedder() -> RecordedEmbedder:
  """Replay the vectors recorded from the default model."""
  return RecordedEmbedder(
    _RECORDED["vectors"],
    model_id=_RECORDED["model_id"],
    revision=_RECORDED["revision"],
  )


def _extractions() -> list[Extraction]:
  """
  Three Papers; recorded cosines decide which statements look alike.

  `alpha` says the same thing twice (0.97, but within one Paper); `beta`
  repeats it (cross-Paper, 0.97); `gamma` words it differently (0.64 and 0.61,
  just under the default threshold); the rest sit far below it.
  """
  return [
    Extraction(
      citation_key="alpha",
      fields=ExtractionFields(
        limitations=[_fact(_POWER_LIMITED, "a"), _fact(_POWER_REDUCED, "b")],
        future_work=[_fact(_SEX, "c")],
      ),
    ),
    Extraction(
      citation_key="beta",
      fields=ExtractionFields(
        limitations=[_fact(_POWER_REDUCED, "d"), _fact(_FOLLOW_UP, "e")]
      ),
    ),
    Extraction(
      citation_key="gamma",
      fields=ExtractionFields(limitations=[_fact(_FEW_PATIENTS, "f")]),
    ),
  ]


def _hint_pairs(threshold: float) -> set[tuple[str, str]]:
  """Return the hinted statement-id pairs over the fixture at `threshold`."""
  hints = merge_hints(
    collect_statements(_extractions()), _recorded_embedder(), threshold
  )
  return {hint.statement_ids for hint in hints}


def test_only_close_statements_from_different_papers_become_hints() -> None:
  """A cross-Paper pair above the threshold is hinted; a same-Paper pair is not."""
  assert _hint_pairs(DEFAULT_MERGE_THRESHOLD) == {
    ("alpha:limitations[0]", "beta:limitations[0]"),
    ("alpha:limitations[1]", "beta:limitations[0]"),
  }


def test_a_lower_threshold_admits_looser_pairs() -> None:
  """The threshold is a setting: at 0.6 the differently worded Paper joins."""
  pairs = _hint_pairs(0.6)

  assert ("alpha:limitations[0]", "gamma:limitations[0]") in pairs
  assert ("beta:limitations[0]", "gamma:limitations[0]") in pairs
  assert not any("future_work" in left + right for left, right in pairs)


def _corpus(tmp_path: Path) -> Path:
  """Write a Corpus whose Extractions carry the embedding fixture."""
  return _write_corpus(
    tmp_path,
    {"alpha": [], "beta": [], "gamma": []},
    extractions=_extractions(),
    years={"alpha": 2018, "beta": 2018, "gamma": 2018},
  )


def test_detect_records_the_hints_and_the_model_behind_them(tmp_path: Path) -> None:
  """The artifact names the embedding model, revision, threshold and every hint."""
  detect_corpus(
    _corpus(tmp_path), StubLlmClient({"groups": []}), embedder=_recorded_embedder()
  )

  report = read_candidate_gaps(tmp_path)
  assert report.embedding is not None
  assert report.embedding.model_id == DEFAULT_EMBEDDING_MODEL
  assert report.embedding.revision == DEFAULT_EMBEDDING_REVISION
  assert report.limitation_merge_threshold == DEFAULT_MERGE_THRESHOLD
  assert {hint.statement_ids for hint in report.limitation_merge_hints} == {
    ("alpha:limitations[0]", "beta:limitations[0]"),
    ("alpha:limitations[1]", "beta:limitations[0]"),
  }
  assert all(hint.cosine >= 0.65 for hint in report.limitation_merge_hints)


def test_without_an_embedder_grouping_runs_llm_only(tmp_path: Path) -> None:
  """No embedder: grouping still runs, and the artifact records no hints."""
  grouping = {
    "groups": [
      {
        "label": "Small sample",
        "statement_ids": ["alpha:limitations[0]", "gamma:limitations[0]"],
      }
    ]
  }

  report = detect_corpus(_corpus(tmp_path), StubLlmClient(grouping))

  assert report.embedding is None
  assert report.limitation_merge_threshold is None
  assert report.limitation_merge_hints == []
  merged = [g for g in report.limitation_groups if len(g.source_citation_keys) > 1]
  assert [g.source_citation_keys for g in merged] == [["alpha", "gamma"]]


def test_build_embedder_returns_none_without_the_extra() -> None:
  """With `sentence-transformers` absent the embedder is skipped, not an error."""
  assert build_embedder(EmbeddingConfig()) is None


def test_default_embedder_loads_the_configured_model_and_revision(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  """The default embedder pins the configured revision and returns plain floats."""
  loaded: list[tuple[str, str]] = []

  class FakeSentenceTransformer:  # pylint: disable=too-few-public-methods
    """Stand-in for `sentence_transformers.SentenceTransformer`."""

    def __init__(self, model_id: str, revision: str):
      loaded.append((model_id, revision))

    def encode(self, texts: list[str], **_: Any) -> list[list[float]]:
      """Encode each text as its length, twice."""
      return [[len(text), len(text)] for text in texts]

  monkeypatch.setitem(
    sys.modules,
    "sentence_transformers",
    SimpleNamespace(SentenceTransformer=FakeSentenceTransformer),
  )
  config = EmbeddingConfig(model_id="org/model", revision="abc123")

  embedder = build_embedder(config)

  assert isinstance(embedder, SentenceTransformerEmbedder)
  assert loaded == [("org/model", "abc123")]
  assert (embedder.model_id, embedder.revision) == ("org/model", "abc123")
  assert embedder.embed(["ab", "abc"]) == [[2.0, 2.0], [3.0, 3.0]]
  assert embedder.embed([]) == []


def test_default_embedder_encodes_each_text_only_once(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  """Chat embeds the whole fact pool per question; a repeat text isn't re-encoded."""
  encoded: list[str] = []

  class FakeSentenceTransformer:  # pylint: disable=too-few-public-methods
    """Stand-in for `sentence_transformers.SentenceTransformer`."""

    def __init__(self, *_: Any, **__: Any):
      pass

    def encode(self, texts: list[str], **_: Any) -> list[list[float]]:
      """Record what is encoded; encode each text as its length."""
      encoded.extend(texts)
      return [[float(len(text))] for text in texts]

  monkeypatch.setitem(
    sys.modules,
    "sentence_transformers",
    SimpleNamespace(SentenceTransformer=FakeSentenceTransformer),
  )
  embedder = build_embedder(EmbeddingConfig())
  assert embedder is not None

  first = embedder.embed(["question one", "a fact", "a fact"])
  second = embedder.embed(["question two!", "a fact"])

  assert first == [[12.0], [6.0], [6.0]]
  assert second == [[13.0], [6.0]]
  assert encoded == ["question one", "a fact", "question two!"]


def test_recorded_embedder_fails_on_an_unrecorded_text() -> None:
  """A text with no recorded vector is an error, never a made-up vector."""
  with pytest.raises(LookupError):
    _recorded_embedder().embed(["Never recorded."])


def test_config_reads_model_revision_and_threshold_from_the_environment() -> None:
  """Every setting has a default and an environment override."""
  assert EmbeddingConfig.from_env({}) == EmbeddingConfig()
  config = EmbeddingConfig.from_env(
    {
      "EMBEDDING_MODEL": "org/model",
      "EMBEDDING_REVISION": "abc123",
      "LIMITATION_MERGE_THRESHOLD": "0.8",
    }
  )
  assert (config.model_id, config.revision, config.merge_threshold) == (
    "org/model",
    "abc123",
    0.8,
  )


@pytest.mark.parametrize("raw", ["high", "0", "1.5", "-0.2"])
def test_an_invalid_threshold_is_a_configuration_error(raw: str) -> None:
  """A threshold outside (0, 1] fails with an explanation, not a silent default."""
  with pytest.raises(EmbeddingConfigError):
    EmbeddingConfig.from_env({"LIMITATION_MERGE_THRESHOLD": raw})


def test_cli_detect_passes_the_configured_embedder_and_threshold(
  tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
  """The detect command injects the embedder and records the configured threshold."""
  root = _corpus(tmp_path)
  monkeypatch.setattr(cli, "build_llm_client", lambda **_: StubLlmClient({}))
  monkeypatch.setattr(cli, "build_embedder", lambda _config: _recorded_embedder())
  monkeypatch.setenv("LIMITATION_MERGE_THRESHOLD", "0.6")

  assert main(["detect", str(root)]) == EXIT_OK
  report = read_candidate_gaps(root)
  assert report.limitation_merge_threshold == 0.6
  assert len(report.limitation_merge_hints) == 5


def test_cli_detect_without_the_extra_still_writes_the_artifact(
  tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
  """With the extra absent the command succeeds and records no embedding model."""
  root = _corpus(tmp_path)
  monkeypatch.setattr(cli, "build_llm_client", lambda **_: StubLlmClient({}))

  assert main(["detect", str(root)]) == EXIT_OK
  assert read_candidate_gaps(root).embedding is None


def test_cli_detect_rejects_an_invalid_threshold(
  tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
  """A malformed threshold stops the run before any LLM call."""
  root = _corpus(tmp_path)
  monkeypatch.setattr(cli, "build_llm_client", lambda **_: StubLlmClient({}))
  monkeypatch.setenv("LIMITATION_MERGE_THRESHOLD", "lots")

  assert main(["detect", str(root)]) == EXIT_ERROR
