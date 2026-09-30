"""
Assembling the Corpus Overview from the CorpusManifest artifact.

These are plain functions with no Streamlit dependency: the layout layer stays
thin by asking here for a finished OverviewData. Every count states what it was
computed over (CODING_STANDARDS.md > Research integrity: show the denominator),
so the Papers that were left out travel with the counts.
"""

from collections import Counter, defaultdict

from pydantic import BaseModel

from research_gap_dashboard.dashboard import text
from research_gap_dashboard.dashboard.artifacts import (
  ExtractionsArtifact,
  ManifestArtifact,
  NormalizedFactsArtifact,
)
from research_gap_dashboard.dashboard.attention import IncompletenessBanner

# The four descriptive axes the Corpus is placed on, in the order the overview
# shows them. Human labels live here, not in the detect stage (ADR 0002).
_AXIS_LABELS: dict[str, str] = {
  "topic": "Topic",
  "method": "Method",
  "population": "Population",
  "dataset": "Dataset",
}

# The seven Extraction fields whose facts count toward a Paper's density.
_EXTRACTION_FIELDS = (
  "research_question",
  "methods",
  "populations",
  "datasets",
  "key_findings",
  "limitations",
  "future_work",
)


class YearCount(BaseModel):
  """How many Papers in the Corpus carry a given publication year."""

  year: int | None
  count: int

  @property
  def label(self) -> str:
    """The year to show, naming the bucket for Papers with no year."""
    return str(self.year) if self.year is not None else text.UNKNOWN_YEAR_LABEL


class VenueCount(BaseModel):
  """How many Papers in the Corpus were published in a given venue."""

  venue: str
  count: int


class ExcludedEntry(BaseModel):
  """A paper-list entry outside the Corpus, with why it was left out."""

  label: str
  reason: str


class CompletenessGauge(BaseModel):
  """The honesty meter: how many Papers reached the artifact with real facts."""

  total_papers: int
  reached_papers: int
  missing_papers: int
  papers_with_dropped_facts: int

  @property
  def reached_fraction(self) -> float:
    """Share of Papers that reached the artifact, 0 when the Corpus is empty."""
    return self.reached_papers / self.total_papers if self.total_papers else 0.0

  @property
  def reached_percent(self) -> int:
    """The reached share as a whole-number percentage, for a headline metric."""
    return round(self.reached_fraction * 100)

  @property
  def is_complete(self) -> bool:
    """True when every Paper reached the artifact with fully verified facts."""
    return (
      self.missing_papers == 0
      and self.papers_with_dropped_facts == 0
      and self.total_papers > 0
    )


class CategoryCount(BaseModel):
  """How many Papers in the Corpus were placed on one category of an axis."""

  category_id: str
  label: str
  count: int


class AxisFrequency(BaseModel):
  """One descriptive axis and how often each of its categories appears."""

  axis: str
  label: str
  papers_placed: int
  categories: list[CategoryCount]


class PaperFactCount(BaseModel):
  """How many verified facts one Paper contributed to the Extraction."""

  citation_key: str
  label: str
  count: int


class FactDensity(BaseModel):
  """Facts-per-paper extraction density across the Papers that were extracted."""

  papers: list[PaperFactCount]
  total_facts: int

  @property
  def papers_with_facts(self) -> int:
    """How many Papers an Extraction record was written for."""
    return len(self.papers)

  @property
  def mean(self) -> float:
    """Mean facts per extracted Paper, 0 when nothing was extracted."""
    return self.total_facts / self.papers_with_facts if self.papers else 0.0


class OverviewData(BaseModel):
  """Everything the Corpus Overview page shows, already computed."""

  paper_count: int
  papers_per_year: list[YearCount]
  venues: list[VenueCount]
  earliest_year: int | None
  latest_year: int | None
  unmatched_entries: list[ExcludedEntry]
  orphan_pdfs: list[str]
  completeness: CompletenessGauge | None = None
  axis_frequencies: list[AxisFrequency] = []
  fact_density: FactDensity | None = None
  scope_statement: str = text.SCOPE_STATEMENT

  @property
  def venue_count(self) -> int:
    """How many distinct venues the Corpus draws from."""
    return len(self.venues)

  @property
  def has_exclusions(self) -> bool:
    """Whether any entry or PDF was left out of the Corpus."""
    return bool(self.unmatched_entries or self.orphan_pdfs)

  @property
  def year_span(self) -> str:
    """The Corpus's year range as text, or a dash when no years are known."""
    if self.earliest_year is None or self.latest_year is None:
      return "\u2014"
    if self.earliest_year == self.latest_year:
      return str(self.earliest_year)
    return f"{self.earliest_year}\u2013{self.latest_year}"


def build_overview(
  manifest: ManifestArtifact,
  *,
  normalized: NormalizedFactsArtifact | None = None,
  extractions: ExtractionsArtifact | None = None,
  completeness: IncompletenessBanner | None = None,
) -> OverviewData:
  """
  Compute the Corpus Overview from the CorpusManifest and optional artifacts.

  The manifest alone yields the headline counts, the year and venue charts, and
  the exclusions. When the later stages have run, their artifacts add the
  parse/extract completeness gauge (from the incompleteness banner), the per-axis
  frequency bars (from NormalizedFacts), and the facts-per-paper density (from
  Extractions). Each is optional so the page still renders on a freshly ingested
  Corpus.
  """
  years = [paper.year for paper in manifest.papers]
  known_years = [year for year in years if year is not None]
  corpus_keys = {paper.citation_key for paper in manifest.papers}
  return OverviewData(
    paper_count=len(manifest.papers),
    papers_per_year=_papers_per_year(years),
    venues=_venues(manifest),
    earliest_year=min(known_years) if known_years else None,
    latest_year=max(known_years) if known_years else None,
    unmatched_entries=[
      ExcludedEntry(
        label=entry.title or entry.citation_key or "(unnamed entry)",
        reason=entry.reason,
      )
      for entry in manifest.unmatched_entries
    ],
    orphan_pdfs=[path.name for path in manifest.orphan_pdfs],
    completeness=_completeness(completeness),
    axis_frequencies=_axis_frequencies(normalized, corpus_keys),
    fact_density=_fact_density(extractions, manifest),
  )


def _completeness(banner: IncompletenessBanner | None) -> CompletenessGauge | None:
  """Turn the incompleteness banner into the overview's honesty meter."""
  if banner is None:
    return None
  return CompletenessGauge(
    total_papers=banner.total_papers,
    reached_papers=banner.reached_artifact,
    missing_papers=banner.missing_papers,
    papers_with_dropped_facts=banner.papers_with_dropped_facts,
  )


def _axis_frequencies(
  normalized: NormalizedFactsArtifact | None, corpus_keys: set[str]
) -> list[AxisFrequency]:
  """Count distinct Corpus Papers per category, one bar group per axis."""
  if normalized is None:
    return []
  records = [r for r in normalized.normalized if r.citation_key in corpus_keys]
  frequencies: list[AxisFrequency] = []
  for axis, label in _AXIS_LABELS.items():
    papers_by_category: dict[str, set[str]] = defaultdict(set)
    labels: dict[str, str] = {}
    placed: set[str] = set()
    for record in records:
      for assignment in record.assignments:
        if assignment.axis != axis:
          continue
        placed.add(record.citation_key)
        papers_by_category[assignment.category_id].add(record.citation_key)
        labels.setdefault(
          assignment.category_id, assignment.category_label or assignment.category_id
        )
    if not papers_by_category:
      continue
    categories = [
      CategoryCount(category_id=cid, label=labels[cid], count=len(keys))
      for cid, keys in papers_by_category.items()
    ]
    categories.sort(key=lambda category: (-category.count, category.label))
    frequencies.append(
      AxisFrequency(
        axis=axis, label=label, papers_placed=len(placed), categories=categories
      )
    )
  return frequencies


def _fact_density(
  extractions: ExtractionsArtifact | None, manifest: ManifestArtifact
) -> FactDensity | None:
  """Count each extracted Paper's verified facts, most-productive first."""
  if extractions is None:
    return None
  titles = {
    paper.citation_key: (paper.title or paper.citation_key) for paper in manifest.papers
  }
  papers: list[PaperFactCount] = []
  total = 0
  for record in extractions.extractions:
    count = sum(len(getattr(record.fields, field)) for field in _EXTRACTION_FIELDS)
    total += count
    papers.append(
      PaperFactCount(
        citation_key=record.citation_key,
        label=titles.get(record.citation_key, record.citation_key),
        count=count,
      )
    )
  papers.sort(key=lambda paper: (-paper.count, paper.label))
  return FactDensity(papers=papers, total_facts=total)


def _papers_per_year(years: list[int | None]) -> list[YearCount]:
  """Count Papers per year, known years ascending, unknown year last."""
  counts = Counter(years)
  known = sorted(year for year in counts if year is not None)
  ordered = [YearCount(year=year, count=counts[year]) for year in known]
  if None in counts:
    ordered.append(YearCount(year=None, count=counts[None]))
  return ordered


def _venues(manifest: ManifestArtifact) -> list[VenueCount]:
  """Count Papers per venue, most-published first, ties broken by name."""
  counts = Counter(
    paper.journal.strip() for paper in manifest.papers if paper.journal.strip()
  )
  ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
  return [VenueCount(venue=venue, count=count) for venue, count in ranked]
