"""
The detect stage: find Candidate Gaps and write the CandidateGaps artifact.

It builds Coverage Matrices from the NormalizedFacts artifact and turns empty
or sparse cells into Knowledge Gap and Coverage Gap cards, and it groups the
Corpus's limitation statements into Unanswered Limitation cards:

- **Knowledge Gaps** come from the Topic × Topic matrix: two Topics the Corpus
  studies that no Paper (or only one) studies together (`CONTEXT.md` > Gap Type,
  "a topic combination no Paper in the Corpus has studied").
- **Coverage Gaps** come from Topic × Method, Topic × Population, and
  Topic × Dataset: a Topic the Corpus studies, but never (or once) with a
  design, population, or data source the Corpus does use.
- **Unanswered Limitations** come from the Extractions: limitation and
  future-work statements grouped by meaning across Papers (see
  `limitations`), carded only when no later Paper in the Corpus addressed
  the group. Every group, addressed or not, is kept in the artifact so the
  grouping and follow-up decisions are inspectable.

Only categories observed in at least one Paper become matrix rows and columns.
A cell is a gap signal only when both of its categories occur in the Corpus, so
its emptiness is informative ("both exist, never together") rather than a
consequence of the taxonomy listing categories this Corpus never touches. The
taxonomy hierarchy (`parent`) is not rolled up: a Paper counts only in the
categories it was placed on.

The sparse-cell threshold is provisional (`docs/BRIEF.md`): only empty cells
count for Corpora under 25 Papers; empty or single-Paper cells at 25 and above.
Every card states its cell count and the denominator it was computed over.

Matrix detection is deterministic and makes no LLM call; categories, cells,
and gaps are sorted, so the same NormalizedFacts artifact always yields the
same cards. Limitation grouping needs the LLM, through the cached client
(ADR 0001), so a rerun over the same Extractions yields the same groups. Every
card links to Evidence from Papers in the Corpus manifest only
(CODING_STANDARDS > Research integrity).
"""

import logging
from collections.abc import Iterable
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, Field

from research_gap_dashboard.aggregate import (
  CategoryAssignment,
  NormalizedFacts,
  read_normalized_facts,
)
from research_gap_dashboard.corpus_layout import inspect_corpus_layout
from research_gap_dashboard.extract import Evidence, read_extractions
from research_gap_dashboard.ingest import read_manifest
from research_gap_dashboard.limitations import (
  FOLLOW_UP_PROMPT_VERSION,
  GROUP_PROMPT_VERSION,
  LimitationGroup,
  group_limitations,
)
from research_gap_dashboard.llm import LlmClient, ModelTier
from research_gap_dashboard.taxonomy import Axis

logger = logging.getLogger(__name__)

CANDIDATE_GAPS_NAME = "candidate_gaps.json"

# Provisional sparse-cell rule (docs/BRIEF.md, open decision -1): below this
# many Papers only empty cells are gaps; at or above it, single-Paper cells too.
SPARSE_CORPUS_SIZE = 25

CellGapType = Literal["knowledge_gap", "coverage_gap"]
GapType = Literal["knowledge_gap", "coverage_gap", "unanswered_limitation"]
Confidence = Literal["low", "medium", "high"]
EvidenceRole = Literal["row", "column", "cell"]

# The matrices this slice detects over: (row axis, column axis, Gap Type).
MATRIX_SPECS: tuple[tuple[Axis, Axis, CellGapType], ...] = (
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
  "unanswered_limitation": "Unanswered Limitation",
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
  "limitation": (
    "Candidate Unanswered Limitation: {source_count} Paper(s) in the Corpus "
    "({papers}) state this limitation or open question, and none of the "
    "{later_count} later Paper(s) in the Corpus addressed it."
  ),
  "limitation_caveat": (
    " This is a signal for human judgment, not a verdict: it may be addressed "
    "outside the Corpus, or in a passage the extraction did not capture."
  ),
  "limitation_confidence": (
    "{later_count} later Paper(s) in the Corpus were checked for a follow-up."
  ),
  "limitation_no_year": (
    "No later Paper could be checked: the source Papers' publication years are unknown."
  ),
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


class CellGap(BaseModel):
  """One Knowledge or Coverage Gap card: a sparse cell and why it matters."""

  gap_id: str = Field(description="Stable id: '<row axis>x<column axis>:<row>:<col>'.")
  gap_type: CellGapType
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


class StatementEvidence(BaseModel):
  """One source statement behind an Unanswered Limitation card."""

  citation_key: str
  statement_id: str
  statement: str
  evidence: Evidence


class UnansweredLimitationGap(BaseModel):
  """One Unanswered Limitation card: a limitation group no later Paper addressed."""

  gap_id: str = Field(description="Stable id: 'limitation:<group id>'.")
  gap_type: Literal["unanswered_limitation"]
  title: str
  explanation: str
  confidence: Confidence
  confidence_reason: str
  group_id: str
  source_citation_keys: list[str]
  later_paper_count: int = Field(description="Later Papers checked for a follow-up.")
  corpus_paper_count: int = Field(description="The denominator: Papers extracted.")
  evidence: list[StatementEvidence] = Field(min_length=1)


CandidateGap = Annotated[
  CellGap | UnansweredLimitationGap, Field(discriminator="gap_type")
]


class CandidateGapsReport(BaseModel):
  """What the detect stage produced: matrices, groups, gap cards, provenance."""

  corpus_root: Path
  corpus_paper_count: int = Field(description="Papers in the Coverage Matrices.")
  extracted_paper_count: int = Field(description="Papers checked for limitations.")
  sparse_max_count: int = Field(
    description="Cells with at most this many Papers are gap signals."
  )
  normalized_prompt_version: str
  limitation_prompt_versions: list[str]
  tier: ModelTier
  excluded_citation_keys: list[str] = Field(
    description="Papers in upstream artifacts but absent from the manifest."
  )
  matrices: list[CoverageMatrix]
  limitation_groups: list[LimitationGroup]
  gaps: list[CandidateGap]


def sparse_max_count(corpus_paper_count: int) -> int:
  """Return the largest cell count that still counts as a gap for this Corpus."""
  return 0 if corpus_paper_count < SPARSE_CORPUS_SIZE else 1


def detect_corpus(
  root: Path, client: LlmClient, *, tier: ModelTier = "default"
) -> CandidateGapsReport:
  """
  Detect every v1 in-Corpus Gap Type and write the CandidateGaps artifact.

  Reads the manifest, the Extractions, and the NormalizedFacts artifact; an
  entry for a Paper not in the manifest is excluded (and listed) so no card
  cites a Paper outside the Corpus.
  """
  manifest = read_manifest(root)
  corpus_keys = {paper.citation_key for paper in manifest.papers}
  normalized = read_normalized_facts(root)
  extracted = read_extractions(root).extractions

  excluded = sorted(
    {facts.citation_key for facts in normalized.normalized}.union(
      e.citation_key for e in extracted
    ).difference(corpus_keys)
  )
  for key in excluded:
    logger.warning("Ignoring %s: in an upstream artifact but not the manifest.", key)

  papers = sorted(
    (facts for facts in normalized.normalized if facts.citation_key in corpus_keys),
    key=lambda facts: facts.citation_key,
  )
  extractions = [e for e in extracted if e.citation_key in corpus_keys]
  matrices, gaps = _matrix_gaps(papers)

  groups = group_limitations(
    extractions,
    {paper.citation_key: paper.year for paper in manifest.papers},
    client,
    tier=tier,
  )
  gaps.extend(
    _limitation_gap(group, len(extractions)) for group in groups if not group.addressed
  )

  report = CandidateGapsReport(
    corpus_root=root,
    corpus_paper_count=len(papers),
    extracted_paper_count=len(extractions),
    sparse_max_count=sparse_max_count(len(papers)),
    normalized_prompt_version=normalized.prompt_version,
    limitation_prompt_versions=[GROUP_PROMPT_VERSION, FOLLOW_UP_PROMPT_VERSION],
    tier=tier,
    excluded_citation_keys=excluded,
    matrices=matrices,
    limitation_groups=groups,
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


def _matrix_gaps(
  papers: list[NormalizedFacts],
) -> tuple[list[CoverageMatrix], list[CandidateGap]]:
  """Build every Coverage Matrix and card each cell at or under the threshold."""
  threshold = sparse_max_count(len(papers))
  matrices: list[CoverageMatrix] = []
  gaps: list[CandidateGap] = []
  for row_axis, column_axis, gap_type in MATRIX_SPECS:
    matrix = build_matrix(papers, row_axis, column_axis)
    matrices.append(matrix)
    gaps.extend(_gaps_from_matrix(matrix, papers, gap_type, threshold))
  return matrices, gaps


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
  gap_type: CellGapType,
  threshold: int,
) -> list[CellGap]:
  """Turn every cell at or under the sparse threshold into a gap card."""
  rows = {row.category_id: row for row in matrix.rows}
  columns = {column.category_id: column for column in matrix.columns}
  gaps: list[CellGap] = []
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
  gap_type: CellGapType,
) -> CellGap:
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
  return CellGap(
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


def _limitation_gap(group: LimitationGroup, total: int) -> UnansweredLimitationGap:
  """
  Assemble one Unanswered Limitation card from a group nobody followed up.

  Confidence grows with how many later Papers could have addressed the group
  and did not: none checked is low, 1 to 4 medium, 5 or more high. This is a
  v1 heuristic, not a statistical test.
  """
  later = len(group.later_citation_keys)
  confidence: Confidence = "high" if later >= 5 else "medium" if later >= 1 else "low"
  if group.earliest_year is None:
    reason = _TEXT["limitation_no_year"]
  else:
    reason = _TEXT["limitation_confidence"].format(later_count=later)
  sources = group.source_citation_keys
  explanation = _TEXT["limitation"].format(
    source_count=len(sources), papers=", ".join(sources), later_count=later
  )
  return UnansweredLimitationGap(
    gap_id=f"limitation:{group.group_id}",
    gap_type="unanswered_limitation",
    title=group.label,
    explanation=explanation + _TEXT["limitation_caveat"],
    confidence=confidence,
    confidence_reason=reason,
    group_id=group.group_id,
    source_citation_keys=sources,
    later_paper_count=later,
    corpus_paper_count=total,
    evidence=[
      StatementEvidence(
        citation_key=statement.citation_key,
        statement_id=statement.statement_id,
        statement=statement.statement,
        evidence=statement.evidence,
      )
      for statement in group.statements
    ],
  )


def _write_report(root: Path, report: CandidateGapsReport) -> None:
  """Write the CandidateGaps artifact under artifacts/."""
  artifacts_dir = inspect_corpus_layout(root).layout.artifacts_dir
  artifacts_dir.mkdir(parents=True, exist_ok=True)
  path = artifacts_dir / CANDIDATE_GAPS_NAME
  path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
