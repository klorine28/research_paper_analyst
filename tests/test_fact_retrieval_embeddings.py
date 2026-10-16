"""
Behavior of chat retrieval when an embedder is injected (issue #119).

With the `embeddings` extra installed, retrieval ranks facts by how close they
are in meaning to the question, so a paraphrase that shares no word with a fact
still finds it. Without an embedder it is #97's keyword retrieval, unchanged.
Vectors were recorded from the default model (`NeuML/pubmedbert-base-embeddings`
at its pinned revision) into `fixtures/embeddings/chat-retrieval.json`, so no
test loads or downloads a model.
"""

import json
from pathlib import Path

import pytest
from conftest import StubLlmClient

from research_gap_dashboard.analytics import (
  CorpusPaper,
  GroundingContext,
  answer_question,
  build_chat_embedder,
)
from research_gap_dashboard.embeddings import RecordedEmbedder
from research_gap_dashboard.fact_retrieval import ExtractedFact, retrieve_facts

_RECORDED = json.loads(
  (Path(__file__).parent / "fixtures" / "embeddings" / "chat-retrieval.json").read_text(
    encoding="utf-8"
  )
)

_OEDEMA = (
  "Cardiac magnetic resonance imaging showed myocardial oedema in the apical segments."
)
_HRV = "Heart rate variability was lower in patients than in controls."
_REGISTRY = "Patients were enrolled from an international multicentre registry."
_MORTALITY = "Mortality at one year was 5.6%."
_STRESS = "Emotional stress preceded the event in most women."
_WOMEN = "Only women over 50 years were included."

_POOL = [
  ExtractedFact(citation_key="alpha2019", field="methods", statement=_OEDEMA),
  ExtractedFact(citation_key="alpha2019", field="key_findings", statement=_HRV),
  ExtractedFact(citation_key="beta2020", field="populations", statement=_REGISTRY),
  ExtractedFact(citation_key="beta2020", field="key_findings", statement=_MORTALITY),
  ExtractedFact(citation_key="gamma2021", field="key_findings", statement=_STRESS),
  ExtractedFact(citation_key="gamma2021", field="populations", statement=_WOMEN),
]


def _embedder() -> RecordedEmbedder:
  """Replay the vectors recorded from the default model."""
  return RecordedEmbedder(
    _RECORDED["vectors"],
    model_id=_RECORDED["model_id"],
    revision=_RECORDED["revision"],
  )


def _statements(question: str, *, embedded: bool) -> list[str]:
  """Retrieve over the pool and return the statements, most relevant first."""
  embedder = _embedder() if embedded else None
  return [
    fact.statement for fact in retrieve_facts(question, _POOL, {}, embedder=embedder)
  ]


def test_a_paraphrase_sharing_no_word_retrieves_the_fact_by_meaning():
  """'What triggered the episodes?' finds the stress fact; keywords find nothing."""
  question = "What triggered the episodes?"

  assert _statements(question, embedded=False) == []
  assert _statements(question, embedded=True) == [_STRESS]


def test_a_paraphrase_in_other_vocabulary_finds_the_imaging_fact():
  """'inflammation of the myocardium' reaches the MRI oedema fact."""
  question = "Which scans revealed inflammation of the myocardium?"

  assert _OEDEMA not in _statements(question, embedded=False)
  assert _statements(question, embedded=True)[0] == _OEDEMA


def test_similarity_lifts_the_fact_that_answers_above_ones_sharing_a_word():
  """
  Similarity lifts the fact that answers above facts merely sharing a word.

  Keywords rank the mortality fact last (it shares only 'year').
  """
  question = "How many patients died within a year?"
  keyword = _statements(question, embedded=False)
  embedded = _statements(question, embedded=True)

  assert keyword[-1] == _MORTALITY
  assert sorted(embedded) == sorted(keyword)
  assert embedded.index(_MORTALITY) < embedded.index(_REGISTRY)
  assert embedded.index(_MORTALITY) < embedded.index(_HRV)


def test_an_out_of_corpus_question_still_retrieves_nothing():
  """Nothing is close enough to a question the Corpus can't answer."""
  assert _statements("What is the capital of France?", embedded=True) == []


def test_without_an_embedder_keyword_retrieval_is_unchanged():
  """No embedder is #97's keyword retrieval: same facts, same order."""
  question = "How many patients died within a year?"

  assert _statements(question, embedded=False) == [
    fact.statement for fact in retrieve_facts(question, _POOL, {})
  ]


def test_chat_grounds_a_paraphrase_in_the_fact_it_retrieved_by_meaning():
  """The chat prompt carries the fact the embedder found; citations stay Corpus-only."""
  context = GroundingContext(
    papers=[
      CorpusPaper(citation_key=key) for key in ("alpha2019", "beta2020", "gamma2021")
    ],
    fact_pool=_POOL,
  )
  client = StubLlmClient(
    {"answer": "Emotional stress.", "citations": ["gamma2021", "invented2099"]}
  )

  grounded = context.for_question("What triggered the episodes?", embedder=_embedder())
  answer = answer_question(
    "What triggered the episodes?", context, client, embedder=_embedder()
  )

  assert [fact.statement for fact in grounded.facts] == [_STRESS]
  assert answer.citations == ["gamma2021"]
  assert answer.dropped_citations == ["invented2099"]


def test_chat_uses_keywords_when_the_extra_is_absent():
  """Without `sentence-transformers` the chat has no embedder and nothing fails."""
  assert build_chat_embedder() is None


def test_an_invalid_embedding_setting_falls_back_to_keywords(
  monkeypatch: pytest.MonkeyPatch,
):
  """A bad embedding setting never takes the chat down; it uses keywords."""
  monkeypatch.setenv("LIMITATION_MERGE_THRESHOLD", "high")

  assert build_chat_embedder() is None
