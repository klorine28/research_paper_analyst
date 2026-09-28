"""
The detect stage: find Candidate Gaps in the Corpus's Coverage Matrices.

This first slice builds Coverage Matrices from the NormalizedFacts artifact and
turns empty or sparse cells into Knowledge Gap and Coverage Gap cards:

- **Knowledge Gaps** come from the Topic × Topic matrix: two Topics the Corpus
  studies that no Paper (or only one) studies together (`CONTEXT.md` > Gap Type,
  "a topic combination no Paper in the Corpus has studied").
- **Coverage Gaps** come from Topic × Method, Topic × Population, and
  Topic × Dataset: a Topic the Corpus studies, but never (or once) with a
  design, population, or data source the Corpus does use.

Only categories observed in at least one Paper become matrix rows and columns.
A cell is a gap signal only when both of its categories occur in the Corpus, so
its emptiness is informative ("both exist, never together") rather than a
consequence of the taxonomy listing categories this Corpus never touches. The
taxonomy hierarchy (`parent`) is not rolled up: a Paper counts only in the
categories it was placed on.

The sparse-cell threshold is provisional (`docs/BRIEF.md`): only empty cells
count for Corpora under 25 Papers; empty or single-Paper cells at 25 and above.
Every card states its cell count and the denominator it was computed over.

Detection is deterministic: no LLM or network call, and categories, cells, and
gaps are sorted, so the same NormalizedFacts artifact always yields the same
CandidateGaps artifact. Every card links to Evidence from Papers in the
Corpus manifest only (CODING_STANDARDS > Research integrity).
"""

import logging
from collections.abc import Iterable
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from research_gap_dashboard.aggregate import (
  CategoryAssignment,
  NormalizedFacts,
  read_normalized_facts,
)
from research_gap_dashboard.corpus_layout import inspect_corpus_layout
from research_gap_dashboard.extract import Evidence
from research_gap_dashboard.ingest import read_manifest
from research_gap_dashboard.taxonomy import Axis

logger = logging.getLogger(__name__)

CANDIDATE_GAPS_NAME = "candidate_gaps.json"

# Provisional sparse-cell rule (docs/BRIEF.md, open decision -1): below this
# many Papers only empty cells are gaps; at or above it, single-Paper cells too.
SPARSE_CORPUS_SIZE = 25

GapType = Literal["knowledge_gap", "coverage_gap"]
Confidence = Literal["low", "medium", "high"]
EvidenceRole = Literal["row", "column", "cell"]

# The matrices this slice detects over: (row axis, column axis, Gap Type).
MATRIX_SPECS: tuple[tuple[Axis, Axis, GapType], ...] = (
  ("topic", "topic", "knowledge_gap"),
  ("topic", "method", "coverage_gap"),
  ("topic", "population", "coverage_gap"),
  ("topic", "dataset", "coverage_gap"),
)

# All user-facing dashboard text this stage writes, kept in one place so it can
# be translated later (CODING_STANDARDS > Language and vocabulary).
_GAP_TYPE_NAMES: dict[GapType, str] = {
  "knowledge_gap": "Knowledge Gap",
  "coverage_gap": "Coverage Gap",
}
_AXIS_NAMES: dict[Axis, str] = {
  "topic": "topic",
  "method": "method",
  "population": "population",
  "dataset": "dataset",
}
_TEXT: dict[str, str] = {
  "title": "{row} × {column}",
  "empty": (
    "Candidate {gap_type}: no Paper in the Corpus combines {row} ({row_axis}) "
    "with {column} ({column_axis}), although {row_count} of {total} Papers cover "
    "{row} and {column_count} of {total} cover {column}."
  ),
  "single": (
    "Candidate {gap_type}: only 1 Paper in the Corpus ({papers}) combines {row} "
    "({row_axis}) with {column} ({column_axis}), although {row_count} of {total} "
    "Papers cover {row} and {column_count} of {total} cover {column}."
  ),
  "caveat": (
    " This is a signal for human judgment, not a verdict: the combination may be "
    "studied outside the Corpus or unrecorded by the extraction."
  ),
  "confidence": (
    "If {row} and {column} were independent across the {total} Papers, about "
    "{expected:.1f} Papers would combine them; {count} do."
  ),
  "confidence_single": " Downgraded one level because 1 Paper does combine them.",
}


class MatrixCategory(BaseModel):
  """One row or column of a Coverage Matrix, and how many Papers it covers."""

  category_id: str
  label: str
  paper_count: int


class MatrixCell(BaseModel):
  """One Coverage Matrix cell: the Papers placed on both its categories."""

  row_id: str
  column_id: str
  paper_count: int
  citation_keys: list[str]


class CoverageMatrix(BaseModel):
  """A two-axis count of Papers per category pair, over the observed categories."""

  row_axis: Axis
  column_axis: Axis
  corpus_paper_count: int = Field(description="The denominator: Papers counted.")
  rows: list[MatrixCategory]
  columns: list[MatrixCategory]
  cells: list[MatrixCell]


class EvidenceLink(BaseModel):
  """One passage in a Corpus Paper behind a gap card, and what it supports."""

  citation_key: str
  role: EvidenceRole = Field(
    description="Supports the row category, the column category, or the cell."
  )
  axis: Axis
  category_id: str
  original_term: str
  fact_ref: str
  evidence: Evidence


class CandidateGap(BaseModel):
  """One Knowledge or Coverage Gap card: a sparse cell and why it matters."""

  gap_id: str = Field(description="Stable id: '<row axis>x<column axis>:<row>:<col>'.")
  gap_type: GapType
  title: str
  explanation: str
  confidence: Confidence
  confidence_reason: str
  row_axis: Axis
  row_id: str
  row_label: str
  column_axis: Axis
  column_id: str
  column_label: str
  cell_count: int = Field(description="Papers placed on both categories.")
  cell_citation_keys: list[str]
  row_paper_count: int
  column_paper_count: int
  corpus_paper_count: int = Field(description="The denominator: Papers counted.")
  evidence: list[EvidenceLink] = Field(min_length=1)


class CandidateGapsReport(BaseModel):
  """What the detect stage produced: matrices, gap cards, and their provenance."""

  corpus_root: Path
  corpus_paper_count: int
  sparse_max_count: int = Field(
    description="Cells with at most this many Papers are gap signals."
  )
  normalized_prompt_version: str
  excluded_citation_keys: list[str] = Field(
    description="NormalizedFacts Papers absent from the manifest, never cited."
  )
  matrices: list[CoverageMatrix]
  gaps: list[CandidateGap]


def sparse_max_count(corpus_paper_count: int) -> int:
  """Return the largest cell count that still counts as a gap for this Corpus."""
  return 0 if corpus_paper_count < SPARSE_CORPUS_SIZE else 1


def detect_corpus(root: Path) -> CandidateGapsReport:
  """
  Detect Knowledge and Coverage Gaps and write the CandidateGaps artifact.

  Reads the manifest and the NormalizedFacts artifact; a NormalizedFacts entry
  for a Paper not in the manifest is excluded (and listed) so no card cites a
  Paper outside the Corpus.
  """
  corpus_keys = {paper.citation_key for paper in read_manifest(root).papers}
  normalized = read_normalized_facts(root)

  papers = sorted(
    (facts for facts in normalized.normalized if facts.citation_key in corpus_keys),
    key=lambda facts: facts.citation_key,
  )
  excluded = sorted(
    facts.citation_key
    for facts in normalized.normalized
    if facts.citation_key not in corpus_keys
  )
  for key in excluded:
    logger.warning("Ignoring NormalizedFacts for %s: not in the manifest.", key)

  threshold = sparse_max_count(len(papers))
  matrices: list[CoverageMatrix] = []
  gaps: list[CandidateGap] = []
  for row_axis, column_axis, gap_type in MATRIX_SPECS:
    matrix = build_matrix(papers, row_axis, column_axis)
    matrices.append(matrix)
    gaps.extend(_gaps_from_matrix(matrix, papers, gap_type, threshold))

  report = CandidateGapsReport(
    corpus_root=root,
    corpus_paper_count=len(papers),
    sparse_max_count=threshold,
    normalized_prompt_version=normalized.prompt_version,
    excluded_citation_keys=excluded,
    matrices=matrices,
    gaps=gaps,
  )
  _write_report(root, report)
  logger.info(
    "Detected %d candidate gaps across %d matrices over %d Papers.",
    len(gaps),
    len(matrices),
    len(papers),
  )
  return report


def build_matrix(
  papers_in: Iterable[NormalizedFacts], row_axis: Axis, column_axis: Axis
) -> CoverageMatrix:
  """
  Count Papers per category pair over the categories observed on each axis.

  A same-axis matrix (Topic × Topic) keeps only the cells above the diagonal:
  a category paired with itself is not a combination, and (a, b) is (b, a).
  """
  papers = list(papers_in)
  rows = _observed_categories(papers, row_axis)
  columns = _observed_categories(papers, column_axis)

  cells: list[MatrixCell] = []
  for row in rows:
    for column in columns:
      if row_axis == column_axis and row.category_id >= column.category_id:
        continue
      keys = [
        facts.citation_key
        for facts in papers
        if row.category_id in facts.category_ids(row_axis)
        and column.category_id in facts.category_ids(column_axis)
      ]
      cells.append(
        MatrixCell(
          row_id=row.category_id,
          column_id=column.category_id,
          paper_count=len(keys),
          citation_keys=keys,
        )
      )

  return CoverageMatrix(
    row_axis=row_axis,
    column_axis=column_axis,
    corpus_paper_count=len(papers),
    rows=rows,
    columns=columns,
    cells=cells,
  )


def read_candidate_gaps(root: Path) -> CandidateGapsReport:
  """Read the CandidateGaps artifact a previous detect run wrote."""
  path = inspect_corpus_layout(root).layout.artifacts_dir / CANDIDATE_GAPS_NAME
  return CandidateGapsReport.model_validate_json(path.read_text(encoding="utf-8"))


def _observed_categories(
  papers: list[NormalizedFacts], axis: Axis
) -> list[MatrixCategory]:
  """List each category any Paper was placed on for this axis, sorted by id."""
  labels: dict[str, str] = {}
  counts: dict[str, int] = {}
  for facts in papers:
    for category_id in facts.category_ids(axis):
      counts[category_id] = counts.get(category_id, 0) + 1
    for assignment in facts.assignments:
      if assignment.axis == axis:
        labels.setdefault(assignment.category_id, assignment.category_label)
  return [
    MatrixCategory(
      category_id=category_id,
      label=labels[category_id],
      paper_count=counts[category_id],
    )
    for category_id in sorted(counts)
  ]


def _gaps_from_matrix(
  matrix: CoverageMatrix,
  papers: list[NormalizedFacts],
  gap_type: GapType,
  threshold: int,
) -> list[CandidateGap]:
  """Turn every cell at or under the sparse threshold into a gap card."""
  rows = {row.category_id: row for row in matrix.rows}
  columns = {column.category_id: column for column in matrix.columns}
  gaps: list[CandidateGap] = []
  for cell in matrix.cells:
    if cell.paper_count > threshold:
      continue
    gaps.append(
      _build_gap(
        matrix, rows[cell.row_id], columns[cell.column_id], cell, papers, gap_type
      )
    )
  return gaps


def _build_gap(  # pylint: disable=too-many-arguments,too-many-positional-arguments
  matrix: CoverageMatrix,
  row: MatrixCategory,
  column: MatrixCategory,
  cell: MatrixCell,
  papers: list[NormalizedFacts],
  gap_type: GapType,
) -> CandidateGap:
  """Assemble one gap card with its explanation, confidence, and Evidence."""
  total = matrix.corpus_paper_count
  confidence, confidence_reason = _confidence(row, column, cell, total)
  template = _TEXT["empty"] if cell.paper_count == 0 else _TEXT["single"]
  explanation = template.format(
    gap_type=_GAP_TYPE_NAMES[gap_type],
    row=row.label,
    column=column.label,
    row_axis=_AXIS_NAMES[matrix.row_axis],
    column_axis=_AXIS_NAMES[matrix.column_axis],
    row_count=row.paper_count,
    column_count=column.paper_count,
    total=total,
    papers=", ".join(cell.citation_keys),
  )
  return CandidateGap(
    gap_id=f"{matrix.row_axis}x{matrix.column_axis}:{row.category_id}:{column.category_id}",
    gap_type=gap_type,
    title=_TEXT["title"].format(row=row.label, column=column.label),
    explanation=explanation + _TEXT["caveat"],
    confidence=confidence,
    confidence_reason=confidence_reason,
    row_axis=matrix.row_axis,
    row_id=row.category_id,
    row_label=row.label,
    column_axis=matrix.column_axis,
    column_id=column.category_id,
    column_label=column.label,
    cell_count=cell.paper_count,
    cell_citation_keys=cell.citation_keys,
    row_paper_count=row.paper_count,
    column_paper_count=column.paper_count,
    corpus_paper_count=total,
    evidence=_evidence(papers, matrix, row, column, cell),
  )


def _confidence(
  row: MatrixCategory, column: MatrixCategory, cell: MatrixCell, total: int
) -> tuple[Confidence, str]:
  """
  Grade how surprising a sparse cell is, from its categories' marginal counts.

  The rubric compares the cell with the count expected if the two categories
  were independent across the Corpus: an empty cell where two or more Papers
  were expected is more telling than one where under one was. A single-Paper
  cell is downgraded one level. This is a v1 heuristic, not a statistical test.
  """
  expected = row.paper_count * column.paper_count / total if total else 0.0
  levels: tuple[Confidence, ...] = ("low", "medium", "high")
  level = 2 if expected >= 2 else 1 if expected >= 1 else 0
  reason = _TEXT["confidence"].format(
    row=row.label,
    column=column.label,
    total=total,
    expected=expected,
    count=cell.paper_count,
  )
  if cell.paper_count > 0:
    level = max(level - 1, 0)
    reason += _TEXT["confidence_single"]
  return levels[level], reason


def _evidence(
  papers: list[NormalizedFacts],
  matrix: CoverageMatrix,
  row: MatrixCategory,
  column: MatrixCategory,
  cell: MatrixCell,
) -> list[EvidenceLink]:
  """
  Link the passages behind both of a cell's categories.

  An empty cell has no Paper of its own to cite, so its Evidence is the Papers
  that establish each category exists in the Corpus; a Paper in the cell is
  cited with the role 'cell' instead.
  """
  in_cell = set(cell.citation_keys)
  links: list[EvidenceLink] = []
  for facts in papers:
    for assignment in facts.assignments:
      role = _role(assignment, matrix, row, column, facts.citation_key in in_cell)
      if role is not None:
        links.append(_link(facts.citation_key, role, assignment))
  return links


def _role(
  assignment: CategoryAssignment,
  matrix: CoverageMatrix,
  row: MatrixCategory,
  column: MatrixCategory,
  in_cell: bool,
) -> EvidenceRole | None:
  """Say which side of the cell an assignment supports, if either."""
  if assignment.axis == matrix.row_axis and assignment.category_id == row.category_id:
    return "cell" if in_cell else "row"
  if (
    assignment.axis == matrix.column_axis
    and assignment.category_id == column.category_id
  ):
    return "cell" if in_cell else "column"
  return None


def _link(
  citation_key: str, role: EvidenceRole, assignment: CategoryAssignment
) -> EvidenceLink:
  """Build one Evidence link from the assignment that placed a Paper on a category."""
  return EvidenceLink(
    citation_key=citation_key,
    role=role,
    axis=assignment.axis,
    category_id=assignment.category_id,
    original_term=assignment.original_term,
    fact_ref=assignment.fact_ref,
    evidence=assignment.evidence,
  )


def _write_report(root: Path, report: CandidateGapsReport) -> None:
  """Write the CandidateGaps artifact under artifacts/."""
  artifacts_dir = inspect_corpus_layout(root).layout.artifacts_dir
  artifacts_dir.mkdir(parents=True, exist_ok=True)
  path = artifacts_dir / CANDIDATE_GAPS_NAME
  path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
