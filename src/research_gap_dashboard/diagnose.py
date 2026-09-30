"""
The extraction funnel diagnostic: find every failure point before fixing it.

Issue #46 Step 1 asks to *diagnose first, not fix*: a run over the real corpora
that reports a per-stage funnel (PDFs -> parsed -> sectioned -> extracted) and
surfaces where papers fall out. It reads only the artifacts previous stages
already wrote (the manifest, the `paper-data/` parsed files, and the extractions
artifact); it never calls an LLM or re-parses a PDF, so it is cheap to run
repeatedly while hunting the ~10% of papers that never reach the artifact with
real facts.

The three failure modes the issue calls out map onto per-Paper flags:
- a PDF with no `.parsed.json` (`parsed` is False),
- empty/near-empty parsed text (`near_empty`),
- a paper collapsed into a single unlabeled section (`collapsed`).
"""

import logging
from pathlib import Path

from pydantic import BaseModel

from research_gap_dashboard.extract import ExtractionFields, read_extractions
from research_gap_dashboard.ingest import read_manifest
from research_gap_dashboard.parsing import (
  ParsedPaper,
  SectionLabel,
  read_parsed_paper,
)

logger = logging.getLogger(__name__)

# Parsed text shorter than this (after stripping) is treated as empty: a scanned
# or image-only PDF with no OCR layer typically yields only a stray title line.
NEAR_EMPTY_CHARS = 200

# Section labels that carry no real body content on their own. A paper whose
# every section is one of these was never really sectioned (docling produced no
# recognizable paper structure), so it is "collapsed".
_STRUCTURELESS_LABELS: frozenset[SectionLabel] = frozenset({"front_matter", "other"})


class _ExtractionOutcome(BaseModel):
  """What the extract stage recorded for one Paper (absent when it never ran)."""

  fact_count: int = 0
  dropped_fact_count: int = 0
  rejection: str | None = None


class PaperFunnel(BaseModel):
  """How one Paper fared at each funnel stage, and why it fell out."""

  citation_key: str
  parsed: bool
  char_count: int
  section_count: int
  labeled_section_count: int
  near_empty: bool
  collapsed: bool
  extracted: bool
  fact_count: int
  dropped_fact_count: int
  issues: list[str]

  @property
  def sectioned(self) -> bool:
    """True when the Paper parsed into real, labeled body text."""
    return self.parsed and not self.near_empty and not self.collapsed


class FunnelReport(BaseModel):
  """The per-stage funnel over a Corpus: PDFs -> parsed -> sectioned -> extracted."""

  corpus_root: Path
  extractions_present: bool
  papers: list[PaperFunnel]

  @property
  def total_papers(self) -> int:
    """Number of Papers (PDFs) the manifest lists: the top of the funnel."""
    return len(self.papers)

  @property
  def parsed_count(self) -> int:
    """Papers that have a non-missing `.parsed.json`."""
    return sum(1 for p in self.papers if p.parsed)

  @property
  def sectioned_count(self) -> int:
    """Papers that parsed into real, labeled body text."""
    return sum(1 for p in self.papers if p.sectioned)

  @property
  def extracted_count(self) -> int:
    """Papers that reached the extractions artifact with at least one fact."""
    return sum(1 for p in self.papers if p.extracted)

  @property
  def end_to_end_rate(self) -> float:
    """Fraction of PDFs that reached the artifact with real facts (0.0 when empty)."""
    return self.extracted_count / self.total_papers if self.papers else 0.0

  @property
  def fallout(self) -> list[PaperFunnel]:
    """Every Paper that fell out of the funnel before the artifact."""
    return [p for p in self.papers if p.issues]


def render_funnel(report: FunnelReport) -> str:
  """Render the funnel as a human-readable summary for the CLI."""
  total = report.total_papers
  lines = [
    f"Extraction funnel for {report.corpus_root}:",
    f"  PDFs (manifest): {total}",
    f"  parsed:          {report.parsed_count}/{total}",
    f"  sectioned:       {report.sectioned_count}/{total}",
  ]
  if report.extractions_present:
    lines.append(f"  extracted:       {report.extracted_count}/{total}")
    lines.append(f"  end-to-end:      {report.end_to_end_rate:.0%}")
  else:
    lines.append("  extracted:       (extract stage has not run)")

  missing = [p for p in report.papers if not p.extracted and p.issues]
  if missing:
    lines.append(f"\n{len(missing)} Paper(s) did not reach the artifact:")
    for paper in missing:
      for issue in paper.issues:
        lines.append(f"  - {paper.citation_key}: {issue}")

  dropped = [p for p in report.papers if p.extracted and p.dropped_fact_count]
  if dropped:
    total_dropped = sum(p.dropped_fact_count for p in dropped)
    lines.append(
      f"\n{total_dropped} fact(s) dropped for unverifiable Evidence "
      f"across {len(dropped)} extracted Paper(s):"
    )
    for paper in dropped:
      lines.append(f"  - {paper.citation_key}: {paper.dropped_fact_count} dropped")

  if not missing and not dropped:
    lines.append("\nEvery Paper reached the artifact with fully verified facts.")
  return "\n".join(lines)


def diagnose_corpus(root: Path) -> FunnelReport:
  """Build the parse/extract funnel for a Corpus from its on-disk artifacts."""
  manifest = read_manifest(root)
  extraction = _read_extraction_outcomes(root)
  extractions_present = extraction is not None
  outcomes = extraction or {}

  papers: list[PaperFunnel] = []
  for paper in manifest.papers:
    parsed = _read_parsed(root, paper.citation_key)
    papers.append(
      _diagnose_paper(
        paper.citation_key,
        parsed,
        outcomes.get(paper.citation_key, _ExtractionOutcome()),
        extractions_present=extractions_present,
      )
    )

  return FunnelReport(
    corpus_root=root, extractions_present=extractions_present, papers=papers
  )


def _diagnose_paper(
  citation_key: str,
  parsed: ParsedPaper | None,
  outcome: _ExtractionOutcome,
  *,
  extractions_present: bool,
) -> PaperFunnel:
  """Classify one Paper against every funnel stage."""
  issues: list[str] = []
  if parsed is None:
    issues.append("no parsed text (.parsed.json missing); the parser dropped this PDF")
    return PaperFunnel(
      citation_key=citation_key,
      parsed=False,
      char_count=0,
      section_count=0,
      labeled_section_count=0,
      near_empty=True,
      collapsed=True,
      extracted=False,
      fact_count=0,
      dropped_fact_count=0,
      issues=issues,
    )

  char_count = len(parsed.full_text.strip())
  labeled = sum(1 for s in parsed.sections if s.label not in _STRUCTURELESS_LABELS)
  near_empty = char_count < NEAR_EMPTY_CHARS
  collapsed = not near_empty and labeled == 0

  if near_empty:
    issues.append(
      f"near-empty parsed text ({char_count} chars); likely a scanned/image-only PDF"
    )
  if collapsed:
    issues.append(
      "collapsed into unlabeled sections; no paper structure was recognized"
    )

  extracted = outcome.fact_count > 0
  if extractions_present and not extracted and not near_empty and not collapsed:
    if outcome.rejection is not None:
      issues.append(f"extraction rejected: {outcome.rejection}")
    else:
      issues.append(
        "parsed but no facts extracted; the extraction stage dropped this Paper"
      )
  if outcome.dropped_fact_count:
    issues.append(
      f"{outcome.dropped_fact_count} fact(s) dropped for unverifiable Evidence"
    )

  return PaperFunnel(
    citation_key=citation_key,
    parsed=True,
    char_count=char_count,
    section_count=len(parsed.sections),
    labeled_section_count=labeled,
    near_empty=near_empty,
    collapsed=collapsed,
    extracted=extracted,
    fact_count=outcome.fact_count,
    dropped_fact_count=outcome.dropped_fact_count,
    issues=issues,
  )


def _read_parsed(root: Path, citation_key: str) -> ParsedPaper | None:
  """Read a Paper's parsed text, or None when the parse stage never wrote it."""
  try:
    return read_parsed_paper(root, citation_key)
  except FileNotFoundError:
    return None


def _read_extraction_outcomes(root: Path) -> dict[str, _ExtractionOutcome] | None:
  """Read each Paper's extract-stage outcome, or None when the stage never ran."""
  try:
    report = read_extractions(root)
  except FileNotFoundError:
    return None
  outcomes: dict[str, _ExtractionOutcome] = {
    extraction.citation_key: _ExtractionOutcome(
      fact_count=_total_facts(extraction.fields)
    )
    for extraction in report.extractions
  }
  for failure in report.failures:
    outcomes[failure.citation_key] = _ExtractionOutcome(rejection=failure.reason)
  for fact in report.unverified:
    outcome = outcomes.setdefault(fact.citation_key, _ExtractionOutcome())
    outcome.dropped_fact_count += 1
  return outcomes


def _total_facts(fields: ExtractionFields) -> int:
  """Count every fact across an Extraction's seven fields."""
  return sum(len(getattr(fields, name)) for name in type(fields).model_fields)
