"""
Assembling the Unanswered Limitations view from the CandidateGaps artifact.

These are plain functions with no Streamlit dependency: the layout layer stays
thin by asking here for a finished LimitationsData. The detect stage keeps every
limitation group, addressed or not, in the artifact so the grouping and
follow-up decisions are inspectable (see `detect`); this view shows them all,
each with its follow-up count and the verbatim source passages behind it, and
marks whether a later Paper in the Corpus addressed it. Groups keep the
artifact's order so the same artifact always lists them the same way.
"""

from pydantic import BaseModel

from research_gap_dashboard.dashboard.artifacts import (
  CandidateGapsArtifact,
  LimitationGroupRecord,
)


class SourceStatement(BaseModel):
  """One verbatim source passage in a limitation group, and its Paper."""

  citation_key: str
  statement: str
  passage: str
  section: str


class FollowUpPassage(BaseModel):
  """One later Paper's passage that addressed a group, and why it did."""

  citation_key: str
  passage: str
  section: str
  reason: str


class LimitationGroupView(BaseModel):
  """One limitation group the view renders: statements, follow-ups, counts."""

  group_id: str
  label: str
  earliest_year: int | None
  source_citation_keys: list[str]
  later_paper_count: int
  follow_up_count: int
  statements: list[SourceStatement]
  follow_ups: list[FollowUpPassage]

  @property
  def addressed(self) -> bool:
    """Report whether any later Paper in the Corpus addressed this group."""
    return self.follow_up_count > 0

  @property
  def source_paper_count(self) -> int:
    """How many distinct Papers state this limitation."""
    return len(self.source_citation_keys)


class LimitationsData(BaseModel):
  """Everything the Unanswered Limitations page shows, already computed."""

  groups: list[LimitationGroupView]
  extracted_paper_count: int

  @property
  def group_count(self) -> int:
    """How many limitation groups the Corpus produced."""
    return len(self.groups)

  @property
  def unanswered_count(self) -> int:
    """How many groups no later Paper in the Corpus addressed."""
    return sum(1 for group in self.groups if not group.addressed)

  @property
  def addressed_count(self) -> int:
    """How many groups a later Paper in the Corpus addressed."""
    return sum(1 for group in self.groups if group.addressed)


def _source_statements(record: LimitationGroupRecord) -> list[SourceStatement]:
  """List every source passage behind a group, in the artifact's order."""
  return [
    SourceStatement(
      citation_key=statement.citation_key,
      statement=statement.statement,
      passage=statement.evidence.passage,
      section=statement.evidence.section,
    )
    for statement in record.statements
  ]


def _follow_up_passages(record: LimitationGroupRecord) -> list[FollowUpPassage]:
  """List every later-Paper passage that addressed a group, in artifact order."""
  return [
    FollowUpPassage(
      citation_key=follow_up.citation_key,
      passage=follow_up.evidence.passage,
      section=follow_up.evidence.section,
      reason=follow_up.reason,
    )
    for follow_up in record.follow_ups
  ]


def _group(record: LimitationGroupRecord) -> LimitationGroupView:
  """Assemble one display group from an artifact record."""
  source_keys = sorted({statement.citation_key for statement in record.statements})
  return LimitationGroupView(
    group_id=record.group_id,
    label=record.label,
    earliest_year=record.earliest_year,
    source_citation_keys=source_keys,
    later_paper_count=len(record.later_citation_keys),
    follow_up_count=len(record.follow_ups),
    statements=_source_statements(record),
    follow_ups=_follow_up_passages(record),
  )


def build_limitations(artifact: CandidateGapsArtifact) -> LimitationsData:
  """Assemble the Unanswered Limitations view from the CandidateGaps artifact."""
  return LimitationsData(
    groups=[_group(record) for record in artifact.limitation_groups],
    extracted_paper_count=artifact.extracted_paper_count,
  )
