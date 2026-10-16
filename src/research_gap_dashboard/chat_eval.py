"""
The chat evaluation set: measured citation accuracy and grounding (issue #99).

Conversational Analytics may cite only Corpus Papers and must quote the Evidence
it relies on (CODING_STANDARDS.md > Research integrity). This module turns those
rules into a pass/fail check per question, so chat quality is measured and can't
silently regress. Each `ChatEvalCase` is a question with the Papers a correct
answer cites and the Evidence substrings it must quote, or, for a question the
Corpus can't answer, the expectation that the chat abstains.

An answer fails its case when it:

- cites a key that is not a Corpus Paper, whether in its citations field (the
  grounding check dropped it) or written into the prose;
- cites a Corpus Paper the question's retrieved context carried nothing from
  (an ungrounded citation: the model was shown no fact, Evidence, or gap of it);
- misses an expected citation, or doesn't quote a required Evidence substring
  that the retrieved context carried verbatim;
- on an out-of-Corpus question, cites anything or doesn't say plainly that the
  Corpus doesn't answer it.

`run_eval` replays the set through `answer_question`. In `just test` the client
is a `RecordedLlmClient` over committed completions, so nothing calls the API;
re-recording against the live model is a manual step
(`uv run scripts/record_chat_eval.py`, see `tests/fixtures/chat-eval/README.md`).
What isn't here: judging an answer's prose beyond these checks; that stays a
human's call.
"""

import json
import re
import unicodedata
from pathlib import Path

from pydantic import BaseModel

from research_gap_dashboard.analytics import (
  GroundedChatAnswer,
  GroundingContext,
  answer_question,
  previous_question,
)
from research_gap_dashboard.llm import LlmClient, LlmResponseError

RERECORD_HINT = (
  "no recorded completion for this question's prompt; if the chat prompt or "
  "retrieval changed on purpose, re-record with "
  "`uv run scripts/record_chat_eval.py` (needs ANTHROPIC_API_KEY)"
)

# A token shaped like a citation key ("hanna2019", "stahli2022a"): a lowercase
# name, a four-digit year, an optional disambiguating letter.
_KEY_LIKE = re.compile(r"\b[a-z][a-z_-]*\d{4}[a-z]?\b")

# Ways the chat says a question is outside the Corpus. Matched on folded text.
_ABSTENTION = re.compile(
  r"not (?:in|covered (?:by|in)|answered (?:by|in)|addressed (?:by|in)|found in) "
  r"(?:this|the) corpus"
  r"|corpus (?:does not|doesn't|cannot|can't|do not) "
  r"(?:answer|address|cover|contain|include|mention|report|discuss)"
  r"|(?:none of|no) (?:the )?papers? (?:in (?:this|the) corpus )?"
  r"(?:address|cover|contain|discuss|report|stud|mention|answer)"
)


class ChatEvalCase(BaseModel):
  """One eval question and what a correct, grounded answer to it looks like."""

  id: str
  question: str
  history: list[str] = []
  expected_citations: list[str] = []
  required_evidence: list[str] = []
  abstain: bool = False


class ChatEvalSet(BaseModel):
  """A Corpus's grounding context and the questions evaluated against it."""

  context: GroundingContext
  cases: list[ChatEvalCase]


class CaseVerdict(BaseModel):
  """Whether one case's answer passed, and every reason it didn't."""

  case_id: str
  answer: str = ""
  citations: list[str] = []
  failures: list[str] = []

  @property
  def passed(self) -> bool:
    """True when the answer broke none of the case's rules."""
    return not self.failures


class EvalReport(BaseModel):
  """The verdicts of one run over an eval set."""

  verdicts: list[CaseVerdict]

  @property
  def passed(self) -> bool:
    """True when every case passed."""
    return all(verdict.passed for verdict in self.verdicts)


def load_eval_set(root: Path) -> ChatEvalSet:
  """Read an eval set: `context.json` (the grounding context) and `cases.json`."""
  context = GroundingContext.model_validate_json(
    (root / "context.json").read_text(encoding="utf-8")
  )
  cases = [
    ChatEvalCase.model_validate(case)
    for case in json.loads((root / "cases.json").read_text(encoding="utf-8"))
  ]
  return ChatEvalSet(context=context, cases=cases)


def grounded_context_for(
  case: ChatEvalCase, context: GroundingContext
) -> GroundingContext:
  """Return the context the model is shown for this case's question."""
  return context.for_question(case.question, previous_question(case.history))


def evaluate_answer(
  case: ChatEvalCase, answer: GroundedChatAnswer, context: GroundingContext
) -> CaseVerdict:
  """Judge one chat answer against its case and the Corpus context it came from."""
  grounded = grounded_context_for(case, context)
  failures = _citation_failures(answer, grounded)
  if case.abstain:
    failures += _abstention_failures(answer)
  else:
    failures += _expectation_failures(case, answer, grounded)
  return CaseVerdict(
    case_id=case.id,
    answer=answer.answer,
    citations=answer.citations,
    failures=failures,
  )


def run_eval(
  cases: list[ChatEvalCase], context: GroundingContext, client: LlmClient
) -> EvalReport:
  """Answer every case through the chat and judge each answer."""
  verdicts: list[CaseVerdict] = []
  for case in cases:
    try:
      answer = answer_question(case.question, context, client, history=case.history)
    except LlmResponseError:
      verdicts.append(CaseVerdict(case_id=case.id, failures=[RERECORD_HINT]))
      continue
    verdicts.append(evaluate_answer(case, answer, context))
  return EvalReport(verdicts=verdicts)


def _citation_failures(
  answer: GroundedChatAnswer, grounded: GroundingContext
) -> list[str]:
  """Invented citations (field or prose) and citations the context didn't ground."""
  allowed = grounded.allowed_keys
  failures = [
    f"invented citation {key!r}: not a Paper in this Corpus"
    for key in answer.dropped_citations
  ]
  for key in sorted(set(_KEY_LIKE.findall(answer.answer)) - allowed):
    if key not in answer.dropped_citations:
      failures.append(f"invented citation {key!r} in the answer text")
  supported = _grounded_keys(grounded)
  failures += [
    f"ungrounded citation {key!r}: the question's context carried nothing from it"
    for key in answer.citations
    if key not in supported
  ]
  return failures


def _grounded_keys(grounded: GroundingContext) -> set[str]:
  """Return the Papers the model was shown content of: facts, Evidence, gaps."""
  keys = {fact.citation_key for fact in grounded.facts}
  keys |= {passage.citation_key for passage in grounded.evidence}
  for gap in grounded.gaps:
    keys |= set(gap.source_citation_keys)
  return keys


def _expectation_failures(
  case: ChatEvalCase, answer: GroundedChatAnswer, grounded: GroundingContext
) -> list[str]:
  """Missing expected citations, and required Evidence not shown or not quoted."""
  failures = [
    f"expected citation {key!r} missing"
    for key in case.expected_citations
    if key not in answer.citations
  ]
  shown = _fold(grounded.render())
  said = _fold(answer.answer)
  for passage in case.required_evidence:
    needle = _fold(passage)
    if needle not in shown:
      failures.append(f"required Evidence {passage!r} not in the retrieved context")
    elif needle not in said:
      failures.append(f"required Evidence {passage!r} not quoted in the answer")
  return failures


def _abstention_failures(answer: GroundedChatAnswer) -> list[str]:
  """Require an out-of-Corpus question to be refused plainly, citing nothing."""
  failures: list[str] = []
  if answer.citations:
    failures.append(
      f"cited {', '.join(answer.citations)} on a question the Corpus can't answer"
    )
  if not _ABSTENTION.search(_fold(answer.answer)):
    failures.append("did not say the Corpus doesn't answer the question")
  return failures


def _fold(text: str) -> str:
  """Fold case, typographic quotes and dashes, and whitespace for substring checks."""
  text = unicodedata.normalize("NFKC", text).casefold()
  text = re.sub(r"[\u2018\u2019\u201b\u2032]", "'", text)
  text = re.sub(r"[\u201c\u201d\u201f\u2033]", '"', text)
  text = re.sub(r"[\u2010-\u2015\u2212]", "-", text)
  return re.sub(r"\s+", " ", text).strip()
