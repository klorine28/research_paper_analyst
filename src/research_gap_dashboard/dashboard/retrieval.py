"""
Assembling the Retrieval Gaps view from the RetrievalGaps artifact.

These are plain functions with no Streamlit dependency: the layout layer stays
thin by asking here for a finished RetrievalGapsData. Retrieval Gaps are the one
Gap Type that looks outside the Corpus (`CONTEXT.md` > Gap Type): out-of-corpus
candidate papers found by citation overlap, carrying no in-Corpus Evidence. This
view keeps the artifact's out-of-corpus label so the layout layer can state,
verbally and visually, that these are candidates for improving the search, not
evidence-linked gaps (CODING_STANDARDS.md > Research integrity). Candidates keep
the artifact's ranked order.
"""

from pydantic import BaseModel

from research_gap_dashboard.dashboard.artifacts import (
  RetrievalCandidateRecord,
  RetrievalGapsArtifact,
)


class RetrievalCandidateView(BaseModel):
  """One out-of-corpus candidate the view renders, with its citation overlap."""

  openalex_id: str
  doi: str
  title: str
  year: int | None
  venue: str
  authors: list[str]
  cited_by_count: int
  citation_overlap: int
  citing_citation_keys: list[str]


class RetrievalGapsData(BaseModel):
  """Everything the Retrieval Gaps page shows, already computed."""

  label: str
  source: str
  corpus_paper_count: int
  coupled_paper_count: int
  min_overlap: int
  candidates: list[RetrievalCandidateView]

  @property
  def candidate_count(self) -> int:
    """How many out-of-corpus candidates the Corpus produced."""
    return len(self.candidates)


def _candidate(record: RetrievalCandidateRecord) -> RetrievalCandidateView:
  """Assemble one display candidate from an artifact record."""
  return RetrievalCandidateView(
    openalex_id=record.openalex_id,
    doi=record.doi,
    title=record.title,
    year=record.year,
    venue=record.venue,
    authors=record.authors,
    cited_by_count=record.cited_by_count,
    citation_overlap=record.citation_overlap,
    citing_citation_keys=record.citing_citation_keys,
  )


def build_retrieval_gaps(artifact: RetrievalGapsArtifact) -> RetrievalGapsData:
  """Assemble the Retrieval Gaps view from the RetrievalGaps artifact."""
  return RetrievalGapsData(
    label=artifact.label,
    source=artifact.source,
    corpus_paper_count=artifact.corpus_paper_count,
    coupled_paper_count=artifact.coupled_paper_count,
    min_overlap=artifact.min_overlap,
    candidates=[_candidate(record) for record in artifact.candidates],
  )
