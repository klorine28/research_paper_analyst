"""
Assembling the Gap Cards view from the CandidateGaps artifact and judgments.

These are plain functions with no Streamlit dependency: the layout layer stays
thin by asking here for finished GapCardsData. Each card carries every field the
view shows, including the verbatim Evidence passages, and the researcher's
accept/reject state merged in from the judgment log so a restored session shows
the same verdicts. Cards keep the artifact's order so the same artifact always
lists gaps the same way.
"""

from pathlib import Path

from pydantic import BaseModel

from research_gap_dashboard.analytics import (
  GapEvidence,
  GapFact,
  GroundingContext,
)
from research_gap_dashboard.dashboard import text
from research_gap_dashboard.dashboard.artifacts import (
  CandidateGapRecord,
  CandidateGapsArtifact,
)
from research_gap_dashboard.dashboard.evidence_context import (
  EvidenceExpander,
  ExpandedEvidence,
)
from research_gap_dashboard.dashboard.judgments import Decision, JudgmentLog

Status = Decision | None


class EvidencePassage(BaseModel):
  """One verbatim Evidence passage on a gap card, and the Paper it came from."""

  citation_key: str
  passage: str
  section: str
  detail: str
  context: ExpandedEvidence | None = None


class GapCard(BaseModel):
  """One Candidate Gap card the view renders, with its judgment state."""

  gap_id: str
  gap_type: str
  gap_type_label: str
  title: str
  explanation: str
  confidence: str
  confidence_label: str
  confidence_reason: str
  cell_count: int | None
  corpus_paper_count: int
  source_citation_keys: list[str]
  evidence: list[EvidencePassage]
  status: Status


class GapCardsData(BaseModel):
  """Everything the Gap Cards page shows, already computed."""

  cards: list[GapCard]
  corpus_paper_count: int

  @property
  def gap_count(self) -> int:
    """How many Candidate Gaps the Corpus produced."""
    return len(self.cards)

  @property
  def accepted_count(self) -> int:
    """How many gaps the researcher has accepted."""
    return sum(1 for card in self.cards if card.status == "accepted")

  @property
  def rejected_count(self) -> int:
    """How many gaps the researcher has rejected."""
    return sum(1 for card in self.cards if card.status == "rejected")

  @property
  def undecided_count(self) -> int:
    """How many gaps still await a verdict."""
    return sum(1 for card in self.cards if card.status is None)


def _evidence_detail(passage_source: CandidateGapRecord, index: int) -> str:
  """Describe what a card's Evidence passage supports, per gap-card shape."""
  link = passage_source.evidence[index]
  if link.statement:
    return link.statement
  if link.role and link.original_term:
    role = text.EVIDENCE_ROLE_LABELS.get(link.role, link.role)
    return text.EVIDENCE_TERM_DETAIL.format(role=role, term=link.original_term)
  return ""


def _card(
  record: CandidateGapRecord, status: Status, expander: EvidenceExpander
) -> GapCard:
  """Assemble one display card from an artifact record and its judgment."""
  return GapCard(
    gap_id=record.gap_id,
    gap_type=record.gap_type,
    gap_type_label=text.GAP_TYPE_LABELS.get(record.gap_type, record.gap_type),
    title=record.title,
    explanation=record.explanation,
    confidence=record.confidence,
    confidence_label=text.CONFIDENCE_LABELS.get(record.confidence, record.confidence),
    confidence_reason=record.confidence_reason,
    cell_count=record.cell_count,
    corpus_paper_count=record.corpus_paper_count,
    source_citation_keys=record.source_citation_keys,
    evidence=[
      EvidencePassage(
        citation_key=link.citation_key,
        passage=link.evidence.passage,
        section=link.evidence.section,
        detail=_evidence_detail(record, index),
        context=expander.context_for(link.citation_key, link.evidence.passage),
      )
      for index, link in enumerate(record.evidence)
    ],
    status=status,
  )


def scope_context_to_gap(card: GapCard, corpus: GroundingContext) -> GroundingContext:
  """
  Scope a Corpus's grounding context down to one Candidate Gap's slice.

  The one-click "Discuss this gap" chat (issue #48) reuses the existing chat and
  grounding machinery, but over a context narrowed to just this gap: its source
  Papers (plus any Paper its Evidence quotes), the single gap, and the same
  expanded Evidence the card shows. So the model answers "why is this a gap, and
  what would close it" from that slice, still citing only Papers in the Corpus
  (CODING_STANDARDS.md > Research integrity). It stays a Candidate Gap, never a
  verdict.
  """
  keys = set(card.source_citation_keys)
  keys |= {passage.citation_key for passage in card.evidence}
  papers = [paper for paper in corpus.papers if paper.citation_key in keys]
  gap = GapFact(
    gap_type=card.gap_type_label,
    title=card.title,
    explanation=card.explanation,
    confidence=card.confidence_label,
    source_citation_keys=card.source_citation_keys,
  )
  evidence = [
    GapEvidence(
      citation_key=passage.citation_key,
      section=passage.section,
      passage=_expanded_passage(passage),
    )
    for passage in card.evidence
  ]
  return GroundingContext(papers=papers, gaps=[gap], evidence=evidence)


def _expanded_passage(passage: EvidencePassage) -> str:
  """Use the paragraph-expanded window when grounded, else the bare anchor quote."""
  context = passage.context
  if context is not None and context.grounded:
    return context.window
  return passage.passage


def build_gap_cards(
  artifact: CandidateGapsArtifact,
  judgments: JudgmentLog,
  corpus_root: Path | None = None,
) -> GapCardsData:
  """
  Merge the Candidate Gaps with the researcher's saved accept/reject state.

  When a corpus root is given, each Evidence passage is deterministically widened
  to its containing paragraph (`evidence_context`) so a card shows the anchor in
  situ; without one the passages stay bare, keeping this callable in unit tests
  that have no parsed text on disk.
  """
  decided = judgments.by_gap_id()
  expander = EvidenceExpander(corpus_root)
  cards = [
    _card(
      record,
      decided[record.gap_id].decision if record.gap_id in decided else None,
      expander,
    )
    for record in artifact.gaps
  ]
  return GapCardsData(cards=cards, corpus_paper_count=artifact.corpus_paper_count)
