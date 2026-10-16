"""
The "changed since last run" summary (ADR 0010 §5, issue #93).

Detect keeps the CandidateGaps artifact each run replaces; comparing it with
the current one says what the run did: which gaps closed (and which new Papers
now cover them), which gaps are new, and which of the researcher's verdicts no
longer reach any card. Gaps are matched across runs the way verdicts are
(`judgments.GapKey`): a cell gap by its id, an Unanswered Limitation by its
verbatim source statements, so a limitation that was only regrouped is neither
closed nor new.

Plain functions with no Streamlit dependency; the Home screen and the run
outcome render the `RunChanges` it returns. Without a previous run there is
nothing to compare, and the summary is absent rather than empty.
"""

from pathlib import Path

from pydantic import BaseModel

from research_gap_dashboard.dashboard import text
from research_gap_dashboard.dashboard.artifacts import (
  CandidateGapRecord,
  CandidateGapsArtifact,
  has_candidate_gaps,
  load_candidate_gaps,
  load_previous_candidate_gaps,
)
from research_gap_dashboard.dashboard.judgments import (
  Decision,
  GapJudgment,
  GapKey,
  SourceStatement,
  gap_key,
  load_judgments,
)


class ChangedGap(BaseModel):
  """A gap that appeared or closed between the last two runs."""

  gap_id: str
  title: str
  gap_type_label: str
  # Closed gaps only: the new Papers that now sit in the cell or followed the
  # limitation up. Empty when the gap closed for another reason.
  closing_citation_keys: list[str] = []


class OrphanedVerdict(BaseModel):
  """A saved verdict that reaches no current Candidate Gap."""

  gap_id: str
  title: str
  decision: Decision


class RunChanges(BaseModel):
  """What the last detect run changed, over the Papers it compared."""

  previous_paper_count: int
  current_paper_count: int
  # Empty when the previous artifact predates recording its Papers.
  new_citation_keys: list[str]
  closed_gaps: list[ChangedGap]
  new_gaps: list[ChangedGap]
  orphaned_verdicts: list[OrphanedVerdict]

  @property
  def has_changes(self) -> bool:
    """Whether the run closed or opened a gap or left a verdict orphaned."""
    return bool(self.closed_gaps or self.new_gaps or self.orphaned_verdicts)


def build_run_changes(corpus_root: Path) -> RunChanges | None:
  """Compare the current Candidate Gaps with the run before; None if no such run."""
  if not has_candidate_gaps(corpus_root):
    return None
  previous = load_previous_candidate_gaps(corpus_root)
  if previous is None:
    return None
  current = load_candidate_gaps(corpus_root)
  new_keys = _new_citation_keys(previous, current)
  current_keys = [gap_key(record) for record in current.gaps]
  previous_keys = [gap_key(record) for record in previous.gaps]
  closed = [
    _changed(record, _closing_keys(record, current, new_keys))
    for record, key in zip(previous.gaps, previous_keys)
    if not any(key.same_gap(other) for other in current_keys)
  ]
  opened = [
    _changed(record)
    for record, key in zip(current.gaps, current_keys)
    if not any(key.same_gap(other) for other in previous_keys)
  ]
  orphaned = [
    OrphanedVerdict(
      gap_id=judgment.gap_id,
      title=_verdict_title(judgment, previous),
      decision=judgment.decision,
    )
    for judgment in load_judgments(corpus_root).orphaned(current_keys)
  ]
  return RunChanges(
    previous_paper_count=previous.corpus_paper_count,
    current_paper_count=current.corpus_paper_count,
    new_citation_keys=new_keys,
    closed_gaps=closed,
    new_gaps=opened,
    orphaned_verdicts=orphaned,
  )


def _new_citation_keys(
  previous: CandidateGapsArtifact, current: CandidateGapsArtifact
) -> list[str]:
  """List the Papers the current run counted that the previous one did not."""
  if not previous.paper_citation_keys:
    return []
  return sorted(set(current.paper_citation_keys) - set(previous.paper_citation_keys))


def _changed(
  record: CandidateGapRecord, closing_citation_keys: list[str] | None = None
) -> ChangedGap:
  """Describe one gap that appeared or closed."""
  return ChangedGap(
    gap_id=record.gap_id,
    title=record.title,
    gap_type_label=text.GAP_TYPE_LABELS.get(record.gap_type, record.gap_type),
    closing_citation_keys=closing_citation_keys or [],
  )


def _closing_keys(
  record: CandidateGapRecord, current: CandidateGapsArtifact, new_keys: list[str]
) -> list[str]:
  """Name the new Papers now covering a closed gap's cell or limitation."""
  new = set(new_keys)
  if not new:
    return []
  key = gap_key(record)
  if key.source_statements:
    return sorted(_follow_up_keys(key, current) & new)
  return sorted(_cell_keys(record, current) & new)


def _cell_keys(record: CandidateGapRecord, current: CandidateGapsArtifact) -> set[str]:
  """Return the Papers the current run places in the closed gap's cell."""
  for matrix in current.matrices:
    if (matrix.row_axis, matrix.column_axis) != (record.row_axis, record.column_axis):
      continue
    for cell in matrix.cells:
      # A same-axis matrix stores one triangle, so the cell may be transposed.
      if {cell.row_id, cell.column_id} == {record.row_id, record.column_id}:
        return set(cell.citation_keys)
  return set()


def _follow_up_keys(key: GapKey, current: CandidateGapsArtifact) -> set[str]:
  """Return the Papers that followed up the groups now holding the gap's statements."""
  keys: set[str] = set()
  for group in current.limitation_groups:
    statements = {
      SourceStatement(
        citation_key=statement.citation_key,
        passage=statement.evidence.passage or statement.statement,
      )
      for statement in group.statements
    }
    if statements & key.source_statements:
      keys.update(follow_up.citation_key for follow_up in group.follow_ups)
  return keys


def _verdict_title(judgment: GapJudgment, previous: CandidateGapsArtifact) -> str:
  """Name an orphaned verdict's gap: as saved, else as the previous run had it."""
  if judgment.title:
    return judgment.title
  if judgment.is_legacy_limitation:
    # Its group id may name a different group in every run but its own.
    return judgment.gap_id
  for record in previous.gaps:
    if record.gap_id == judgment.gap_id:
      return record.title
  return judgment.gap_id


def summary_text(changes: RunChanges) -> str:
  """One sentence saying what the run changed, with its denominator."""
  if not changes.has_changes:
    return text.RUN_CHANGES_NOTHING
  return text.RUN_CHANGES_SUMMARY.format(
    closed=len(changes.closed_gaps),
    new=len(changes.new_gaps),
    orphaned=len(changes.orphaned_verdicts),
    current=changes.current_paper_count,
    previous=changes.previous_paper_count,
  )


def details_markdown(changes: RunChanges) -> str:
  """List every closed gap, new gap and orphaned verdict, as Markdown."""
  blocks: list[str] = []
  if changes.new_citation_keys:
    keys = ", ".join(changes.new_citation_keys)
    blocks.append(text.RUN_CHANGES_NEW_PAPERS.format(keys=keys))
  if changes.closed_gaps:
    lines = [
      text.RUN_CHANGES_CLOSED_BY.format(
        type=gap.gap_type_label,
        title=gap.title,
        keys=", ".join(gap.closing_citation_keys),
      )
      if gap.closing_citation_keys
      else text.RUN_CHANGES_CLOSED_OTHER.format(
        type=gap.gap_type_label, title=gap.title
      )
      for gap in changes.closed_gaps
    ]
    blocks.append(_section(text.RUN_CHANGES_CLOSED_HEADING, lines))
  if changes.new_gaps:
    lines = [
      text.RUN_CHANGES_NEW_LINE.format(type=gap.gap_type_label, title=gap.title)
      for gap in changes.new_gaps
    ]
    blocks.append(_section(text.RUN_CHANGES_NEW_HEADING, lines))
  if changes.orphaned_verdicts:
    lines = [
      text.RUN_CHANGES_ORPHANED_LINE.format(
        decision=text.RUN_CHANGES_DECISIONS[verdict.decision], title=verdict.title
      )
      for verdict in changes.orphaned_verdicts
    ]
    blocks.append(_section(text.RUN_CHANGES_ORPHANED_HEADING, lines))
  blocks.append(text.RUN_CHANGES_CAVEAT)
  return "\n\n".join(blocks)


def _section(heading: str, lines: list[str]) -> str:
  """Render a bold heading with its count, then one bullet per line."""
  bullets = "\n".join(f"- {line}" for line in lines)
  return f"{heading.format(count=len(lines))}\n\n{bullets}"
