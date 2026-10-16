"""
Behavior of the chat evaluation set (issue #99).

Two seams: `evaluate_answer` judges one chat answer against its eval case and
the context the model was shown, and `run_eval` drives every case of a recorded
eval set through `answer_question`. The first is exercised with hand-written
answers so each failure mode is pinned; the second runs the committed eval set
against its recorded completions, so chat quality can't silently regress.
"""

from pathlib import Path

import pytest

from research_gap_dashboard.analytics import (
  CorpusPaper,
  GroundedChatAnswer,
  GroundingContext,
)
from research_gap_dashboard.chat_eval import (
  ChatEvalCase,
  evaluate_answer,
  load_eval_set,
  run_eval,
)
from research_gap_dashboard.fact_retrieval import ExtractedFact
from research_gap_dashboard.llm import RecordedLlmClient

CHAT_EVAL_DIR = Path(__file__).parent / "fixtures" / "chat-eval"


def _context() -> GroundingContext:
  """Build a two-Paper Corpus whose Extractions sit in the retrieval pool."""
  return GroundingContext(
    papers=[
      CorpusPaper(
        citation_key="hanna2019", title="Takotsubo", year=2019, authors=["A Hanna"]
      ),
      CorpusPaper(
        citation_key="lee2021", title="Heart failure", year=2021, authors=["B Lee"]
      ),
    ],
    fact_pool=[
      ExtractedFact(
        citation_key="hanna2019",
        field="methods",
        statement="Retrospective chart review",
        passage="We retrospectively reviewed 120 charts.",
        section="methods",
      ),
      ExtractedFact(
        citation_key="lee2021",
        field="methods",
        statement="Echocardiographic follow-up",
        passage="All patients underwent echocardiography at 6 months.",
        section="methods",
      ),
    ],
  )


_METHODS_CASE = ChatEvalCase(
  id="hanna-methods",
  question="What methods did Hanna use?",
  expected_citations=["hanna2019"],
  required_evidence=["retrospectively reviewed 120 charts"],
)

_ABSTAIN_CASE = ChatEvalCase(
  id="out-of-corpus",
  question="What is the capital of France?",
  abstain=True,
)


def _answer(text: str, citations: list[str], dropped: list[str] | None = None):
  return GroundedChatAnswer(
    answer=text, citations=citations, dropped_citations=dropped or []
  )


def test_a_grounded_cited_answer_quoting_its_evidence_passes():
  """Expected Paper cited, Evidence quoted verbatim: the case passes."""
  answer = _answer(
    'Hanna ran a chart review: "We retrospectively reviewed 120 charts."',
    ["hanna2019"],
  )

  verdict = evaluate_answer(_METHODS_CASE, answer, _context())

  assert verdict.passed, verdict.failures


def test_an_invented_citation_fails_the_case():
  """A citation the grounding check dropped (not a Corpus Paper) fails the eval."""
  answer = _answer(
    'Hanna: "We retrospectively reviewed 120 charts."',
    ["hanna2019"],
    dropped=["ghost2020"],
  )

  verdict = evaluate_answer(_METHODS_CASE, answer, _context())

  assert not verdict.passed
  assert any("ghost2020" in failure for failure in verdict.failures)


def test_an_invented_citation_in_the_answer_text_fails_the_case():
  """A key-like citation written into the prose but not in the Corpus fails too."""
  answer = _answer(
    'Hanna: "We retrospectively reviewed 120 charts." (see also smith2018)',
    ["hanna2019"],
  )

  verdict = evaluate_answer(_METHODS_CASE, answer, _context())

  assert not verdict.passed
  assert any("smith2018" in failure for failure in verdict.failures)


def test_citing_a_corpus_paper_the_question_retrieved_nothing_from_fails():
  """An ungrounded citation: a real Corpus Paper the model was shown no facts of."""
  answer = _answer(
    'Hanna: "We retrospectively reviewed 120 charts."', ["hanna2019", "lee2021"]
  )

  verdict = evaluate_answer(_METHODS_CASE, answer, _context())

  assert not verdict.passed
  assert any("lee2021" in failure for failure in verdict.failures)


def test_a_missing_expected_citation_fails_the_case():
  """An answer that doesn't cite the Paper the question is about fails."""
  answer = _answer("A chart review: we retrospectively reviewed 120 charts.", [])

  verdict = evaluate_answer(_METHODS_CASE, answer, _context())

  assert not verdict.passed
  assert any("hanna2019" in failure for failure in verdict.failures)


def test_required_evidence_must_be_quoted_in_the_answer():
  """An answer that paraphrases instead of quoting the required Evidence fails."""
  answer = _answer("Hanna looked back over some charts.", ["hanna2019"])

  verdict = evaluate_answer(_METHODS_CASE, answer, _context())

  assert not verdict.passed
  assert any("120 charts" in failure for failure in verdict.failures)


def test_required_evidence_absent_from_the_retrieved_context_fails():
  """A required passage the model was never shown means retrieval missed it."""
  case = _METHODS_CASE.model_copy(
    update={"required_evidence": ["echocardiography at 6 months"]}
  )
  answer = _answer(
    "Hanna: echocardiography at 6 months (we retrospectively reviewed).",
    ["hanna2019"],
  )

  verdict = evaluate_answer(case, answer, _context())

  assert not verdict.passed
  assert any("not in the retrieved context" in f for f in verdict.failures)


@pytest.mark.parametrize(
  "text",
  [
    "The Corpus does not answer this question.",
    "This is not in this Corpus.",
    "None of the Papers in the Corpus address this.",
  ],
)
def test_an_honest_not_in_this_corpus_passes_an_abstain_case(text: str):
  """An out-of-Corpus question answered with an honest refusal and no citation."""
  verdict = evaluate_answer(_ABSTAIN_CASE, _answer(text, []), _context())

  assert verdict.passed, verdict.failures


def test_a_guess_on_an_out_of_corpus_question_fails():
  """Answering an out-of-Corpus question from general knowledge fails."""
  verdict = evaluate_answer(_ABSTAIN_CASE, _answer("Paris.", []), _context())

  assert not verdict.passed


def test_citing_a_paper_on_an_out_of_corpus_question_fails():
  """An abstain case must cite nothing, even while saying it can't answer."""
  answer = _answer("The Corpus does not answer this question.", ["hanna2019"])

  verdict = evaluate_answer(_ABSTAIN_CASE, answer, _context())

  assert not verdict.passed


def test_a_case_with_no_recorded_completion_fails_with_the_rerecord_hint():
  """A changed prompt misses the recording and says how to re-record."""
  report = run_eval([_METHODS_CASE], _context(), RecordedLlmClient({}))

  assert not report.passed
  assert "record_chat_eval" in report.verdicts[0].failures[0]


def test_the_recorded_chat_eval_set_passes():
  """Every committed eval question, replayed from its recorded completion, passes."""
  eval_set = load_eval_set(CHAT_EVAL_DIR)

  report = run_eval(
    eval_set.cases,
    eval_set.context,
    RecordedLlmClient.from_file(CHAT_EVAL_DIR / "llm-responses.json"),
  )

  failures = {v.case_id: v.failures for v in report.verdicts if not v.passed}
  assert not failures
  assert len(report.verdicts) >= 3
