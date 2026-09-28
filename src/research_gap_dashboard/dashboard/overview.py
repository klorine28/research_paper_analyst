"""
Assembling the Corpus Overview from the CorpusManifest artifact.

These are plain functions with no Streamlit dependency: the layout layer stays
thin by asking here for a finished OverviewData. Every count states what it was
computed over (CODING_STANDARDS.md > Research integrity: show the denominator),
so the Papers that were left out travel with the counts.
"""

from collections import Counter

from pydantic import BaseModel

from research_gap_dashboard.dashboard import text
from research_gap_dashboard.dashboard.artifacts import ManifestArtifact


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


class OverviewData(BaseModel):
  """Everything the Corpus Overview page shows, already computed."""

  paper_count: int
  papers_per_year: list[YearCount]
  venues: list[VenueCount]
  earliest_year: int | None
  latest_year: int | None
  unmatched_entries: list[ExcludedEntry]
  orphan_pdfs: list[str]
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


def build_overview(manifest: ManifestArtifact) -> OverviewData:
  """Compute the Corpus Overview from a CorpusManifest artifact."""
  years = [paper.year for paper in manifest.papers]
  known_years = [year for year in years if year is not None]
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
  )


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
