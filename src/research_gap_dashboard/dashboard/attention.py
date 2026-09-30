"""
The "needs manual attention" read model: which Papers did not make it through.

The pipeline never hard-blocks on one bad Paper (issue #46 Step 3); instead it
records the fallout — a PDF no parser could read, an extraction rejected
outright, or individual facts dropped for unverifiable Evidence (ADR 0004). This
module reads those records back off disk and turns them into a queue the
dashboard shows, plus a banner that states how incomplete the Corpus is so a
researcher never mistakes a partial Corpus for the whole one.

Like every dashboard read model it imports no pipeline stage (ADR 0002): it
reads the manifest, the parse-stage summary, and the Extractions artifact
through `dashboard.artifacts`, and the raw parsed text through
`dashboard.grounding`.
"""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from research_gap_dashboard.dashboard.artifacts import (
  has_extractions,
  has_manifest,
  has_parse_report,
  load_extractions,
  load_manifest,
  load_parse_report,
)
from research_gap_dashboard.dashboard.grounding import has_parsed_paper

AttentionKind = Literal["unparsed", "extraction_failed", "facts_dropped"]


class AttentionItem(BaseModel):
  """One Paper that needs a look, why, and whether its raw text is available."""

  citation_key: str
  title: str
  kind: AttentionKind
  reason: str
  dropped_fact_count: int = 0
  has_parsed_text: bool = False


class IncompletenessBanner(BaseModel):
  """How complete the Corpus is: the denominator research integrity demands."""

  total_papers: int
  unparsed: int
  extraction_failed: int
  papers_with_dropped_facts: int
  dropped_facts: int

  @property
  def missing_papers(self) -> int:
    """Papers that produced no facts at all (never parsed, or wholly rejected)."""
    return self.unparsed + self.extraction_failed

  @property
  def reached_artifact(self) -> int:
    """Papers present in the artifact with at least one verified fact."""
    return self.total_papers - self.missing_papers

  @property
  def is_complete(self) -> bool:
    """True when every Paper reached the artifact with fully verified facts."""
    return (
      self.missing_papers == 0
      and self.papers_with_dropped_facts == 0
      and self.total_papers > 0
    )


class AttentionReport(BaseModel):
  """The incompleteness banner plus the per-Paper queue that explains it."""

  banner: IncompletenessBanner
  items: list[AttentionItem]


def build_attention(corpus_root: Path) -> AttentionReport:
  """Build the needs-attention queue and incompleteness banner for a Corpus."""
  titles = _paper_titles(corpus_root)
  parse_failures = _parse_failures(corpus_root)
  extraction_failures, dropped = _extraction_outcomes(corpus_root)

  items: list[AttentionItem] = []
  for citation_key, title in titles.items():
    parsed = has_parsed_paper(corpus_root, citation_key)
    if not parsed:
      items.append(
        AttentionItem(
          citation_key=citation_key,
          title=title,
          kind="unparsed",
          reason=parse_failures.get(
            citation_key, "no parsed text on disk; run `parse`"
          ),
          has_parsed_text=False,
        )
      )
    elif citation_key in extraction_failures:
      items.append(
        AttentionItem(
          citation_key=citation_key,
          title=title,
          kind="extraction_failed",
          reason=extraction_failures[citation_key],
          has_parsed_text=True,
        )
      )
    elif citation_key in dropped:
      count = dropped[citation_key]
      items.append(
        AttentionItem(
          citation_key=citation_key,
          title=title,
          kind="facts_dropped",
          reason=f"{count} fact(s) dropped for unverifiable Evidence",
          dropped_fact_count=count,
          has_parsed_text=True,
        )
      )

  banner = IncompletenessBanner(
    total_papers=len(titles),
    unparsed=sum(1 for i in items if i.kind == "unparsed"),
    extraction_failed=sum(1 for i in items if i.kind == "extraction_failed"),
    papers_with_dropped_facts=sum(1 for i in items if i.kind == "facts_dropped"),
    dropped_facts=sum(i.dropped_fact_count for i in items),
  )
  return AttentionReport(banner=banner, items=items)


def _paper_titles(corpus_root: Path) -> dict[str, str]:
  """Map every manifest Paper's citation key to its title (key as a fallback)."""
  if not has_manifest(corpus_root):
    return {}
  manifest = load_manifest(corpus_root)
  return {
    paper.citation_key: paper.title or paper.citation_key for paper in manifest.papers
  }


def _parse_failures(corpus_root: Path) -> dict[str, str]:
  """Map each Paper the parse stage could not read to the reason it failed."""
  if not has_parse_report(corpus_root):
    return {}
  report = load_parse_report(corpus_root)
  return {failure.citation_key: failure.error for failure in report.failures}


def _extraction_outcomes(
  corpus_root: Path,
) -> tuple[dict[str, str], dict[str, int]]:
  """Return each Paper's extraction rejection reason and dropped-fact count."""
  if not has_extractions(corpus_root):
    return {}, {}
  artifact = load_extractions(corpus_root)
  failures = {f.citation_key: f.reason for f in artifact.failures}
  dropped: dict[str, int] = {}
  for fact in artifact.unverified:
    dropped[fact.citation_key] = dropped.get(fact.citation_key, 0) + 1
  return failures, dropped
