"""
Assembling the Gap Cards view from the CandidateGaps artifact and judgments.

These are plain functions with no Streamlit dependency: the layout layer stays
thin by asking here for finished GapCardsData. Each card carries every field the
view shows, including the verbatim Evidence passages, and the researcher's
accept/reject state merged in from the judgment log so a restored session shows
the same verdicts. Cards keep the artifact's order so the same artifact always
lists gaps the same way.

A card a detection layer rejected is kept, never dropped (ADR 0008 "demote,
never delete"): it leaves the filtered list, and the chat and narrative built
on it, until the researcher accepts it, and it is always listed with every
rejection and lowering under "Rejected by review".
"""

from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from research_gap_dashboard.analytics import (
  GapEvidence,
  GapFact,
  GroundingContext,
)
from research_gap_dashboard.dashboard import text
from research_gap_dashboard.dashboard.artifacts import (
  CandidateGapRecord,
  CandidateGapsArtifact,
  CoverageMatrixRecord,
  GapReviewRecord,
  RetrievalGapsArtifact,
  SaturationCheckRecord,
  SaturationChecksArtifact,
  SaturationHitRecord,
)
from research_gap_dashboard.dashboard.evidence_context import (
  EvidenceExpander,
  ExpandedEvidence,
)
from research_gap_dashboard.dashboard.judgments import (
  Decision,
  GapKey,
  JudgmentLog,
  gap_key,
)
from research_gap_dashboard.dashboard.suggestions import (
  SuggestedPapersRecord,
  Suggestion,
  SuggestionState,
  lookup_overlap,
  retrieval_overlaps,
  suggestion_state,
)

Status = Decision | None


class EvidencePassage(BaseModel):
  """One verbatim Evidence passage on a gap card, and the Paper it came from."""

  citation_key: str
  passage: str
  section: str
  detail: str
  context: ExpandedEvidence | None = None


class CategoryPapers(BaseModel):
  """The Papers placed on one category of a gap, or behind a limitation."""

  label: str
  citation_keys: list[str]


class NeighbourCell(BaseModel):
  """One Coverage Matrix cell that shares a category with a gap's cell."""

  row_label: str
  column_label: str
  paper_count: int


class BridgeView(BaseModel):
  """One bridging category on a cell gap's Tier 3, in the artifact's order."""

  label: str
  text: str


class GapData(BaseModel):
  """
  Tier 3, "show the data": what a card's confidence was computed from.

  The expected count is the independence expectation for the cell, shown as a
  ranking score beside the observed count, never as a significance claim (ADR
  0008). Limitation cards have no cell, so they carry no neighbours or score.
  """

  category_papers: list[CategoryPapers]
  neighbours: list[NeighbourCell]
  expected_count: float | None
  observed_count: int | None
  reasons: list[str]
  bridges: list[BridgeView] = []


class SaturationSearchView(BaseModel):
  """One search a saturation check ran, as the card's Tier 3 lists it."""

  source: str
  query: str
  attempt: str = Field(default="base", description='"base" or "llm_variant".')
  returned_count: int
  in_corpus_count: int
  error: str


class SaturationHitView(BaseModel):
  """One out-of-Corpus hit of a saturation check, never a Corpus Paper."""

  title: str
  details: str = Field(description="Year, venue, DOI or PMID and sources.")
  screened: bool = Field(description="It passed every screening step that ran.")
  reason: str
  llm_verdict: str = Field(default="", description="The LLM verdict, as shown.")
  llm_reason: str = ""
  second_attempt: bool = Field(
    default=False, description="Found only by the LLM-rephrased second attempt."
  )
  suggestion: Suggestion | None = Field(
    default=None, description="How to add it to the Corpus (the Grow path)."
  )
  state: SuggestionState = SuggestionState()
  retrieval_overlap: int = Field(
    default=0,
    description="Corpus Papers citing it, when it is also a Retrieval Gap candidate.",
  )


class SaturationView(BaseModel):
  """
  A card's wider-literature check (ADR 0009 §14): badge, result, drill-down.

  `status` is "not_checked" until a check runs; afterwards the latest snapshot
  fills the rest and `history` lists every snapshot, newest first. Knowledge,
  Coverage and Unanswered Limitation cards are `checkable`; a Retrieval Gap is
  an out-of-Corpus work already and never is (ADR 0009 §10).
  """

  checkable: bool
  status: str = "not_checked"
  badge: str
  summary: str = ""
  checked_on: str = ""
  checked_at: datetime | None = None
  screening_method: str = ""
  searches: list[SaturationSearchView] = []
  hits: list[SaturationHitView] = []
  screened_count: int = 0
  llm_screening: str = Field(
    default="", description="How the LLM screening ran, with model and prompt."
  )
  history: list[str] = []
  second_attempt_label: str = Field(
    default="", description="Set when the base queries found nothing."
  )
  second_attempt_provenance: str = ""
  second_attempt_note: str = ""


ReviewEffect = Literal["reject", "lower"]


class ReviewStep(BaseModel):
  """One detection layer's rejection or lowering of a candidate, as shown."""

  layer: str
  effect: ReviewEffect
  level_after: str
  reason: str
  # The verdict in plain words: which layer, what it did, and why.
  label: str


class GapCard(BaseModel):
  """
  One Candidate Gap card the view renders, with its judgment state.

  Fields map onto ADR 0008's three tiers: Tier 1 is the title, confidence, `why`
  and cell count; Tier 2 the Evidence, Audit Basis and Minimal Study; Tier 3
  `data`. `saturation` is the
  wider-literature check, shown across all three tiers.
  """

  gap_id: str
  # What a verdict on this card is saved under: the gap id for a cell gap, the
  # verbatim source statements for an Unanswered Limitation (ADR 0010 §5).
  key: GapKey
  gap_type: str
  gap_type_label: str
  title: str
  explanation: str
  why: str
  confidence: str
  confidence_label: str
  confidence_reason: str
  cell_count: int | None
  corpus_paper_count: int
  source_citation_keys: list[str]
  # Every Paper the gap rests on, once each: its source Papers, then any other
  # Paper its Evidence cites. The card's Explainer and Comparison links use it.
  paper_keys: list[str]
  evidence: list[EvidencePassage]
  # Tier 2, from the L5 review; empty when the review did not keep the gap.
  audit_basis: str = ""
  audit_basis_evidence_ids: list[str] = []
  minimal_study: str = ""
  data: GapData
  status: Status
  saturation: SaturationView
  # Two witnesses: the corroborating limitation groups' labels; empty if none.
  flagged_by_authors: list[str] = []
  # Unanswered Limitations: the group's limitation type label; "" if untagged.
  bucket_label: str = ""
  # Every layer step that rejected or lowered the candidate, in trail order.
  review: list[ReviewStep] = []

  @property
  def rejected_by_review(self) -> bool:
    """Whether a detection layer rejected this candidate."""
    return any(step.effect == "reject" for step in self.review)

  @property
  def surfaced(self) -> bool:
    """Whether the gap list shows it: not rejected by review, or accepted anyway."""
    return not self.rejected_by_review or self.status == "accepted"

  @property
  def review_layer(self) -> str:
    """The layer whose rejection an accept overrides; empty if none rejected it."""
    return next((s.layer for s in self.review if s.effect == "reject"), "")


class GapCardsData(BaseModel):
  """Everything the Gap Cards page shows, already computed."""

  cards: list[GapCard]
  corpus_paper_count: int
  # (High, Medium): the smallest equal category sizes at which an empty cell
  # ranks each level at this Corpus size; None for an artifact written before L1.
  l1_smallest_margins: tuple[int | None, int | None] | None = None

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

  @property
  def reviewed_cards(self) -> list[GapCard]:
    """The "Rejected by review" section: rejected cards, then lowered ones."""
    rejected = [card for card in self.cards if card.rejected_by_review]
    lowered = [
      card for card in self.cards if card.review and not card.rejected_by_review
    ]
    return [*rejected, *lowered]


# Levels in display order, and the default view: Low-confidence gaps are hidden
# until the researcher asks for them (issue #108), but never silently, since the
# page always states how many are hidden.
CONFIDENCE_LEVELS: tuple[str, ...] = ("high", "medium", "low")
DEFAULT_CONFIDENCE_FILTER: tuple[str, ...] = ("high", "medium")


class FilteredGapCards(BaseModel):
  """The cards the Gap Cards filters keep, and how many they hide (#79, #108)."""

  cards: list[GapCard]
  total_count: int
  # Rejected by a detection layer and not accepted: out of the list, not the
  # filters' doing, and listed under "Rejected by review" instead.
  review_hidden_count: int = 0

  @property
  def shown_count(self) -> int:
    """How many gaps pass the filter."""
    return len(self.cards)

  @property
  def hidden_count(self) -> int:
    """How many gaps the filter hides."""
    return self.total_count - self.shown_count - self.review_hidden_count


def filter_by_confidence(data: GapCardsData, levels: Iterable[str]) -> FilteredGapCards:
  """Keep only the cards at the chosen confidence levels, in artifact order."""
  return filter_gap_cards(data, confidence_levels=set(levels))


def _evidence_detail(passage_source: CandidateGapRecord, index: int) -> str:
  """Describe what a card's Evidence passage supports, per gap-card shape."""
  link = passage_source.evidence[index]
  if link.statement:
    return link.statement
  if link.role and link.original_term:
    role = text.EVIDENCE_ROLE_LABELS.get(link.role, link.role)
    return text.EVIDENCE_TERM_DETAIL.format(role=role, term=link.original_term)
  return ""


def _is_cell_gap(record: CandidateGapRecord) -> bool:
  """Say whether a record is a Coverage Matrix cell gap, not a limitation."""
  return record.cell_count is not None and bool(record.row_id)


def _why(record: CandidateGapRecord) -> str:
  """Build the card's one-line why from the counts the artifact recorded."""
  if _is_cell_gap(record):
    return text.GAP_WHY_CELL.format(
      row=record.row_label,
      column=record.column_label,
      row_count=record.row_paper_count,
      column_count=record.column_paper_count,
      total=record.corpus_paper_count,
      count=record.cell_count,
    )
  return text.GAP_WHY_LIMITATION.format(
    source_count=len(record.source_citation_keys),
    later_count=record.later_paper_count,
  )


def _category_keys(
  record: CandidateGapRecord, axis: str, category_id: str
) -> list[str]:
  """List the Papers whose Evidence places them on one of the cell's categories."""
  return sorted(
    {
      link.citation_key
      for link in record.evidence
      if link.axis == axis and link.category_id == category_id
    }
  )


def _neighbours(
  record: CandidateGapRecord, matrices: list[CoverageMatrixRecord]
) -> list[NeighbourCell]:
  """
  List the cells of the gap's matrix that share one of its categories.

  On a same-axis matrix (Topic x Topic) a category may sit in either position,
  since only one triangle of cells is stored. Fullest cells come first.
  """
  matrix = next(
    (
      m
      for m in matrices
      if m.row_axis == record.row_axis and m.column_axis == record.column_axis
    ),
    None,
  )
  if matrix is None:
    return []
  labels = {c.category_id: c.label for c in [*matrix.rows, *matrix.columns]}
  same_axis = matrix.row_axis == matrix.column_axis
  gap_categories = {record.row_id, record.column_id}
  neighbours: list[NeighbourCell] = []
  for cell in matrix.cells:
    if cell.row_id == record.row_id and cell.column_id == record.column_id:
      continue
    if same_axis:
      shares = bool({cell.row_id, cell.column_id} & gap_categories)
    else:
      shares = cell.row_id == record.row_id or cell.column_id == record.column_id
    if shares:
      neighbours.append(
        NeighbourCell(
          row_label=labels.get(cell.row_id, cell.row_id),
          column_label=labels.get(cell.column_id, cell.column_id),
          paper_count=cell.paper_count,
        )
      )
  return sorted(neighbours, key=lambda n: (-n.paper_count, n.row_label, n.column_label))


def _data(record: CandidateGapRecord, matrices: list[CoverageMatrixRecord]) -> GapData:
  """Assemble Tier 3 from the record and the matrix it was detected in."""
  if not _is_cell_gap(record):
    return GapData(
      category_papers=[
        CategoryPapers(
          label=text.GAP_SOURCE_PAPERS_GROUP,
          citation_keys=record.source_citation_keys,
        )
      ],
      neighbours=[],
      expected_count=None,
      observed_count=None,
      reasons=[record.confidence_reason],
    )
  total = record.corpus_paper_count
  expected = (
    record.row_paper_count * record.column_paper_count / total if total else 0.0
  )
  return GapData(
    category_papers=[
      CategoryPapers(
        label=record.row_label,
        citation_keys=_category_keys(record, record.row_axis, record.row_id),
      ),
      CategoryPapers(
        label=record.column_label,
        citation_keys=_category_keys(record, record.column_axis, record.column_id),
      ),
    ],
    neighbours=_neighbours(record, matrices),
    expected_count=expected,
    observed_count=record.cell_count,
    reasons=_reasons(record),
    bridges=[
      BridgeView(
        label=bridge.label,
        text=text.GAP_DATA_BRIDGE.format(
          label=bridge.label,
          axis=bridge.axis,
          row_count=bridge.row_link_count,
          column_count=bridge.column_link_count,
        ),
      )
      for bridge in record.bridges
    ],
  )


# The Gap Types a saturation check runs on (ADR 0009 §10); Retrieval Gaps are
# deliberately absent.
CHECKABLE_GAP_TYPES = frozenset(
  {"knowledge_gap", "coverage_gap", "unanswered_limitation"}
)


def _badge(check: SaturationCheckRecord) -> str:
  """Return the one-line badge for a check snapshot."""
  template = text.GAP_SATURATION_BADGES.get(
    check.status, text.GAP_SATURATION_BADGES["not_run"]
  )
  return template.format(
    count=sum(1 for hit in check.hits if hit.counts),
    date=check.checked_at.date().isoformat(),
  )


def _llm_screening(check: SaturationCheckRecord) -> str:
  """Say how a check's LLM screening ran; empty on snapshots that predate it."""
  run = check.llm_screening
  if run is None:
    return ""
  if run.status != "screened":
    return text.GAP_SATURATION_LLM_NOT_RUN.format(reason=run.reason)
  return text.GAP_SATURATION_LLM_RUN.format(
    count=run.screened_count, model=run.model, version=run.prompt_version
  )


def _hit_details(hit_sources: list[str], year: int | None, venue: str, ref: str) -> str:
  """Join a hit's year, venue, identifier and sources into one line."""
  parts = [str(year) if year else "", venue, ref]
  parts.append(text.GAP_SATURATION_FROM.format(sources=", ".join(hit_sources)))
  return " \u00b7 ".join(part for part in parts if part)


@dataclass(frozen=True)
class _GrowContext:
  """What a hit needs to offer the Grow path and show the double signal."""

  suggested: SuggestedPapersRecord = field(default_factory=SuggestedPapersRecord)
  overlaps: dict[str, int] = field(default_factory=dict)


def _hit_view(hit: SaturationHitRecord, grow: _GrowContext) -> SaturationHitView:
  """One hit as Tier 3 lists it, with its Grow-path state and double signal."""
  suggestion = Suggestion(
    route="saturation",
    doi=hit.doi,
    pmid=hit.pmid,
    openalex_id=hit.openalex_id,
    title=hit.title,
    year=hit.year,
  )
  ids = suggestion.ids()
  return SaturationHitView(
    title=hit.title or hit.work_id,
    details=_hit_details(
      hit.sources,
      hit.year,
      hit.venue,
      f"DOI {hit.doi}" if hit.doi else f"PMID {hit.pmid}" if hit.pmid else "",
    ),
    screened=hit.counts,
    reason=hit.reason,
    llm_verdict=(
      text.GAP_SATURATION_LLM_VERDICTS.get(hit.llm_verdict, hit.llm_verdict)
      if hit.llm_verdict
      else ""
    ),
    llm_reason=hit.llm_reason,
    second_attempt=hit.attempt == "llm_variant",
    suggestion=suggestion if ids else None,
    state=suggestion_state(grow.suggested, ids),
    retrieval_overlap=lookup_overlap(ids, grow.overlaps),
  )


def _saturation(
  record: CandidateGapRecord,
  checks: list[SaturationCheckRecord],
  grow: _GrowContext,
) -> SaturationView:
  """Build a card's wider-literature view from its checks, latest last."""
  checkable = record.gap_type in CHECKABLE_GAP_TYPES
  if not checks:
    return SaturationView(
      checkable=checkable, badge=text.GAP_SATURATION_BADGES["not_checked"]
    )
  latest = checks[-1]
  return SaturationView(
    checkable=checkable,
    status=latest.status,
    badge=_badge(latest),
    summary=latest.status_reason,
    checked_at=latest.checked_at,
    checked_on=latest.checked_at.strftime("%Y-%m-%d %H:%M"),
    screening_method=latest.screening_method,
    searches=[
      SaturationSearchView(
        source=search.source,
        query=search.query,
        attempt=search.attempt,
        returned_count=len(search.returned_work_ids),
        in_corpus_count=len(search.in_corpus_work_ids),
        error=search.error,
      )
      for search in latest.searches
    ],
    hits=[_hit_view(hit, grow) for hit in latest.hits],
    screened_count=sum(1 for hit in latest.hits if hit.counts),
    llm_screening=_llm_screening(latest),
    history=[
      text.GAP_SATURATION_HISTORY_ENTRY.format(
        date=check.checked_at.strftime("%Y-%m-%d %H:%M"), badge=_badge(check)
      )
      for check in reversed(checks)
    ],
    **_second_attempt(latest),
  )


def _second_attempt(check: SaturationCheckRecord) -> dict[str, str]:
  """Return the label, model/prompt provenance and note of the second attempt."""
  attempt = check.second_attempt
  if attempt is None:
    return {}
  provenance = ""
  if attempt.concepts:
    provenance = text.GAP_SATURATION_SECOND_ATTEMPT_PROVENANCE.format(
      model=attempt.model or text.GAP_SATURATION_UNKNOWN_MODEL,
      prompt_version=attempt.prompt_version,
    )
  return {
    "second_attempt_label": attempt.label,
    "second_attempt_provenance": provenance,
    "second_attempt_note": attempt.note,
  }


def _review(record: CandidateGapRecord) -> list[ReviewStep]:
  """List the trail steps that rejected or lowered the candidate, worded."""
  steps: list[ReviewStep] = []
  for step in record.trail:
    if step.effect not in ("reject", "lower"):
      continue
    layer = text.GAP_REVIEW_LAYER_LABELS.get(step.layer, step.layer)
    if step.effect == "reject":
      label = text.GAP_REVIEW_REJECTED.format(layer=layer, reason=step.reason)
      effect: ReviewEffect = "reject"
    else:
      level = text.CONFIDENCE_LABELS.get(step.level_after, step.level_after)
      label = text.GAP_REVIEW_LOWERED.format(
        level=level, layer=layer, reason=step.reason
      )
      effect = "lower"
    steps.append(
      ReviewStep(
        layer=step.layer,
        effect=effect,
        level_after=step.level_after,
        reason=step.reason,
        label=label,
      )
    )
  return steps


def _reasons(record: CandidateGapRecord) -> list[str]:
  """List each layer's reason in order, or the lone reason a pre-trail card has."""
  return [step.reason for step in record.trail] or [record.confidence_reason]


def _kept_review(record: CandidateGapRecord) -> GapReviewRecord | None:
  """Return the review behind a card's Audit Basis and Minimal Study, if kept."""
  review = record.review
  return review if review is not None and review.verdict == "keep" else None


def _card(  # pylint: disable=too-many-arguments,too-many-positional-arguments
  record: CandidateGapRecord,
  status: Status,
  expander: EvidenceExpander,
  matrices: list[CoverageMatrixRecord],
  checks: list[SaturationCheckRecord],
  grow: _GrowContext,
  group_labels: dict[str, str],
) -> GapCard:
  """Assemble one display card from an artifact record and its judgment."""
  review = _kept_review(record)
  audit_basis = review.audit_basis if review is not None else None
  return GapCard(
    gap_id=record.gap_id,
    key=gap_key(record),
    gap_type=record.gap_type,
    gap_type_label=text.GAP_TYPE_LABELS.get(record.gap_type, record.gap_type),
    title=record.title,
    explanation=record.explanation,
    why=_why(record),
    confidence=record.confidence,
    confidence_label=text.CONFIDENCE_LABELS.get(record.confidence, record.confidence),
    confidence_reason=record.confidence_reason,
    cell_count=record.cell_count,
    corpus_paper_count=record.corpus_paper_count,
    source_citation_keys=record.source_citation_keys,
    paper_keys=list(
      dict.fromkeys(
        [
          *record.source_citation_keys,
          *(link.citation_key for link in record.evidence),
        ]
      )
    ),
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
    audit_basis=audit_basis.text if audit_basis is not None else "",
    audit_basis_evidence_ids=audit_basis.evidence_ids if audit_basis else [],
    minimal_study=(review.minimal_study or "") if review is not None else "",
    data=_data(record, matrices),
    status=status,
    saturation=_saturation(record, checks, grow),
    flagged_by_authors=[
      group_labels.get(group_id, group_id)
      for group_id in record.corroborating_group_ids
    ],
    bucket_label=(
      text.LIMITATION_BUCKET_LABELS.get(record.bucket, record.bucket)
      if record.bucket
      else ""
    ),
    review=_review(record),
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
  gap = _gap_fact(card)
  evidence = [
    GapEvidence(
      citation_key=passage.citation_key,
      section=passage.section,
      passage=_expanded_passage(passage),
    )
    for passage in card.evidence
  ]
  return GroundingContext(papers=papers, gaps=[gap], evidence=evidence)


def confidence_levels_label(levels: Iterable[str]) -> str:
  """Spell confidence levels the way the cards do, in display order."""
  chosen = set(levels)
  labels = [
    text.CONFIDENCE_LABELS.get(level, level)
    for level in CONFIDENCE_LEVELS
    if level in chosen
  ]
  return ", ".join(labels) or text.GAP_SCOPE_NO_LEVELS


def _gap_fact(card: GapCard) -> GapFact:
  """One card as the grounding context carries it."""
  return GapFact(
    gap_type=card.gap_type_label,
    title=card.title,
    explanation=card.explanation,
    confidence=card.confidence_label,
    source_citation_keys=card.source_citation_keys,
  )


def _gap_scope_note(levels: Iterable[str], hidden: int) -> str:
  """Tell the model which confidence levels its gap list covers, and what's hidden."""
  chosen = set(levels)
  if not hidden:
    return text.GAP_SCOPE_NOTE_ALL.format(levels=confidence_levels_label(chosen))
  return text.GAP_SCOPE_NOTE.format(
    levels=confidence_levels_label(chosen),
    hidden=hidden,
    hidden_levels=confidence_levels_label(set(CONFIDENCE_LEVELS) - chosen),
  )


def surfaced_gap_context(
  data: GapCardsData,
  corpus: GroundingContext,
  levels: Iterable[str] = DEFAULT_CONFIDENCE_FILTER,
) -> GroundingContext:
  """
  Narrow a Corpus's grounding context to the gaps the confidence filter shows.

  The general chat discusses what the Gap Cards page surfaces (issue #98), not
  every candidate: on a real Corpus most are Low-confidence cells that would
  swamp the prompt. Papers stay the whole Corpus, the citation allow-list, and
  the context states which levels it holds and how many gaps it leaves out.
  """
  chosen = tuple(levels)
  filtered = filter_by_confidence(data, chosen)
  return corpus.model_copy(
    update={
      "gaps": [_gap_fact(card) for card in filtered.cards],
      "gap_scope": _gap_scope_note(chosen, filtered.hidden_count),
    }
  )


class NarrativeScope(BaseModel):
  """
  What the narrative summary is grounded in, and the denominator it states.

  The write-up reflects the researcher's judgment (issue #81) over the same gaps
  the chat sees (issue #98): only accepted gaps at the confidence levels the
  filter shows enter its context. The status counts are over those shown gaps;
  `hidden_count` and `hidden_accepted_count` say what the filter left out.
  """

  context: GroundingContext
  accepted_count: int
  rejected_count: int
  undecided_count: int
  paper_count: int
  confidence_levels: list[str] = list(CONFIDENCE_LEVELS)
  hidden_count: int = 0
  hidden_accepted_count: int = 0

  @property
  def levels_label(self) -> str:
    """The confidence levels covered, as the cards spell them."""
    return confidence_levels_label(self.confidence_levels)


def narrative_scope(
  data: GapCardsData,
  corpus: GroundingContext,
  levels: Iterable[str] = DEFAULT_CONFIDENCE_FILTER,
) -> NarrativeScope:
  """
  Narrow a Corpus's grounding context to the shown gaps the researcher accepted.

  The Papers stay the whole Corpus, since they are the citation allow-list;
  only the gaps shrink. Rejected, undecided and filtered-out gaps are excluded.
  """
  chosen = [level for level in CONFIDENCE_LEVELS if level in set(levels)]
  filtered = filter_by_confidence(data, chosen)
  shown = filtered.cards
  shown_ids = {card.gap_id for card in shown}
  accepted = [card for card in shown if card.status == "accepted"]
  hidden_accepted = sum(
    1
    for card in data.cards
    if card.status == "accepted" and card.gap_id not in shown_ids
  )
  hidden = filtered.hidden_count
  return NarrativeScope(
    context=corpus.model_copy(
      update={
        "gaps": [_gap_fact(card) for card in accepted],
        "gap_scope": _gap_scope_note(chosen, hidden),
      }
    ),
    accepted_count=len(accepted),
    rejected_count=sum(1 for card in shown if card.status == "rejected"),
    undecided_count=sum(1 for card in shown if card.status is None),
    paper_count=data.corpus_paper_count,
    confidence_levels=chosen,
    hidden_count=hidden,
    hidden_accepted_count=hidden_accepted,
  )


def _expanded_passage(passage: EvidencePassage) -> str:
  """Use the paragraph-expanded window when grounded, else the bare anchor quote."""
  context = passage.context
  if context is not None and context.grounded:
    return context.window
  return passage.passage


def build_gap_cards(  # pylint: disable=too-many-arguments
  artifact: CandidateGapsArtifact,
  judgments: JudgmentLog,
  corpus_root: Path | None = None,
  saturation: SaturationChecksArtifact | None = None,
  *,
  suggested: SuggestedPapersRecord | None = None,
  retrieval: RetrievalGapsArtifact | None = None,
) -> GapCardsData:
  """
  Merge the Candidate Gaps with the researcher's saved accept/reject state.

  When a corpus root is given, each Evidence passage is deterministically widened
  to its containing paragraph (`evidence_context`) so a card shows the anchor in
  situ; without one the passages stay bare, keeping this callable in unit tests
  that have no parsed text on disk. `saturation`, the check log, gives each card
  its wider-literature badge; without it every card reads "not checked".
  `suggested` gives each hit its Grow-path state, and `retrieval` marks the
  hits that are also Retrieval Gap candidates (ADR 0009 §11).
  """
  grow = _GrowContext(
    suggested=suggested or SuggestedPapersRecord(),
    overlaps=retrieval_overlaps(retrieval),
  )
  expander = EvidenceExpander(corpus_root)
  checks_by_gap: dict[str, list[SaturationCheckRecord]] = {}
  for check in (saturation or SaturationChecksArtifact()).checks:
    checks_by_gap.setdefault(check.gap_id, []).append(check)
  group_labels = {
    group.group_id: group.label or group.group_id
    for group in artifact.limitation_groups
  }
  cards = [
    _card(
      record,
      _decision(judgments, record),
      expander,
      artifact.matrices,
      checks_by_gap.get(record.gap_id, []),
      grow,
      group_labels,
    )
    for record in artifact.gaps
  ]
  margins = artifact.l1_smallest_margins
  return GapCardsData(
    cards=cards,
    corpus_paper_count=artifact.corpus_paper_count,
    l1_smallest_margins=(margins.high, margins.medium) if margins else None,
  )


def _decision(judgments: JudgmentLog, record: CandidateGapRecord) -> Status:
  """Return the verdict that applies to a card, if one does."""
  verdict = judgments.verdict_for(gap_key(record))
  return verdict.decision if verdict is not None else None


# The triage filter's names for a card's judgment state; "undecided" stands for a
# card with no saved verdict, which the judgment log represents as no entry.
StatusFilter = Literal["accepted", "rejected", "undecided"]
STATUS_FILTERS: tuple[StatusFilter, ...] = ("accepted", "rejected", "undecided")


def _status_filter(card: GapCard) -> StatusFilter:
  """Name a card's judgment state the way the triage filter does."""
  return card.status if card.status is not None else "undecided"


def filter_gap_cards(
  data: GapCardsData,
  statuses: Collection[StatusFilter] = STATUS_FILTERS,
  gap_types: Collection[str] | None = None,
  confidence_levels: Collection[str] | None = None,
  *,
  possibly_addressed_only: bool = False,
) -> FilteredGapCards:
  """
  Keep the cards matching every filter, in artifact order, counting the rest.

  Filters combine with AND: a card shows only when its status, its Gap Type and
  its confidence level are all selected, and, with `possibly_addressed_only`,
  when its latest wider-literature check found possibly closing work (ADR 0009
  §14). `None` means no filter on that facet. The hidden count is what lets
  the page always say how many gaps the filters keep out of view. A card
  rejected by review is never shown until accepted, whatever the filters, and
  is counted apart from what the filters hide.
  """
  surfaced = [card for card in data.cards if card.surfaced]
  cards = [
    card
    for card in surfaced
    if _status_filter(card) in statuses
    and (gap_types is None or card.gap_type in gap_types)
    and (confidence_levels is None or card.confidence in confidence_levels)
    and (not possibly_addressed_only or is_possibly_addressed(card))
  ]
  return FilteredGapCards(
    cards=cards,
    total_count=data.gap_count,
    review_hidden_count=data.gap_count - len(surfaced),
  )


def is_possibly_addressed(card: GapCard) -> bool:
  """Whether the card's latest wider-literature check found possibly closing work."""
  return card.saturation.status == "possibly_addressed"


# Mirrors the pipeline's saturate.CHECK_FRESHNESS; restated, not imported (ADR
# 0002). "Check all accepted" does not search again for a gap checked within it.
CHECK_FRESHNESS = timedelta(days=30)


class AcceptedCheckPlan(BaseModel):
  """What "check all accepted" would do now: which gaps it searches, which not."""

  to_check: list[str]
  fresh: list[str]

  @property
  def accepted_count(self) -> int:
    """How many accepted gaps can be checked at all."""
    return len(self.to_check) + len(self.fresh)


def plan_accepted_checks(data: GapCardsData, now: datetime) -> AcceptedCheckPlan:
  """
  Split the accepted, checkable gaps into those to search and those still fresh.

  A gap's latest check is fresh when it ran (it is not "not run") within
  `CHECK_FRESHNESS` of `now`; the pipeline applies the same rule, and the page
  uses the count of gaps to search to estimate the run's cost.
  """
  to_check: list[str] = []
  fresh: list[str] = []
  for card in data.cards:
    if card.status != "accepted" or not card.saturation.checkable:
      continue
    checked_at = card.saturation.checked_at
    if (
      checked_at is not None
      and card.saturation.status != "not_run"
      and now - checked_at <= CHECK_FRESHNESS
    ):
      fresh.append(card.gap_id)
    else:
      to_check.append(card.gap_id)
  return AcceptedCheckPlan(to_check=to_check, fresh=fresh)


def next_undecided_gap_id(cards: Sequence[GapCard], current: str | None) -> str | None:
  """
  Return the next undecided card after `current`, wrapping round, or None.

  Starting from the top when there is no current card (or it is no longer in
  the list) and never returning `current` itself, so repeated presses walk
  through every undecided card in order.
  """
  ids = [card.gap_id for card in cards]
  start = ids.index(current) + 1 if current in ids else 0
  for card in [*cards[start:], *cards[:start]]:
    if card.status is None and card.gap_id != current:
      return card.gap_id
  return None
