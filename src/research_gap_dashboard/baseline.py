"""
The corpus baseline: one report of what every pipeline stage kept and lost.

Issue #126 records a baseline per test Corpus so every detection change is
judged against the same data (spec #123). It reads only the artifacts the
stages already wrote (manifest, `paper-data/`, Extractions, NormalizedFacts,
CandidateGaps) and never calls an LLM, so it is free to rerun. A stage that has
not run leaves its section empty rather than failing the report.

Papers fall out at three points, each with the reason its stage recorded:
- **parse**: no usable parsed text (from the `diagnose` funnel),
- **extract**: the Extraction was rejected,
- **aggregate**: normalization misfired on an axis.
"""

from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from research_gap_dashboard.aggregate import AggregateReport, read_normalized_facts
from research_gap_dashboard.detect import (
  CandidateGapsReport,
  Confidence,
  GapType,
  read_candidate_gaps,
)
from research_gap_dashboard.diagnose import PaperFunnel, diagnose_corpus
from research_gap_dashboard.extract import ExtractReport, read_extractions
from research_gap_dashboard.taxonomy import AXES, Axis

Stage = Literal["parse", "extract", "aggregate"]

_GAP_TYPES: tuple[GapType, ...] = (
  "knowledge_gap",
  "coverage_gap",
  "unanswered_limitation",
)


class LostPaper(BaseModel):
  """A Paper that did not reach the Coverage Matrices, and where and why."""

  citation_key: str
  stage: Stage
  reason: str


class AxisBaseline(BaseModel):
  """One axis's normalization outcome over the kept Papers."""

  axis: Axis
  mapped: int
  papers_mapped: int
  unmapped: int
  not_study_design: int


class GapTypeCount(BaseModel):
  """How many Candidate Gaps of one Gap Type were found at each confidence."""

  gap_type: GapType
  low: int
  medium: int
  high: int

  @property
  def total(self) -> int:
    """Every candidate of this Gap Type."""
    return self.low + self.medium + self.high


class FactBaseline(BaseModel):
  """What the extract stage kept: verified facts, and the ones it dropped."""

  extracted_paper_count: int
  verified: int
  unverified: int
  unverified_by_field: dict[str, int]
  study_design_paper_count: int


class DetectBaseline(BaseModel):
  """What the detect stage found: candidates by type and confidence, and groups."""

  gaps: list[GapTypeCount]
  limitation_group_count: int
  addressed_group_count: int


class CorpusBaseline(BaseModel):
  """Per-stage counts over one Corpus; `None` marks a stage that has not run."""

  corpus_root: Path
  total_papers: int
  kept_citation_keys: list[str]
  lost: list[LostPaper]
  parse_warnings: dict[str, str]
  prompt_versions: dict[str, str]
  facts: FactBaseline | None
  axes: list[AxisBaseline] | None
  detect: DetectBaseline | None


def baseline_corpus(root: Path) -> CorpusBaseline:
  """Build the baseline for a Corpus from the artifacts on disk."""
  funnel = diagnose_corpus(root)
  extract = _read_or_none(read_extractions, root)
  aggregate = _read_or_none(read_normalized_facts, root)
  detect = _read_or_none(read_candidate_gaps, root)

  lost = sorted(
    (
      loss
      for paper in funnel.papers
      if (loss := _loss(paper, extract, aggregate)) is not None
    ),
    key=lambda loss: loss.citation_key,
  )
  lost_keys = {loss.citation_key for loss in lost}
  kept = sorted(
    p.citation_key for p in funnel.papers if p.citation_key not in lost_keys
  )

  return CorpusBaseline(
    corpus_root=root,
    total_papers=funnel.total_papers,
    kept_citation_keys=kept,
    lost=lost,
    parse_warnings={
      p.citation_key: "; ".join(_parse_issues(p))
      for p in sorted(funnel.papers, key=lambda p: p.citation_key)
      if p.citation_key in kept and (p.near_empty or p.collapsed)
    },
    prompt_versions=_prompt_versions(extract, aggregate),
    facts=_facts(extract),
    axes=_axes(aggregate),
    detect=_detect(detect),
  )


def _loss(
  paper: PaperFunnel, extract: ExtractReport | None, aggregate: AggregateReport | None
) -> LostPaper | None:
  """
  Say where a Paper fell out of the latest stage that ran, or None if it got through.

  A Paper whose text parsed without structure is still extracted, so it is lost
  only where a stage really dropped it; its parse issues ride along as context.
  """
  if not paper.parsed:
    return LostPaper(
      citation_key=paper.citation_key, stage="parse", reason=paper.issues[0]
    )
  if extract is None:
    return None
  return _extract_loss(paper, extract) or (
    _aggregate_loss(paper.citation_key, aggregate) if aggregate else None
  )


def _extract_loss(paper: PaperFunnel, extract: ExtractReport) -> LostPaper | None:
  """Say why the extract stage dropped a parsed Paper, or None if it kept it."""
  key = paper.citation_key
  if key in {e.citation_key for e in extract.extractions}:
    return None
  reason = next(
    (f.reason for f in extract.failures if f.citation_key == key),
    "not in the Extractions artifact; rerun `extract`",
  )
  context = "; ".join(_parse_issues(paper))
  return LostPaper(
    citation_key=key,
    stage="extract",
    reason=f"{reason} (parse: {context})" if context else reason,
  )


def _aggregate_loss(key: str, aggregate: AggregateReport) -> LostPaper | None:
  """Say why the aggregate stage dropped an extracted Paper, or None if it kept it."""
  if key in {n.citation_key for n in aggregate.normalized}:
    return None
  failure = next((f for f in aggregate.failures if f.citation_key == key), None)
  return LostPaper(
    citation_key=key,
    stage="aggregate",
    reason=(
      f"{failure.axis} axis: {failure.reason}"
      if failure
      else "not in the NormalizedFacts artifact; rerun `aggregate`"
    ),
  )


def _parse_issues(paper: PaperFunnel) -> list[str]:
  """Return the funnel's parse-quality flags for a Paper, in words."""
  issues: list[str] = []
  if paper.near_empty:
    issues.append(f"near-empty parsed text ({paper.char_count} chars)")
  if paper.collapsed:
    issues.append("collapsed into unlabeled sections")
  return issues


def _prompt_versions(
  extract: ExtractReport | None, aggregate: AggregateReport | None
) -> dict[str, str]:
  versions: dict[str, str] = {}
  if extract is not None:
    versions["extract"] = extract.prompt_version
  if aggregate is not None:
    versions["aggregate"] = aggregate.prompt_version
  return versions


def _facts(extract: ExtractReport | None) -> FactBaseline | None:
  if extract is None:
    return None
  fields = [extraction.fields for extraction in extract.extractions]
  return FactBaseline(
    extracted_paper_count=len(fields),
    verified=sum(
      len(getattr(f, name)) for f in fields for name in type(f).model_fields
    ),
    unverified=len(extract.unverified),
    unverified_by_field=dict(
      sorted(Counter(fact.field for fact in extract.unverified).items())
    ),
    study_design_paper_count=sum(1 for f in fields if f.study_design),
  )


def _axes(aggregate: AggregateReport | None) -> list[AxisBaseline] | None:
  if aggregate is None:
    return None
  papers = aggregate.normalized
  return [
    AxisBaseline(
      axis=axis,
      mapped=sum(1 for p in papers for a in p.assignments if a.axis == axis),
      papers_mapped=sum(1 for p in papers if p.category_ids(axis)),
      unmapped=sum(1 for p in papers for u in p.unmapped if u.axis == axis),
      not_study_design=(
        sum(len(p.not_study_design) for p in papers) if axis == "method" else 0
      ),
    )
    for axis in AXES
  ]


def _detect(detect: CandidateGapsReport | None) -> DetectBaseline | None:
  if detect is None:
    return None
  counts: Counter[tuple[GapType, Confidence]] = Counter(
    (gap.gap_type, gap.confidence) for gap in detect.gaps
  )
  return DetectBaseline(
    gaps=[
      GapTypeCount(
        gap_type=gap_type,
        low=counts[(gap_type, "low")],
        medium=counts[(gap_type, "medium")],
        high=counts[(gap_type, "high")],
      )
      for gap_type in _GAP_TYPES
    ],
    limitation_group_count=len(detect.limitation_groups),
    addressed_group_count=sum(1 for g in detect.limitation_groups if g.addressed),
  )


def _read_or_none[T](reader: Callable[[Path], T], root: Path) -> T | None:
  """Read a stage's artifact, or None when that stage has not run."""
  try:
    return reader(root)
  except FileNotFoundError:
    return None


def render_baseline(baseline: CorpusBaseline) -> str:
  """Render the baseline as Markdown, ready to post on an issue."""
  lines = [
    f"### Baseline: `{baseline.corpus_root.name}`",
    "",
    f"Papers kept: {len(baseline.kept_citation_keys)} of {baseline.total_papers}"
    " (reached the Coverage Matrices).",
  ]
  if baseline.prompt_versions:
    versions = ", ".join(f"{k} `{v}`" for k, v in baseline.prompt_versions.items())
    lines.append(f"Prompt versions: {versions}.")
  lines.extend(_render_lost(baseline))
  lines.extend(_render_parse_warnings(baseline))
  lines.extend(_render_facts(baseline))
  lines.extend(_render_axes(baseline))
  lines.extend(_render_gaps(baseline))
  return "\n".join(lines)


def _render_lost(baseline: CorpusBaseline) -> list[str]:
  if not baseline.lost:
    return ["", "No Paper was lost."]
  lines = ["", "| Lost Paper | Stage | Reason |", "|---|---|---|"]
  lines.extend(
    f"| {p.citation_key} | {p.stage} | {p.reason.replace('|', '/')} |"
    for p in baseline.lost
  )
  return lines


def _render_parse_warnings(baseline: CorpusBaseline) -> list[str]:
  if not baseline.parse_warnings:
    return []
  lines = ["", "| Kept despite a parse issue | Issue |", "|---|---|"]
  lines.extend(f"| {key} | {issue} |" for key, issue in baseline.parse_warnings.items())
  return lines


def _render_facts(baseline: CorpusBaseline) -> list[str]:
  facts = baseline.facts
  if facts is None:
    return ["", "Facts: extract has not run."]
  by_field = ", ".join(
    f"{field} {count}" for field, count in facts.unverified_by_field.items()
  )
  return [
    "",
    f"Facts over {facts.extracted_paper_count} extracted Papers: "
    f"{facts.verified} verified, {facts.unverified} unverified"
    + (f" ({by_field})" if by_field else "")
    + ".",
    f"Papers with a verified study-design fact: {facts.study_design_paper_count}"
    f" of {facts.extracted_paper_count}.",
  ]


def _render_axes(baseline: CorpusBaseline) -> list[str]:
  if baseline.axes is None:
    return ["", "Terms per axis: aggregate has not run."]
  lines = [
    "",
    f"Terms per axis over {len(baseline.kept_citation_keys)} kept Papers:",
    "",
    "| Axis | Mapped | Papers mapped | Unmapped | Not a study design |",
    "|---|---|---|---|---|",
  ]
  lines.extend(
    f"| {a.axis} | {a.mapped} | {a.papers_mapped} | {a.unmapped} "
    f"| {a.not_study_design} |"
    for a in baseline.axes
  )
  return lines


def _render_gaps(baseline: CorpusBaseline) -> list[str]:
  detect = baseline.detect
  if detect is None:
    return ["", "Candidate Gaps: detect has not run."]
  lines = [
    "",
    "| Gap Type | Low | Medium | High | Total |",
    "|---|---|---|---|---|",
  ]
  lines.extend(
    f"| {g.gap_type} | {g.low} | {g.medium} | {g.high} | {g.total} |"
    for g in detect.gaps
  )
  lines.extend(
    [
      "",
      f"Limitation groups: {detect.limitation_group_count} "
      f"({detect.addressed_group_count} addressed by a later Paper).",
    ]
  )
  return lines
