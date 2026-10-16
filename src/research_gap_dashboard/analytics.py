"""
Grounded generation for Conversational Analytics and the narrative summary.

Two dashboard features share one piece of machinery: a chat where the researcher
discusses the Papers, Candidate Gaps, and dashboard data, and a narrative
summary a researcher could adapt for a "gaps in the literature" section. Both
answer only from what the Corpus contains and both may cite only Papers in the
Corpus (CONTEXT.md > Conversational Analytics; CODING_STANDARDS.md > Research
integrity).

Grounding is enforced, not hoped for. The context handed to the model lists the
Corpus by citation key, and every generated citation is checked against that
allow-list before rendering: a key that does not resolve to a Corpus Paper is
dropped from the answer and surfaced as flagged, never shown as a citation.

This module owns the LLM seam (`llm`) so the Streamlit layer never does: the
dashboard reads finished artifacts and invokes these functions, keeping the
dashboard package itself free of pipeline logic (ADR 0002). Its inputs are plain
value models (`CorpusPaper`, `GapFact`) that the layout layer maps its artifact
read models onto, so this module has no dependency back on the dashboard.
"""

from pathlib import Path

from pydantic import BaseModel, Field

from research_gap_dashboard.fact_retrieval import (
  ExtractedFact,
  named_papers,
  paper_aliases,
  render_fact,
  retrieve_facts,
)
from research_gap_dashboard.llm import (
  LlmClient,
  LlmConfigError,
  ModelTier,
  build_llm_client,
  complete_model,
)

# Bump when a prompt or output shape changes: these key the LLM cache (ADR 0001),
# so a bump regenerates answers instead of serving stale ones.
CHAT_PROMPT_VERSION = "analytics-chat-v2"
NARRATIVE_PROMPT_VERSION = "analytics-narrative-v2"

# Forced structured-output calls occasionally misfire (an empty object, or the
# schema echoed back). Retry a few times, bypassing the cache, before accepting
# an empty answer.
ANALYTICS_ATTEMPTS = 3

# Told to the model when a question's retrieval found no extracted fact, so an
# out-of-Corpus question is answered with an honest "not in this Corpus".
NO_FACTS_MATCHED_NOTE = (
  "EXTRACTED FACTS: none of the Papers' extracted facts matched this question. "
  "If the rest of the context does not answer it either, say it is not in this "
  "Corpus."
)

# Told to the model when the question names a Paper that has no Extraction, so it
# says the Paper's facts weren't extracted instead of that the Paper is absent.
UNEXTRACTED_PAPERS_NOTE = (
  "NOT EXTRACTED: {keys} are in the Corpus but no facts were extracted from "
  "them, so only their bibliographic record above is available. Say so if the "
  "question needs their content."
)


class CorpusPaper(BaseModel):
  """One Corpus Paper as the grounding context needs it: how to cite it."""

  citation_key: str
  title: str = ""
  year: int | None = None
  authors: list[str] = []


class GapFact(BaseModel):
  """One Candidate Gap as the grounding context needs it, sourced to Papers."""

  gap_type: str
  title: str
  explanation: str = ""
  confidence: str = ""
  source_citation_keys: list[str] = []


class GapEvidence(BaseModel):
  """
  One verbatim Evidence passage the context may quote, and the Paper it's in.

  A scoped chat about one gap (issue #48) carries the same expanded Evidence the
  gap card shows, so the model can quote the passage the researcher is looking at
  instead of paraphrasing it. The passage stays verbatim from the Paper
  (CODING_STANDARDS.md > Research integrity).
  """

  citation_key: str
  section: str = ""
  passage: str


class GroundingContext(BaseModel):
  """
  The Corpus facts the model may answer from, and the allow-list to cite against.

  Holds the Papers (the only citable sources) and the Candidate Gaps already
  detected, so both chat and the narrative summary reason over the same grounded
  view of one Corpus.

  `fact_pool` is every extracted fact of the Corpus; it is never rendered whole.
  Each chat question retrieves its relevant slice into `facts` (issue #97), so
  the prompt stays within a size budget however large the Corpus.
  """

  papers: list[CorpusPaper]
  gaps: list[GapFact] = []
  # Which gaps `gaps` holds when it is a filtered set (issue #98), told to the
  # model so it never mistakes the surfaced gaps for every candidate.
  gap_scope: str = ""
  evidence: list[GapEvidence] = []
  facts: list[ExtractedFact] = []
  fact_pool: list[ExtractedFact] = []
  notes: list[str] = []

  @property
  def allowed_keys(self) -> set[str]:
    """The citation keys that resolve to a Paper in this Corpus."""
    return {paper.citation_key for paper in self.papers}

  def for_question(
    self, question: str, previous_question: str = ""
  ) -> "GroundingContext":
    """
    Return this context with the facts relevant to one question retrieved in.

    With no fact pool (a gap-scoped thread, or a Corpus not yet extracted) the
    context is returned unchanged. When the pool holds facts but none match, the
    model is told so, rather than left to guess from the Corpus list.
    """
    if not self.fact_pool:
      return self
    aliases = {
      paper.citation_key: paper_aliases(paper.citation_key, paper.authors)
      for paper in self.papers
    }
    facts = retrieve_facts(
      question, self.fact_pool, aliases, follow_up_of=previous_question
    )
    notes = list(self.notes)
    extracted = {fact.citation_key for fact in self.fact_pool}
    unextracted = sorted(
      named_papers(question, aliases, follow_up_of=previous_question) - extracted
    )
    if unextracted:
      notes.insert(0, UNEXTRACTED_PAPERS_NOTE.format(keys=", ".join(unextracted)))
    if not facts:
      notes.insert(0, NO_FACTS_MATCHED_NOTE)
    return self.model_copy(update={"facts": facts, "notes": notes})

  def render(self) -> str:
    """Render the grounded context as the prompt block the model answers from."""
    lines = [f"CORPUS ({len(self.papers)} Papers). Cite only these citation keys:"]
    for paper in self.papers:
      year = f" ({paper.year})" if paper.year is not None else ""
      title = paper.title or paper.citation_key
      lines.append(f"- [{paper.citation_key}] {title}{year}")
    if self.gaps or self.gap_scope:
      lines.append("")
      lines.append(
        f"CANDIDATE GAPS ({len(self.gaps)}), each a signal for human judgment, "
        "not a proven gap:"
      )
      if self.gap_scope:
        lines.append(self.gap_scope)
      for gap in self.gaps:
        sources = ", ".join(gap.source_citation_keys) or "\u2014"
        lines.append(
          f"- [{gap.gap_type}] {gap.title} (confidence: {gap.confidence}; "
          f"sources: {sources})"
        )
        if gap.explanation:
          lines.append(f"  {gap.explanation}")
    if self.evidence:
      lines.append("")
      lines.append(
        f"EVIDENCE ({len(self.evidence)} passages), quoted verbatim from the "
        "Papers above; you may quote these back:"
      )
      for passage in self.evidence:
        section = f" \u2014 {passage.section}" if passage.section else ""
        lines.append(f"- [{passage.citation_key}{section}] {passage.passage}")
    if self.facts:
      lines.append("")
      lines.append(
        f"EXTRACTED FACTS ({len(self.facts)} retrieved for this question), each "
        "with Evidence quoted verbatim from the Paper; you may quote it back:"
      )
      lines.extend(render_fact(fact) for fact in self.facts)
    for note in self.notes:
      if note.strip():
        lines.append("")
        lines.append(note)
    return "\n".join(lines)


class _ChatCompletion(BaseModel):
  """The raw chat answer the model emits, before its citations are grounded."""

  answer: str = Field(
    default="",
    description=(
      "The answer to the researcher's question, drawn only from the Corpus "
      "context. Say so plainly when the Corpus does not answer it."
    ),
  )
  citations: list[str] = Field(
    default_factory=list,
    description=(
      "The citation keys of the Corpus Papers this answer relies on. Use only "
      "keys listed in the CORPUS block; never invent a citation."
    ),
  )


class _NarrativeCompletion(BaseModel):
  """The raw narrative summary the model emits, before its citations are grounded."""

  paragraphs: list[str] = Field(
    default_factory=list,
    description=(
      "A few paragraphs for a 'gaps in the literature' section, grounded in the "
      "Candidate Gaps and Papers of this Corpus."
    ),
  )
  citations: list[str] = Field(
    default_factory=list,
    description=(
      "The citation keys of the Corpus Papers the summary relies on. Use only "
      "keys listed in the CORPUS block; never invent a citation."
    ),
  )


class GroundedChatAnswer(BaseModel):
  """A chat answer whose citations have been checked against the Corpus."""

  answer: str
  citations: list[str]
  dropped_citations: list[str]


class GroundedNarrative(BaseModel):
  """A narrative summary whose citations have been checked against the Corpus."""

  paragraphs: list[str]
  citations: list[str]
  dropped_citations: list[str]


def build_analytics_client(cache_dir: Path) -> LlmClient | None:
  """
  Build the cached LLM client for chat, or None when no API key is configured.

  Conversational Analytics needs a live model, so a missing key is a degraded
  page (a clear message), not a crash. This keeps the LLM-config boundary inside
  this module so the dashboard package never imports the LLM seam (ADR 0002).
  """
  try:
    return build_llm_client(cache_dir=cache_dir)
  except LlmConfigError:
    return None


def _ground_citations(
  citations: list[str], allowed: set[str]
) -> tuple[list[str], list[str]]:
  """Split cited keys into those that resolve to a Corpus Paper and those that don't."""
  kept: list[str] = []
  dropped: list[str] = []
  seen: set[str] = set()
  for citation in citations:
    key = citation.strip()
    if not key or key in seen:
      continue
    seen.add(key)
    (kept if key in allowed else dropped).append(key)
  return kept, dropped


def answer_question(  # pylint: disable=too-many-arguments
  question: str,
  context: GroundingContext,
  client: LlmClient,
  *,
  history: list[str] | None = None,
  prompt_version: str = CHAT_PROMPT_VERSION,
  tier: ModelTier = "default",
) -> GroundedChatAnswer:
  """
  Answer one question grounded in the Corpus, citing only Corpus Papers.

  The model may cite only Papers in the grounded context; any citation it returns
  that does not resolve to a Corpus Paper is dropped from the answer and surfaced
  as flagged rather than shown (CODING_STANDARDS.md > Research integrity).
  """
  history = history or []
  grounded = context.for_question(question, previous_question(history))
  completion = complete_model(
    client,
    _build_chat_prompt(question, grounded, history),
    _ChatCompletion,
    prompt_version=prompt_version,
    tier=tier,
    attempts=ANALYTICS_ATTEMPTS,
    accept=lambda result: bool(result.answer.strip()),
  )
  kept, dropped = _ground_citations(completion.citations, context.allowed_keys)
  return GroundedChatAnswer(
    answer=completion.answer, citations=kept, dropped_citations=dropped
  )


def previous_question(history: list[str]) -> str:
  """Return the researcher's last question in the transcript, for follow-ups."""
  for turn in reversed(history):
    if turn.startswith("user:"):
      return turn.removeprefix("user:")
  return ""


def summarize_gaps(
  context: GroundingContext,
  client: LlmClient,
  *,
  prompt_version: str = NARRATIVE_PROMPT_VERSION,
  tier: ModelTier = "default",
) -> GroundedNarrative:
  """
  Write a few paragraphs on the Corpus's gaps, citing only Corpus Papers.

  Grounded in the gaps the context carries (the dashboard passes only those the
  researcher accepted) and their source Papers; every citation is validated
  against the Corpus before it is returned, so the summary never invents a
  reference (CODING_STANDARDS.md > Research integrity).
  """
  completion = complete_model(
    client,
    _build_narrative_prompt(context),
    _NarrativeCompletion,
    prompt_version=prompt_version,
    tier=tier,
    attempts=ANALYTICS_ATTEMPTS,
    accept=lambda result: any(p.strip() for p in result.paragraphs),
  )
  paragraphs = [p.strip() for p in completion.paragraphs if p.strip()]
  kept, dropped = _ground_citations(completion.citations, context.allowed_keys)
  return GroundedNarrative(
    paragraphs=paragraphs, citations=kept, dropped_citations=dropped
  )


def _build_chat_prompt(
  question: str, context: GroundingContext, history: list[str]
) -> str:
  """Build the chat prompt: rules, grounded context, prior turns, the question."""
  parts = [
    "You are a research analyst discussing one Corpus of papers with a "
    "researcher. Answer only from the CORPUS context below. If the Corpus does "
    "not answer the question, say so plainly instead of guessing. Cite Papers "
    "only by the citation keys listed, and put those keys in the citations "
    "field; never invent a citation or cite work outside the Corpus. When you "
    "rely on an extracted fact or Evidence passage, quote the passage so the "
    "researcher can verify it. Speak of Candidate Gaps as signals for human "
    "judgment, not proven gaps.",
    "",
    context.render(),
  ]
  if history:
    parts += ["", "CONVERSATION SO FAR:", *history]
  parts += ["", f"QUESTION: {question}"]
  return "\n".join(parts)


def _build_narrative_prompt(context: GroundingContext) -> str:
  """Build the narrative-summary prompt from the grounded gaps and Papers."""
  return "\n".join(
    [
      "You draft a short 'gaps in the literature' section for a researcher, "
      "grounded strictly in the CORPUS context below. Write a few paragraphs "
      "summarizing where this Corpus looks thin or silent, drawing only on "
      "the Candidate Gaps listed, which the researcher has accepted, and the "
      "Papers. Do not raise gaps beyond those listed. Present gaps as "
      "candidates for human judgment, not proven gaps. Cite Papers only by the "
      "citation keys listed, and put those keys in the citations field; never "
      "invent a citation or cite work outside the Corpus.",
      "",
      context.render(),
    ]
  )
