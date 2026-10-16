"""
Assembling the Gap Cards view from the CandidateGaps artifact and judgments.

These are plain functions with no Streamlit dependency: the layout layer stays
thin by asking here for finished GapCardsData. Each card carries every field the
view shows, including the verbatim Evidence passages, and the researcher's
accept/reject state merged in from the judgment log so a restored session shows
the same verdicts. Cards keep the artifact's order so the same artifact always
lists gaps the same way.
"""

from collections.abc import Collection, Iterable, Sequence
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
  SaturationCheckRecord,
  SaturationChecksArtifact,
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


class CategoryPapers(BaseModel):
  """The Papers placed on one category of a gap, or behind a limitation."""

  label: str
  citation_keys: list[str]


class NeighbourCell(BaseModel):
  """One Coverage Matrix cell that shares a category with a gap's cell."""

  row_label: str
  column_label: str
  paper_count: int


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


class SaturationSearchView(BaseModel):
  """One search a saturation check ran, as the card's Tier 3 lists it."""

  source: str
  query: str
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


class SaturationView(BaseModel):
  """
  A card's wider-literature check (ADR 0009 §14): badge, result, drill-down.

  `status` is "not_checked" until a check runs; afterwards the latest snapshot
  fills the rest and `history` lists every snapshot, newest first. Only cell
  gaps (Knowledge and Coverage) are `checkable` so far.
  """

  checkable: bool
  status: str = "not_checked"
  badge: str
  summary: str = ""
  checked_on: str = ""
  screening_method: str = ""
  searches: list[SaturationSearchView] = []
  hits: list[SaturationHitView] = []
  screened_count: int = 0
  llm_screening: str = Field(
    default="", description="How the LLM screening ran, with model and prompt."
  )
  history: list[str] = []


class GapCard(BaseModel):
  """
  One Candidate Gap card the view renders, with its judgment state.

  Fields map onto ADR 0008's three tiers: Tier 1 is the title, confidence, `why`
  and cell count; Tier 2 the Evidence; Tier 3 `data`. `saturation` is the
  wider-literature check, shown across all three tiers.
  """

  gap_id: str
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
  data: GapData
  status: Status
  saturation: SaturationView


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


# Levels in display order, and the default view: Low-confidence gaps are hidden
# until the researcher asks for them (issue #108), but never silently, since the
# page always states how many are hidden.
CONFIDENCE_LEVELS: tuple[str, ...] = ("high", "medium", "low")
DEFAULT_CONFIDENCE_FILTER: tuple[str, ...] = ("high", "medium")


class FilteredGapCards(BaseModel):
  """The cards the Gap Cards filters keep, and how many they hide (#79, #108)."""

  cards: list[GapCard]
  total_count: int

  @property
  def shown_count(self) -> int:
    """How many gaps pass the filter."""
    return len(self.cards)

  @property
  def hidden_count(self) -> int:
    """How many gaps the filter hides."""
    return self.total_count - self.shown_count


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


def _saturation(
  record: CandidateGapRecord, checks: list[SaturationCheckRecord]
) -> SaturationView:
  """Build a card's wider-literature view from its checks, latest last."""
  checkable = _is_cell_gap(record)
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
    checked_on=latest.checked_at.strftime("%Y-%m-%d %H:%M"),
    screening_method=latest.screening_method,
    searches=[
      SaturationSearchView(
        source=search.source,
        query=search.query,
        returned_count=len(search.returned_work_ids),
        in_corpus_count=len(search.in_corpus_work_ids),
        error=search.error,
      )
      for search in latest.searches
    ],
    hits=[
      SaturationHitView(
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
      )
      for hit in latest.hits
    ],
    screened_count=sum(1 for hit in latest.hits if hit.counts),
    llm_screening=_llm_screening(latest),
    history=[
      text.GAP_SATURATION_HISTORY_ENTRY.format(
        date=check.checked_at.strftime("%Y-%m-%d %H:%M"), badge=_badge(check)
      )
      for check in reversed(checks)
    ],
  )


def _reasons(record: CandidateGapRecord) -> list[str]:
  """List each layer's reason in order, or the lone reason a pre-trail card has."""
  return [step.reason for step in record.trail] or [record.confidence_reason]


def _card(  # pylint: disable=too-many-arguments,too-many-positional-arguments
  record: CandidateGapRecord,
  status: Status,
  expander: EvidenceExpander,
  matrices: list[CoverageMatrixRecord],
  checks: list[SaturationCheckRecord],
) -> GapCard:
  """Assemble one display card from an artifact record and its judgment."""
  return GapCard(
    gap_id=record.gap_id,
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
    data=_data(record, matrices),
    status=status,
    saturation=_saturation(record, checks),
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


class NarrativeScope(BaseModel):
  """
  What the narrative summary is grounded in, and the denominator it states.

  The write-up reflects the researcher's judgment (issue #81): only accepted
  gaps enter its context, and the counts let the page say how many gaps over
  how many Papers it covers and how many were left out.
  """

  context: GroundingContext
  accepted_count: int
  rejected_count: int
  undecided_count: int
  paper_count: int


def narrative_scope(data: GapCardsData, corpus: GroundingContext) -> NarrativeScope:
  """
  Narrow a Corpus's grounding context to the gaps the researcher accepted.

  The Papers stay the whole Corpus, since they are the citation allow-list;
  only the gaps shrink. Rejected and undecided gaps are excluded.
  """
  gaps = [
    GapFact(
      gap_type=card.gap_type_label,
      title=card.title,
      explanation=card.explanation,
      confidence=card.confidence_label,
      source_citation_keys=card.source_citation_keys,
    )
    for card in data.cards
    if card.status == "accepted"
  ]
  return NarrativeScope(
    context=corpus.model_copy(update={"gaps": gaps}),
    accepted_count=data.accepted_count,
    rejected_count=data.rejected_count,
    undecided_count=data.undecided_count,
    paper_count=data.corpus_paper_count,
  )


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
  saturation: SaturationChecksArtifact | None = None,
) -> GapCardsData:
  """
  Merge the Candidate Gaps with the researcher's saved accept/reject state.

  When a corpus root is given, each Evidence passage is deterministically widened
  to its containing paragraph (`evidence_context`) so a card shows the anchor in
  situ; without one the passages stay bare, keeping this callable in unit tests
  that have no parsed text on disk. `saturation`, the check log, gives each card
  its wider-literature badge; without it every card reads "not checked".
  """
  decided = judgments.by_gap_id()
  expander = EvidenceExpander(corpus_root)
  checks_by_gap: dict[str, list[SaturationCheckRecord]] = {}
  for check in (saturation or SaturationChecksArtifact()).checks:
    checks_by_gap.setdefault(check.gap_id, []).append(check)
  cards = [
    _card(
      record,
      decided[record.gap_id].decision if record.gap_id in decided else None,
      expander,
      artifact.matrices,
      checks_by_gap.get(record.gap_id, []),
    )
    for record in artifact.gaps
  ]
  return GapCardsData(cards=cards, corpus_paper_count=artifact.corpus_paper_count)


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
) -> FilteredGapCards:
  """
  Keep the cards matching every filter, in artifact order, counting the rest.

  Filters combine with AND: a card shows only when its status, its Gap Type and
  its confidence level are all selected. `None` means no filter on that facet.
  The hidden count is what lets the page always say how many gaps the filters
  keep out of view.
  """
  cards = [
    card
    for card in data.cards
    if _status_filter(card) in statuses
    and (gap_types is None or card.gap_type in gap_types)
    and (confidence_levels is None or card.confidence in confidence_levels)
  ]
  return FilteredGapCards(cards=cards, total_count=data.gap_count)


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
