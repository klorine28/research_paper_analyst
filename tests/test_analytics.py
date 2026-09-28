"""
Behavior of the Conversational Analytics and narrative-summary machinery.

The acceptance criterion is that grounding is enforced, not hoped for: chat and
the narrative summary may cite only Papers in the Corpus. These tests drive the
generation through the offline fake client and assert that a model citation that
does not resolve to a Corpus Paper is dropped and flagged, while grounded ones
survive, and that the prompt actually carries the Corpus so answers are grounded.
"""

from typing import Any

from conftest import StubLlmClient

from research_gap_dashboard.analytics import (
  CorpusPaper,
  GapFact,
  GroundingContext,
  answer_question,
  summarize_gaps,
)


def _context(*, with_gap: bool = False) -> GroundingContext:
  """Build a two-Paper Corpus context, optionally carrying a Candidate Gap."""
  gaps = (
    [
      GapFact(
        gap_type="Coverage Gap",
        title="No RCTs on Takotsubo",
        explanation="No Paper studies Takotsubo with an RCT.",
        confidence="medium",
        source_citation_keys=["hanna2019"],
      )
    ]
    if with_gap
    else []
  )
  return GroundingContext(
    papers=[
      CorpusPaper(citation_key="hanna2019", title="Takotsubo", year=2019),
      CorpusPaper(citation_key="lee2021", title="Heart failure", year=2021),
    ],
    gaps=gaps,
  )


class _CapturingStub(StubLlmClient):  # pylint: disable=too-few-public-methods
  """A stub that records the last prompt it was asked to complete."""

  def __init__(self, result: dict[str, Any]):
    super().__init__(result)
    self.prompt = ""

  def complete(self, prompt, schema, *, prompt_version, tier="default", refresh=False):
    self.prompt = prompt
    return super().complete(
      prompt, schema, prompt_version=prompt_version, tier=tier, refresh=refresh
    )


def test_context_allow_list_is_the_corpus_citation_keys():
  """The grounding context cites against exactly the Corpus's Papers."""
  assert _context().allowed_keys == {"hanna2019", "lee2021"}


def test_chat_answer_drops_a_citation_outside_the_corpus():
  """A cited key that is not a Corpus Paper is flagged, never returned as a citation."""
  client = StubLlmClient(
    {"answer": "No RCTs on Takotsubo.", "citations": ["hanna2019", "ghost2020"]}
  )

  result = answer_question("Any RCTs?", _context(), client)

  assert result.citations == ["hanna2019"]
  assert result.dropped_citations == ["ghost2020"]


def test_chat_prompt_carries_the_corpus_history_and_question():
  """The prompt lists the Corpus citation keys, prior turns, and the question."""
  client = _CapturingStub({"answer": "Answer", "citations": []})

  answer_question(
    "What is missing?",
    _context(),
    client,
    history=["user: earlier question", "assistant: earlier answer"],
  )

  assert "hanna2019" in client.prompt
  assert "What is missing?" in client.prompt
  assert "earlier question" in client.prompt


def test_narrative_summary_keeps_only_grounded_citations():
  """The narrative summary drops any citation that is not a Corpus Paper."""
  client = StubLlmClient(
    {
      "paragraphs": ["The Corpus is silent on RCTs for Takotsubo.", "  "],
      "citations": ["hanna2019", "invented1999"],
    }
  )

  result = summarize_gaps(_context(with_gap=True), client)

  assert result.paragraphs == ["The Corpus is silent on RCTs for Takotsubo."]
  assert result.citations == ["hanna2019"]
  assert result.dropped_citations == ["invented1999"]


def test_narrative_prompt_carries_the_candidate_gaps():
  """The narrative prompt describes the Candidate Gaps it must summarize."""
  client = _CapturingStub({"paragraphs": ["A summary."], "citations": ["hanna2019"]})

  summarize_gaps(_context(with_gap=True), client)

  assert "No RCTs on Takotsubo" in client.prompt
