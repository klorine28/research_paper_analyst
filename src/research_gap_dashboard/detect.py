"""
The detect stage: find Candidate Gaps and write the CandidateGaps artifact.

It builds Coverage Matrices from the NormalizedFacts artifact and turns candidate
cells into Knowledge Gap and Coverage Gap cards, and it groups the
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
consequence of the taxonomy listing categories this Corpus never touches. A
Paper counts only in the categories it was placed on; the taxonomy hierarchy
the NormalizedFacts artifact records is applied afterwards, as a layer.

The candidate net is one rule at every Corpus size (ADR 0008 §2): a cell is a
candidate if it is empty, or holds one Paper while more than one was expected
if its two categories were independent. Every card states its cell count and
the denominator it was computed over.

Each cell card records a verdict trail: one step per detection layer, in the
order applied (ADR 0008). L1 opens every trail: the one-sided hypergeometric
lower tail `P(X <= observed)` of the cell given its two category sizes, a
ranking score and never a significance test (no multiplicity correction, and
no card wording claims one). It is computed exactly with integer
combinatorics, and its cut points are compared as fractions so a probability
of exactly 1/20 ranks High on every platform.
The taxonomy roll-up (L2) then rejects a cell pairing a category with its own
ancestor and lowers one where a Paper placed only on a broader category may
cover it. A rejected candidate stays in the artifact with its reason. With no
hierarchy recorded, the roll-up adds nothing.
The bridges layer then looks for categories, on any axis, that each link both
sides of a surviving cell through at least two Papers; two or more raise it one
level. Each card stores its bridges, strongest (Adamic-Adar) first.

Two witnesses come last: after grouping, every limitation group is tagged
(one cached cheap-tier call) with a limitation type and the observed
categories it concerns, and a cell is corroborated when one group names both
its categories, or one of them plus a type routed to the other's axis
(`BUCKET_AXES`). Corroboration raises one level and records the groups; the
layer is boost-only, so a cell no author mentions keeps its level and only
says so. Generic types (sample size, follow-up, analysis, other) corroborate
nothing.

The LLM review (L5, see `review`) comes last. It runs on every cell candidate
still at Medium or above and not rejected, and on every Unanswered Limitation,
and keeps, lowers one level, or rejects; it never raises. Low cells make no
review call, so they carry no Audit Basis or Minimal Study.

When an `Embedder` is injected, cross-Paper limitation pairs whose embeddings
are close are passed to grouping as merge hints, and the embedding model id,
revision, threshold, and every hint are recorded in the artifact; without one
(the `embeddings` extra absent) grouping runs LLM-only.

Matrix detection before the review is deterministic and makes no LLM call;
categories, cells, and gaps are sorted, so the same NormalizedFacts artifact
always yields the same candidates. Limitation grouping and the review need the
LLM, through the cached client (ADR 0001), so a rerun over the same inputs
yields the same groups and verdicts at no cost. Every card links to Evidence
from Papers in the Corpus manifest only
(CODING_STANDARDS > Research integrity).

A re-run first moves the artifact it replaces to `candidate_gaps.previous.json`,
keeping its modification time, so the dashboard can say what changed since the
last run and tell which artifact a researcher's older verdicts were made on.
"""

# pylint: disable=too-many-lines

import logging
import math
import shutil
from collections.abc import Callable, Iterable
from fractions import Fraction
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, model_validator

from research_gap_dashboard.aggregate import (
  CategoryAssignment,
  NormalizedFacts,
  read_normalized_facts,
)
from research_gap_dashboard.corpus_layout import inspect_corpus_layout
from research_gap_dashboard.embeddings import (
  DEFAULT_MERGE_THRESHOLD,
  Embedder,
  EmbeddingProvenance,
)
from research_gap_dashboard.extract import Evidence, read_extractions
from research_gap_dashboard.ingest import read_manifest
from research_gap_dashboard.limitations import (
  FOLLOW_UP_PROMPT_VERSION,
  GROUP_PROMPT_VERSION,
  TAG_PROMPT_VERSION,
  LimitationBucket,
  LimitationGroup,
  MergeHint,
  collect_statements,
  group_limitations,
  merge_hints,
  tag_limitations,
)
from research_gap_dashboard.llm import LlmClient, ModelTier
from research_gap_dashboard.review import (
  REJECTION_CLASS_NAMES,
  REVIEW_PROMPT_VERSION,
  GapReview,
  ReviewEvidence,
  ReviewRequest,
  review_gap,
)
from research_gap_dashboard.taxonomy import AXES, Axis

logger = logging.getLogger(__name__)

CANDIDATE_GAPS_NAME = "candidate_gaps.json"
# The artifact the latest run replaced; the "changed since last run" baseline.
PREVIOUS_CANDIDATE_GAPS_NAME = "candidate_gaps.previous.json"

# L1 starting levels (ADR 0008 §1): the lower-tail probability at or under
# which an empty-or-thin cell starts High, then Medium; anything above is Low.
L1_HIGH_MAX = Fraction(1, 20)
L1_MEDIUM_MAX = Fraction(1, 5)

CellGapType = Literal["knowledge_gap", "coverage_gap"]
GapType = Literal["knowledge_gap", "coverage_gap", "unanswered_limitation"]
Confidence = Literal["low", "medium", "high"]
EvidenceRole = Literal["row", "column", "cell"]
LayerEffect = Literal["start", "raise", "lower", "reject", "none"]

# The layer whose verdict opens every cell candidate's trail.
L1_LAYER = "l1"
# The layer that opened the trail of artifacts written before L1: a pre-trail
# artifact's single start step is rebuilt under this name.
HEURISTIC_LAYER = "heuristic"
# ADR 0008 L2: rejects or lowers cells that are artifacts of the hierarchy.
ROLL_UP_LAYER = "taxonomy_roll_up"
# ADR 0008 bridges: raises cells whose two sides share bridging categories.
BRIDGES_LAYER = "bridges"
# A bridge's link to each side must be backed by at least this many Papers.
BRIDGE_MIN_LINK_PAPERS = 2
# A cell needs at least this many bridges to be raised one level.
BRIDGE_MIN_COUNT = 2
# A category on at least this share of the Corpus's Papers links everything,
# so it never bridges (e.g. "humans").
NEAR_UNIVERSAL_SHARE = Fraction(9, 10)
# ADR 0008 two witnesses: author-stated limitations corroborating a cell.
TWO_WITNESS_LAYER = "two_witness"
# The tier limitation tagging runs on: one batched, low-stakes call.
TAG_TIER: ModelTier = "cheap"

# The axis each limitation type can corroborate (spec #112); None for the
# generic types, which never corroborate a specific cell.
BUCKET_AXES: dict[LimitationBucket, Axis | None] = {
  "study_design": "method",
  "population": "population",
  "setting_data": "dataset",
  "measurement": "topic",
  "open_question": "topic",
  "sample_size": None,
  "follow_up": None,
  "analysis": None,
  "other": None,
}
# ADR 0008 L5: the LLM review; it keeps, lowers or rejects, never raises.
REVIEW_LAYER = "l5_review"
# The layer that opens an Unanswered Limitation's trail: its follow-up check.
FOLLOW_UP_LAYER = "limitation_follow_up"
# Cell candidates below this level skip the review (ADR 0008 §1: Medium+ only).
REVIEW_MIN_LEVEL: Confidence = "medium"
# At most this many Evidence items per role go to the reviewer, in artifact
# order; grounds outside them are dropped like any other unknown id.
REVIEW_EVIDENCE_PER_ROLE = 8
# At most this many neighbouring cells describe what surrounds a cell gap.
REVIEW_NEIGHBOURS = 8

_LEVELS: tuple[Confidence, ...] = ("low", "medium", "high")

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
    "About {expected:.1f} of {total} Papers would combine {row} and {column} if "
    "the two were independent; {count} do. {chance} {level}"
  ),
  "chance_rare": (
    "With these category sizes, chance alone would leave this cell with so few Papers "
    "about 1 time in {one_in}."
  ),
  "chance_common": (
    "With these category sizes, chance alone would leave this cell with so few Papers "
    "more often than not."
  ),
  "level_high": "Ranked High: 1 time in 20 or rarer.",
  "level_medium": "Ranked Medium: rarer than 1 time in 5, but not 1 in 20.",
  "level_low": "Ranked Low: 1 time in 5 or more often.",
  "candidate_rule": (
    "A cell is a candidate if it is empty, or holds one Paper while more than one "
    "was expected (row Papers × column Papers / Corpus Papers above 1)."
  ),
  "legacy_rule": "A cell is a candidate if it holds at most {count} Papers.",
  "roll_up_reject": (
    "Rejected as a taxonomy artifact: {broad} is a broader {axis} category "
    "that contains {narrow}, so pairing them is not a combination of two subjects."
  ),
  "roll_up_lower": (
    "Lowered one level: Paper(s) {papers} sit in the broader cell {broader} "
    "without being placed on any narrower category, so they may or may not "
    "cover this combination; check them."
  ),
  "roll_up_lower_floor": (
    "Already at the lowest level: Paper(s) {papers} sit in the broader cell "
    "{broader} without being placed on any narrower category, so they may or "
    "may not cover this combination; check them."
  ),
  "roll_up_sibling": (
    "No change: Paper(s) {papers} sit in the broader cell {broader}, but each "
    "is also placed on a narrower category, so none counts as covering this "
    "combination."
  ),
  "roll_up_broader": "{label} × {other}",
  "bridges_raise": (
    "Raised one level: {count} categories each link both sides of this cell "
    "through at least {min_links} Papers, strongest first: {bridges}."
  ),
  "bridges_ceiling": (
    "Already at the highest level: {count} categories each link both sides of "
    "this cell through at least {min_links} Papers, strongest first: {bridges}."
  ),
  "bridges_one": (
    "No change: only {count} category links both sides of this cell through at "
    "least {min_links} Papers ({bridges}); {min_count} are needed to raise it."
  ),
  "bridge": "{label} ({axis}; {row_count} and {column_count} Papers)",
  "witness_raise": (
    "Raised one level, also flagged by authors: limitation group(s) {groups} "
    "stated in the Corpus point at this combination."
  ),
  "witness_ceiling": (
    "Already at the highest level; also flagged by authors: limitation "
    "group(s) {groups} stated in the Corpus point at this combination."
  ),
  "witness_related": (
    "No change: limitation group(s) {groups} concern one side of this combination only."
  ),
  "witness_none": (
    "No change: no author in the Corpus states a limitation pointing at this "
    "combination."
  ),
  "witness_group": "{group_id} ({label})",
  "review_keep": "Kept by review: {warrant}",
  "review_lower": "Lowered one level by review (weakly supported): {warrant}",
  "review_lower_floor": (
    "Kept by review as weakly supported; already at the lowest level: {warrant}"
  ),
  "review_reject": "Rejected by review as {rejection_class}: {warrant}",
  "review_reject_unnamed": "Rejected by review: {warrant}",
  "review_failed": (
    "Not reviewed: the review returned no usable answer, so the level stands "
    "as the earlier layers left it."
  ),
  "review_neighbour": "{row} × {column}: {count} Paper(s)",
  "review_later": (
    "{count} later Paper(s) in the Corpus were checked for a follow-up; none "
    "addressed it."
  ),
  "review_no_later": "No later Paper in the Corpus could be checked for a follow-up.",
  "review_evidence_cell": "{term} (placed on {category}): {passage}",
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

  @property
  def evidence_id(self) -> str:
    """
    The id the review cites this passage by: '<citation key>:<field>[<index>]'.

    Aggregate writes fact refs bracketed ('[methods[0]]'); the outer pair is
    dropped so the id reads like a limitation statement id.
    """
    ref = self.fact_ref
    if ref.startswith("[") and ref.endswith("]]"):
      ref = ref[1:-1]
    return f"{self.citation_key}:{ref}"


class LayerStep(BaseModel):
  """One detection layer's verdict on a cell candidate (ADR 0008 §1, §5)."""

  layer: str = Field(description="The layer that judged the candidate.")
  effect: LayerEffect
  level_after: Confidence = Field(description="The confidence after this step.")
  reason: str = Field(description="Why, in plain words.")


class Bridge(BaseModel):
  """
  A category that links both sides of a cell, each link backed by >= 2 Papers.

  `weight` is its Adamic-Adar weight, 1 / ln(degree) in the category
  co-occurrence graph: a category that co-occurs with fewer others says more
  about the two sides it links. It orders bridges; it decides nothing.
  """

  axis: Axis
  category_id: str
  label: str
  row_link_count: int = Field(description="Papers on both the row category and it.")
  column_link_count: int = Field(
    description="Papers on both the column category and it."
  )
  degree: int = Field(description="Categories it co-occurs with in any Paper.")
  weight: float = Field(description="Adamic-Adar weight: 1 / ln(degree).")


class CellGap(BaseModel):
  """
  One Knowledge or Coverage Gap card: a sparse cell and why it matters.

  `trail` lists every layer's verdict in the order applied; the first step is
  always a `start`, and `confidence` is the final level. An artifact written
  before trails existed reads with a single start step rebuilt from its
  recorded confidence and reason, so old artifacts stay readable.
  """

  gap_id: str = Field(description="Stable id: '<row axis>x<column axis>:<row>:<col>'.")
  gap_type: CellGapType
  title: str
  explanation: str
  confidence: Confidence = Field(description="The final level after every layer.")
  confidence_reason: str
  trail: list[LayerStep] = Field(min_length=1)
  l1_probability: float = Field(
    ge=0, le=1, description="L1: P(X <= cell count) under independence."
  )
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
  bridges: list[Bridge] = Field(
    default_factory=list,
    description="Bridging categories, strongest first; empty before bridges.",
  )
  corroborating_group_ids: list[str] = Field(
    default_factory=list,
    description="Limitation groups that corroborate this cell (two witnesses).",
  )
  review: GapReview | None = Field(
    default=None, description="The L5 verdict; None when the review did not run."
  )

  @property
  def rejected(self) -> bool:
    """Whether a layer rejected this candidate; it stays in the artifact (§5)."""
    return any(step.effect == "reject" for step in self.trail)

  @model_validator(mode="before")
  @classmethod
  def _fill_fields_of_older_artifacts(cls, data: Any) -> Any:
    """
    Give an older artifact's card the fields it was written without.

    A pre-trail card gets its single heuristic start step; a pre-L1 card gets
    its L1 score recomputed from its recorded counts, which fully determine it.
    """
    if not isinstance(data, dict):
      return data
    filled: dict[str, Any] = dict(data)
    if "trail" not in filled:
      start = {
        "layer": HEURISTIC_LAYER,
        "effect": "start",
        "level_after": filled.get("confidence"),
        "reason": filled.get("confidence_reason"),
      }
      filled["trail"] = [start]
    if "l1_probability" not in filled:
      filled["l1_probability"] = l1_probability(
        int(filled["cell_count"]),
        int(filled["row_paper_count"]),
        int(filled["column_paper_count"]),
        int(filled["corpus_paper_count"]),
      )
    return filled


class StatementEvidence(BaseModel):
  """One source statement behind an Unanswered Limitation card."""

  citation_key: str
  statement_id: str
  statement: str
  evidence: Evidence

  @property
  def evidence_id(self) -> str:
    """The id the review cites this statement by: its statement id."""
    return self.statement_id


class UnansweredLimitationGap(BaseModel):
  """
  One Unanswered Limitation card: a limitation group no later Paper addressed.

  Its trail opens with the follow-up check's level; the review (L5) may add a
  step. An artifact written before limitation trails reads with that start
  step rebuilt from its recorded confidence and reason.
  """

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
  bucket: LimitationBucket | None = Field(
    default=None, description="The group's limitation type; None if untagged."
  )
  trail: list[LayerStep] = Field(min_length=1)
  review: GapReview | None = Field(
    default=None, description="The L5 verdict; None when the review did not run."
  )

  @property
  def rejected(self) -> bool:
    """Whether the review rejected this candidate; it stays in the artifact."""
    return any(step.effect == "reject" for step in self.trail)

  @model_validator(mode="before")
  @classmethod
  def _fill_trail_of_older_artifacts(cls, data: Any) -> Any:
    """Give a pre-trail limitation card its single follow-up start step."""
    if isinstance(data, dict) and "trail" not in data:
      start = {
        "layer": FOLLOW_UP_LAYER,
        "effect": "start",
        "level_after": data.get("confidence"),
        "reason": data.get("confidence_reason"),
      }
      return {**data, "trail": [start]}
    return data


CandidateGap = Annotated[
  CellGap | UnansweredLimitationGap, Field(discriminator="gap_type")
]


class L1Margins(BaseModel):
  """
  The smallest equal category sizes at which an empty cell reaches each level.

  For a Corpus of N Papers, an empty cell between two categories of m Papers
  each ranks High only from `high` and Medium only from `medium`: what L1 can
  and cannot detect at this Corpus size. None when no size up to N reaches it.
  """

  high: int | None
  medium: int | None


class CandidateGapsReport(BaseModel):
  """
  What the detect stage produced: matrices, groups, gap cards, provenance.

  An artifact written before the candidate rule existed recorded a sparse
  count instead; it reads with that count restated as its rule and no margins.
  """

  corpus_root: Path
  corpus_paper_count: int = Field(description="Papers in the Coverage Matrices.")
  paper_citation_keys: list[str] = Field(
    default_factory=list,
    description="Every Paper in the manifest; empty in artifacts written before.",
  )
  extracted_paper_count: int = Field(description="Papers checked for limitations.")
  candidate_rule: str = Field(description="Which cells became cell candidates.")
  l1_smallest_margins: L1Margins | None = Field(
    default=None, description="None in artifacts written before L1."
  )
  normalized_prompt_version: str
  limitation_prompt_versions: list[str]
  review_prompt_version: str | None = Field(
    default=None, description="The L5 prompt; None in artifacts written before L5."
  )
  tier: ModelTier
  excluded_citation_keys: list[str] = Field(
    description="Papers in upstream artifacts but absent from the manifest."
  )
  embedding: EmbeddingProvenance | None = Field(
    default=None, description="The model behind the merge hints; None if unused."
  )
  limitation_merge_threshold: float | None = Field(
    default=None, description="Cosine at or above which a pair became a hint."
  )
  limitation_merge_hints: list[MergeHint] = Field(default_factory=list)
  limitation_tag_tier: ModelTier | None = Field(
    default=None, description="The tier groups were tagged on; None if untagged."
  )
  limitation_tagging_notes: list[str] = Field(
    default_factory=list, description="Tags dropped for naming no known group."
  )
  matrices: list[CoverageMatrix]
  limitation_groups: list[LimitationGroup]
  gaps: list[CandidateGap]

  @model_validator(mode="before")
  @classmethod
  def _rule_from_sparse_count(cls, data: Any) -> Any:
    """Restate a pre-L1 artifact's sparse count as its candidate rule."""
    if isinstance(data, dict) and "candidate_rule" not in data:
      if "sparse_max_count" in data:
        rule = _TEXT["legacy_rule"].format(count=data["sparse_max_count"])
        return {**data, "candidate_rule": rule}
    return data


def l1_probability(
  observed: int, row_count: int, column_count: int, total: int
) -> float:
  """
  Return `P(X <= observed)` for a cell, its two categories independent.

  X is hypergeometric: of `total` Papers, `row_count` are on the row category,
  and `column_count` are drawn for the column category; X counts the draws on
  the row category too.
  """
  return float(_lower_tail(observed, row_count, column_count, total))


def l1_level(probability: Fraction | float) -> Confidence:
  """Return the starting level for an L1 probability (ADR 0008 §1)."""
  if probability <= L1_HIGH_MAX:
    return "high"
  if probability <= L1_MEDIUM_MAX:
    return "medium"
  return "low"


def l1_smallest_margins(total: int) -> L1Margins:
  """Find the smallest equal category sizes at which an empty cell ranks each level."""
  high: int | None = None
  medium: int | None = None
  for margin in range(1, total + 1):
    level = l1_level(_lower_tail(0, margin, margin, total))
    if medium is None and level != "low":
      medium = margin
    if level == "high":
      high = margin
      break
  return L1Margins(high=high, medium=medium)


def is_candidate_cell(cell_count: int, expected: float) -> bool:
  """Apply the candidate net: empty, or one Paper where more than one was expected."""
  return cell_count == 0 or (cell_count == 1 and expected > 1)


def _lower_tail(
  observed: int, row_count: int, column_count: int, total: int
) -> Fraction:
  """Compute the hypergeometric lower tail exactly, as a fraction."""
  draws = math.comb(total, column_count)
  if draws == 0:
    return Fraction(1)
  ways = sum(
    math.comb(row_count, k) * math.comb(total - row_count, column_count - k)
    for k in range(0, observed + 1)
  )
  return Fraction(ways, draws)


def detect_corpus(  # pylint: disable=too-many-locals
  root: Path,
  client: LlmClient,
  *,
  tier: ModelTier = "default",
  embedder: Embedder | None = None,
  merge_threshold: float = DEFAULT_MERGE_THRESHOLD,
) -> CandidateGapsReport:
  """
  Detect every v1 in-Corpus Gap Type and write the CandidateGaps artifact.

  Reads the manifest, the Extractions, and the NormalizedFacts artifact; an
  entry for a Paper not in the manifest is excluded (and listed) so no card
  cites a Paper outside the Corpus. `embedder`, when given, supplies merge
  hints for limitation grouping at `merge_threshold` cosine.
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
  hierarchy: dict[Axis, dict[str, str]] = {
    axis: normalized.parents(axis) for axis in AXES
  }
  matrices, cell_gaps = _matrix_gaps(papers, hierarchy)

  hints = (
    merge_hints(collect_statements(extractions), embedder, merge_threshold)
    if embedder is not None
    else []
  )
  groups = group_limitations(
    extractions,
    {paper.citation_key: paper.year for paper in manifest.papers},
    client,
    tier=tier,
    hints=hints,
  )
  groups, tagging_notes = tag_limitations(
    groups, _observed_labels(papers), client, tier=TAG_TIER
  )
  gaps: list[CandidateGap] = []
  gaps.extend(_two_witnesses(gap, groups) if groups else gap for gap in cell_gaps)
  gaps.extend(
    _limitation_gap(group, len(extractions)) for group in groups if not group.addressed
  )
  gaps = _Review(matrices, client, tier).apply_all(gaps)

  report = CandidateGapsReport(
    corpus_root=root,
    corpus_paper_count=len(papers),
    paper_citation_keys=sorted(corpus_keys),
    extracted_paper_count=len(extractions),
    candidate_rule=_TEXT["candidate_rule"],
    l1_smallest_margins=l1_smallest_margins(len(papers)),
    normalized_prompt_version=normalized.prompt_version,
    limitation_prompt_versions=[
      GROUP_PROMPT_VERSION,
      FOLLOW_UP_PROMPT_VERSION,
      TAG_PROMPT_VERSION,
    ],
    review_prompt_version=REVIEW_PROMPT_VERSION,
    tier=tier,
    excluded_citation_keys=excluded,
    embedding=(
      EmbeddingProvenance(model_id=embedder.model_id, revision=embedder.revision)
      if embedder is not None
      else None
    ),
    limitation_merge_threshold=merge_threshold if embedder is not None else None,
    limitation_merge_hints=hints,
    limitation_tag_tier=TAG_TIER if groups else None,
    limitation_tagging_notes=tagging_notes,
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
  papers: list[NormalizedFacts], hierarchy: dict[Axis, dict[str, str]]
) -> tuple[list[CoverageMatrix], list[CellGap]]:
  """Build every Coverage Matrix, card each candidate cell (L1), then layer it."""
  roll_up = _RollUp(papers, hierarchy)
  bridges = _Bridges(papers, roll_up.ancestors)
  matrices: list[CoverageMatrix] = []
  gaps: list[CellGap] = []
  for row_axis, column_axis, gap_type in MATRIX_SPECS:
    matrix = build_matrix(papers, row_axis, column_axis)
    matrices.append(matrix)
    gaps.extend(
      bridges.apply(roll_up.apply(gap))
      for gap in _gaps_from_matrix(matrix, papers, gap_type)
    )
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
) -> list[CellGap]:
  """Turn every cell the candidate net catches into a gap card."""
  rows = {row.category_id: row for row in matrix.rows}
  columns = {column.category_id: column for column in matrix.columns}
  total = matrix.corpus_paper_count
  gaps: list[CellGap] = []
  for cell in matrix.cells:
    row, column = rows[cell.row_id], columns[cell.column_id]
    expected = row.paper_count * column.paper_count / total if total else 0.0
    if not is_candidate_cell(cell.paper_count, expected):
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
  probability = _lower_tail(
    cell.paper_count, row.paper_count, column.paper_count, total
  )
  confidence = l1_level(probability)
  confidence_reason = _l1_reason(row, column, cell, total, probability)
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
    l1_probability=float(probability),
    trail=[
      LayerStep(
        layer=L1_LAYER,
        effect="start",
        level_after=confidence,
        reason=confidence_reason,
      )
    ],
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


def _l1_reason(
  row: MatrixCategory,
  column: MatrixCategory,
  cell: MatrixCell,
  total: int,
  probability: Fraction,
) -> str:
  """
  Explain the L1 level in counts, never as a significance claim.

  The probability is restated as "about 1 time in n" so a reader without
  statistics training can weigh it; the level's cut points are restated the
  same way.
  """
  expected = row.paper_count * column.paper_count / total if total else 0.0
  if 0 < probability <= Fraction(1, 2):
    chance = _TEXT["chance_rare"].format(one_in=round(1 / probability))
  else:
    chance = _TEXT["chance_common"]
  return _TEXT["confidence"].format(
    row=row.label,
    column=column.label,
    total=total,
    expected=expected,
    count=cell.paper_count,
    chance=chance,
    level=_TEXT[f"level_{l1_level(probability)}"],
  )


class _RollUp:
  """
  The taxonomy roll-up layer (ADR 0008 L2) over one Corpus's hierarchy.

  The matrices count a Paper only on the categories it was placed on, so the
  hierarchy matters in two ways. A same-axis cell pairing a category with its
  own ancestor is no combination of two subjects: rejected. A Paper placed on
  an ancestor of one side (and on none of that ancestor's descendants) together
  with the other side may cover the narrower cell: lowered one level, naming
  it. A Paper that is also on a narrower category than that ancestor (a sibling
  of the cell's category, say) explains its broad placement, so it counts for
  nothing: "no prospective study, only retrospective ones" stays a gap.
  """

  def __init__(
    self, papers: list[NormalizedFacts], hierarchy: dict[Axis, dict[str, str]]
  ) -> None:
    self._papers = papers
    self._parents = hierarchy
    self._labels: dict[tuple[Axis, str], str] = {}
    for facts in papers:
      for assignment in facts.assignments:
        self._labels.setdefault(
          (assignment.axis, assignment.category_id), assignment.category_label
        )

  def apply(self, gap: CellGap) -> CellGap:
    """Return the gap with this layer's step appended, if it has anything to say."""
    step = self._step(gap)
    if step is None:
      return gap
    return gap.model_copy(
      update={"trail": [*gap.trail, step], "confidence": step.level_after}
    )

  def ancestors(self, axis: Axis, category_id: str) -> list[str]:
    """List a category's broader categories, nearest first (cycle-safe)."""
    parents = self._parents.get(axis, {})
    found: list[str] = []
    current = parents.get(category_id)
    while current is not None and current != category_id and current not in found:
      found.append(current)
      current = parents.get(current)
    return found

  def _step(self, gap: CellGap) -> LayerStep | None:
    """Judge one candidate: reject, lower, note a sibling fill, or stay silent."""
    level = gap.trail[-1].level_after
    if gap.row_axis == gap.column_axis:
      if gap.row_id in self.ancestors(gap.column_axis, gap.column_id):
        return self._reject(gap, gap.row_label, gap.column_label, level)
      if gap.column_id in self.ancestors(gap.row_axis, gap.row_id):
        return self._reject(gap, gap.column_label, gap.row_label, level)

    covering, siblings = self._broader_fills(gap)
    if covering:
      lowered = _lowered(level)
      template = "roll_up_lower" if lowered != level else "roll_up_lower_floor"
      return LayerStep(
        layer=ROLL_UP_LAYER,
        effect="lower" if lowered != level else "none",
        level_after=lowered,
        reason=_TEXT[template].format(**_named(covering)),
      )
    if siblings:
      return LayerStep(
        layer=ROLL_UP_LAYER,
        effect="none",
        level_after=level,
        reason=_TEXT["roll_up_sibling"].format(**_named(siblings)),
      )
    return None

  def _broader_fills(
    self, gap: CellGap
  ) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """
    Find the Papers in each broader cell, keyed by that cell's name.

    Returns the Papers placed only on the broader category (they may cover the
    candidate) apart from those also placed on a narrower one (they do not).
    """
    covering: dict[str, list[str]] = {}
    siblings: dict[str, list[str]] = {}
    for axis, category_id, other_axis, other_id, other_label in _sides(gap):
      for ancestor in self.ancestors(axis, category_id):
        broader = _TEXT["roll_up_broader"].format(
          label=self._labels.get((axis, ancestor), ancestor), other=other_label
        )
        for facts in self._papers:
          placed = facts.category_ids(axis)
          if ancestor not in placed or other_id not in facts.category_ids(other_axis):
            continue
          narrower = any(ancestor in self.ancestors(axis, p) for p in placed)
          target = siblings if narrower else covering
          target.setdefault(broader, []).append(facts.citation_key)
    return covering, siblings

  def _reject(
    self, gap: CellGap, broad: str, narrow: str, level: Confidence
  ) -> LayerStep:
    """Reject an ancestor x descendant cell; its level is kept for the record."""
    return LayerStep(
      layer=ROLL_UP_LAYER,
      effect="reject",
      level_after=level,
      reason=_TEXT["roll_up_reject"].format(
        broad=broad, narrow=narrow, axis=_AXIS_NAMES[gap.row_axis]
      ),
    )


def _lowered(level: Confidence) -> Confidence:
  """Return the level one step down, stopping at Low."""
  return _LEVELS[max(_LEVELS.index(level) - 1, 0)]


class _Bridges:
  """
  The bridges layer (ADR 0008) over one Corpus's category co-occurrence graph.

  Nodes are (axis, category) pairs; two are linked when a Paper is placed on
  both. A bridge for cell A x C is a node linked to A and to C by at least two
  Papers each: one broad Paper links all its categories to each other, so a
  single-Paper link would make nearly every cell bridged. A, C, their ancestors
  and descendants (the roll-up's ground) and near-universal categories never
  bridge. The layer only raises; a rejected candidate is left as it is.
  """

  def __init__(
    self,
    papers: list[NormalizedFacts],
    ancestors: Callable[[Axis, str], list[str]],
  ) -> None:
    self._ancestors = ancestors
    self._papers_on: dict[tuple[Axis, str], set[str]] = {}
    self._labels: dict[tuple[Axis, str], str] = {}
    neighbours: dict[tuple[Axis, str], set[tuple[Axis, str]]] = {}
    for facts in papers:
      nodes: set[tuple[Axis, str]] = {
        (axis, c) for axis in AXES for c in facts.category_ids(axis)
      }
      for node in nodes:
        self._papers_on.setdefault(node, set()).add(facts.citation_key)
        neighbours.setdefault(node, set()).update(nodes - {node})
      for assignment in facts.assignments:
        self._labels.setdefault(
          (assignment.axis, assignment.category_id), assignment.category_label
        )
    self._degree = {node: len(linked) for node, linked in neighbours.items()}
    total = len(papers)
    self._near_universal = {
      node
      for node, keys in self._papers_on.items()
      if total and Fraction(len(keys), total) >= NEAR_UNIVERSAL_SHARE
    }

  def apply(self, gap: CellGap) -> CellGap:
    """Return the gap with its bridges stored and this layer's step appended."""
    if gap.rejected:
      return gap
    found = self.bridges(gap)
    if not found:
      return gap
    step = _bridges_step(gap.trail[-1].level_after, found)
    return gap.model_copy(
      update={
        "bridges": found,
        "trail": [*gap.trail, step],
        "confidence": step.level_after,
      }
    )

  def bridges(self, gap: CellGap) -> list[Bridge]:
    """List the cell's bridges, strongest first (ties by label, then id)."""
    row = self._papers_on.get((gap.row_axis, gap.row_id), set())
    column = self._papers_on.get((gap.column_axis, gap.column_id), set())
    found: list[Bridge] = []
    for node, keys in self._papers_on.items():
      if node in self._near_universal or self._is_kin(gap, node):
        continue
      row_links, column_links = len(row & keys), len(column & keys)
      if min(row_links, column_links) < BRIDGE_MIN_LINK_PAPERS:
        continue
      degree = self._degree[node]
      found.append(
        Bridge(
          axis=node[0],
          category_id=node[1],
          label=self._labels.get(node, node[1]),
          row_link_count=row_links,
          column_link_count=column_links,
          degree=degree,
          # A bridge links two other nodes, so its degree is at least 2.
          weight=1 / math.log(degree),
        )
      )
    return sorted(found, key=lambda b: (-b.weight, b.label, b.axis, b.category_id))

  def _is_kin(self, gap: CellGap, node: tuple[Axis, str]) -> bool:
    """Whether a node is one of the cell's categories, or above or below one."""
    axis, category_id = node
    for side_axis, side_id in (
      (gap.row_axis, gap.row_id),
      (gap.column_axis, gap.column_id),
    ):
      if axis != side_axis:
        continue
      if category_id == side_id:
        return True
      if category_id in self._ancestors(axis, side_id):
        return True
      if side_id in self._ancestors(axis, category_id):
        return True
    return False


def _bridges_step(level: Confidence, found: list[Bridge]) -> LayerStep:
  """Raise one level for enough bridges; otherwise note the ones found."""
  named = "; ".join(
    _TEXT["bridge"].format(
      label=bridge.label,
      axis=_AXIS_NAMES[bridge.axis],
      row_count=bridge.row_link_count,
      column_count=bridge.column_link_count,
    )
    for bridge in found
  )
  if len(found) < BRIDGE_MIN_COUNT:
    return LayerStep(
      layer=BRIDGES_LAYER,
      effect="none",
      level_after=level,
      reason=_TEXT["bridges_one"].format(
        count=len(found),
        bridges=named,
        min_links=BRIDGE_MIN_LINK_PAPERS,
        min_count=BRIDGE_MIN_COUNT,
      ),
    )
  raised = _LEVELS[min(_LEVELS.index(level) + 1, len(_LEVELS) - 1)]
  template = "bridges_raise" if raised != level else "bridges_ceiling"
  return LayerStep(
    layer=BRIDGES_LAYER,
    effect="raise" if raised != level else "none",
    level_after=raised,
    reason=_TEXT[template].format(
      count=len(found), bridges=named, min_links=BRIDGE_MIN_LINK_PAPERS
    ),
  )


def _observed_labels(papers: list[NormalizedFacts]) -> dict[tuple[Axis, str], str]:
  """Map every category any Paper was placed on, per axis, to its label."""
  return {
    (axis, category.category_id): category.label
    for axis in AXES
    for category in _observed_categories(papers, axis)
  }


def _two_witnesses(gap: CellGap, groups: list[LimitationGroup]) -> CellGap:
  """
  Apply the two-witness layer (ADR 0008): corroborate, never penalise.

  A group witnesses the cell when it names both categories, or one of them
  plus a limitation type routed to the other category's axis. A rejected
  candidate is left as it is: no boost can undo a rejection.
  """
  if gap.rejected:
    return gap
  level = gap.trail[-1].level_after
  sides: tuple[tuple[Axis, str], tuple[Axis, str]] = (
    (gap.row_axis, gap.row_id),
    (gap.column_axis, gap.column_id),
  )
  witnesses: list[LimitationGroup] = []
  related: list[LimitationGroup] = []
  for group in groups:
    kind = _witness_kind(group, sides)
    if kind == "witness":
      witnesses.append(group)
    elif kind == "related":
      related.append(group)
  if witnesses:
    raised = _LEVELS[min(_LEVELS.index(level) + 1, len(_LEVELS) - 1)]
    step = LayerStep(
      layer=TWO_WITNESS_LAYER,
      effect="raise" if raised != level else "none",
      level_after=raised,
      reason=_TEXT["witness_raise" if raised != level else "witness_ceiling"].format(
        groups=_group_names(witnesses)
      ),
    )
  elif related:
    step = LayerStep(
      layer=TWO_WITNESS_LAYER,
      effect="none",
      level_after=level,
      reason=_TEXT["witness_related"].format(groups=_group_names(related)),
    )
  else:
    step = LayerStep(
      layer=TWO_WITNESS_LAYER,
      effect="none",
      level_after=level,
      reason=_TEXT["witness_none"],
    )
  return gap.model_copy(
    update={
      "trail": [*gap.trail, step],
      "confidence": step.level_after,
      "corroborating_group_ids": [group.group_id for group in witnesses],
    }
  )


def _witness_kind(
  group: LimitationGroup, sides: tuple[tuple[Axis, str], tuple[Axis, str]]
) -> Literal["witness", "related"] | None:
  """Say whether a group witnesses a cell, concerns one side only, or neither."""
  named = {(c.axis, c.category_id) for c in group.categories}
  routed = BUCKET_AXES[group.bucket] if group.bucket is not None else None
  (first, second) = sides
  if first in named and second in named:
    return "witness"
  if first in named or second in named:
    other = second if first in named else first
    return "witness" if routed == other[0] else "related"
  return None


def _group_names(groups: list[LimitationGroup]) -> str:
  """Name the limitation groups a two-witness reason cites."""
  return "; ".join(
    _TEXT["witness_group"].format(group_id=group.group_id, label=group.label)
    for group in groups
  )


def _sides(gap: CellGap) -> tuple[tuple[Axis, str, Axis, str, str], ...]:
  """Each side of a cell as (axis, id, other axis, other id, other label)."""
  return (
    (gap.row_axis, gap.row_id, gap.column_axis, gap.column_id, gap.column_label),
    (gap.column_axis, gap.column_id, gap.row_axis, gap.row_id, gap.row_label),
  )


def _named(found: dict[str, list[str]]) -> dict[str, str]:
  """Format the Papers and broader cells a roll-up reason names."""
  keys = sorted({key for keys in found.values() for key in keys})
  return {"papers": ", ".join(keys), "broader": "; ".join(sorted(found))}


class _Review:  # pylint: disable=too-few-public-methods
  """
  The LLM review layer (ADR 0008 L5), run last on the candidates worth paying for.

  It reviews every cell candidate still at Medium or above and not already
  rejected, and every Unanswered Limitation. A keep leaves the level, a
  `least_probable` keep lowers it one level, a reject rejects it; the verdict
  and its Audit Basis and Minimal Study are stored on the candidate. A review
  that returns no usable answer is recorded as such and changes nothing.
  """

  def __init__(
    self, matrices: list[CoverageMatrix], client: LlmClient, tier: ModelTier
  ) -> None:
    self._matrices = {(m.row_axis, m.column_axis): m for m in matrices}
    self._client = client
    self._tier: ModelTier = tier

  def apply_all(self, gaps: list[CandidateGap]) -> list[CandidateGap]:
    """Return the gaps with every due review applied, in the same order."""
    reviewed = sum(1 for gap in gaps if self._due(gap))
    if reviewed:
      logger.info("Reviewing %d candidate gaps (%s).", reviewed, REVIEW_PROMPT_VERSION)
    return [self._apply(gap) if self._due(gap) else gap for gap in gaps]

  @staticmethod
  def _due(gap: CandidateGap) -> bool:
    """Whether the review runs: Medium+ live cells, and every limitation card."""
    if isinstance(gap, UnansweredLimitationGap):
      return not gap.rejected
    return not gap.rejected and _LEVELS.index(gap.confidence) >= _LEVELS.index(
      REVIEW_MIN_LEVEL
    )

  def _apply(self, gap: CandidateGap) -> CandidateGap:
    """Review one candidate and append the verdict's step."""
    review = review_gap(self._request(gap), self._client, tier=self._tier)
    step = _review_step(review, gap.trail[-1].level_after)
    return gap.model_copy(
      update={
        "trail": [*gap.trail, step],
        "confidence": step.level_after,
        "review": review,
      }
    )

  def _request(self, gap: CandidateGap) -> ReviewRequest:
    """Describe one candidate to the reviewer."""
    if isinstance(gap, UnansweredLimitationGap):
      evidence = [
        ReviewEvidence(
          evidence_id=item.evidence_id,
          citation_key=item.citation_key,
          text=f'{item.statement} Passage: "{item.evidence.passage}"',
        )
        for item in gap.evidence
      ]
      surroundings = [self._later_papers(gap)]
    else:
      evidence = _cell_review_evidence(gap)
      surroundings = self._neighbours(gap)
    return ReviewRequest(
      gap_id=gap.gap_id,
      gap_type_name=_GAP_TYPE_NAMES[gap.gap_type],
      title=gap.title,
      explanation=gap.explanation,
      reasons=[step.reason for step in gap.trail],
      evidence=evidence,
      surroundings=surroundings,
    )

  @staticmethod
  def _later_papers(gap: UnansweredLimitationGap) -> str:
    """Say how many later Papers were checked, since none addressed the gap."""
    if gap.later_paper_count == 0:
      return _TEXT["review_no_later"]
    return _TEXT["review_later"].format(count=gap.later_paper_count)

  def _neighbours(self, gap: CellGap) -> list[str]:
    """List the fullest cells sharing a category with the gap's cell, fullest first."""
    matrix = self._matrices.get((gap.row_axis, gap.column_axis))
    if matrix is None:
      return []
    labels = {c.category_id: c.label for c in [*matrix.rows, *matrix.columns]}
    sides = {gap.row_id, gap.column_id}
    cells = [
      cell
      for cell in matrix.cells
      if (cell.row_id, cell.column_id) != (gap.row_id, gap.column_id)
      and ({cell.row_id, cell.column_id} & sides)
      and cell.paper_count > 0
    ]
    cells.sort(key=lambda cell: (-cell.paper_count, cell.row_id, cell.column_id))
    return [
      _TEXT["review_neighbour"].format(
        row=labels.get(cell.row_id, cell.row_id),
        column=labels.get(cell.column_id, cell.column_id),
        count=cell.paper_count,
      )
      for cell in cells[:REVIEW_NEIGHBOURS]
    ]


def _cell_review_evidence(gap: CellGap) -> list[ReviewEvidence]:
  """Pick a cell gap's Evidence for the reviewer: a few per role, each id once."""
  labels = {gap.row_id: gap.row_label, gap.column_id: gap.column_label}
  per_role: dict[EvidenceRole, int] = {}
  seen: set[str] = set()
  items: list[ReviewEvidence] = []
  for link in gap.evidence:
    if (
      link.evidence_id in seen or per_role.get(link.role, 0) >= REVIEW_EVIDENCE_PER_ROLE
    ):
      continue
    seen.add(link.evidence_id)
    per_role[link.role] = per_role.get(link.role, 0) + 1
    items.append(
      ReviewEvidence(
        evidence_id=link.evidence_id,
        citation_key=link.citation_key,
        text=_TEXT["review_evidence_cell"].format(
          term=link.original_term,
          category=labels.get(link.category_id, link.category_id),
          passage=f'"{link.evidence.passage}"',
        ),
      )
    )
  return items


def _review_step(review: GapReview | None, level: Confidence) -> LayerStep:
  """Turn a review verdict into its trail step; nothing here ever raises a level."""
  if review is None:
    return LayerStep(
      layer=REVIEW_LAYER,
      effect="none",
      level_after=level,
      reason=_TEXT["review_failed"],
    )
  if review.verdict == "reject":
    if review.rejection_class is None:
      reason = _TEXT["review_reject_unnamed"].format(warrant=review.warrant)
    else:
      reason = _TEXT["review_reject"].format(
        rejection_class=REJECTION_CLASS_NAMES[review.rejection_class],
        warrant=review.warrant,
      )
    return LayerStep(
      layer=REVIEW_LAYER, effect="reject", level_after=level, reason=reason
    )
  if review.bucket == "least_probable":
    lowered = _lowered(level)
    template = "review_lower" if lowered != level else "review_lower_floor"
    return LayerStep(
      layer=REVIEW_LAYER,
      effect="lower" if lowered != level else "none",
      level_after=lowered,
      reason=_TEXT[template].format(warrant=review.warrant),
    )
  return LayerStep(
    layer=REVIEW_LAYER,
    effect="none",
    level_after=level,
    reason=_TEXT["review_keep"].format(warrant=review.warrant),
  )


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
    trail=[
      LayerStep(
        layer=FOLLOW_UP_LAYER, effect="start", level_after=confidence, reason=reason
      )
    ],
    bucket=group.bucket,
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
  if path.is_file():
    # copy2 keeps the replaced artifact's mtime: when it was written, which is
    # what dates the verdicts made while it was on screen.
    shutil.copy2(path, artifacts_dir / PREVIOUS_CANDIDATE_GAPS_NAME)
  path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
