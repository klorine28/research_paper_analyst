"""The Retrieval Gaps page: out-of-corpus candidates, kept apart from gap cards."""

import streamlit as st

from research_gap_dashboard.dashboard import text
from research_gap_dashboard.dashboard.artifacts import (
  CorpusChoice,
  has_retrieval_gaps,
  load_retrieval_gaps,
)
from research_gap_dashboard.dashboard.retrieval import (
  RetrievalCandidateView,
  RetrievalGapsData,
  build_retrieval_gaps,
)


def render_page(chosen: CorpusChoice) -> None:
  """Render the Retrieval Gaps page for the chosen Corpus."""
  if not has_retrieval_gaps(chosen.root):
    st.header(text.RETRIEVAL_HEADING)
    st.warning(text.NO_RETRIEVAL_GAPS_ARTIFACT)
    return
  render_retrieval_gaps(build_retrieval_gaps(load_retrieval_gaps(chosen.root)))


def render_retrieval_gaps(data: RetrievalGapsData) -> None:
  """Render the Retrieval Gaps page, kept clearly separate from gap cards."""
  st.header(text.RETRIEVAL_HEADING)
  st.warning(text.RETRIEVAL_SEPARATION_NOTICE)
  if not data.candidates:
    st.success(text.NO_RETRIEVAL_CANDIDATES)
    return
  st.caption(
    text.RETRIEVAL_SUMMARY.format(
      total=data.candidate_count,
      coupled=data.coupled_paper_count,
      corpus=data.corpus_paper_count,
      min_overlap=data.min_overlap,
    )
  )
  if data.no_doi_paper_count:
    st.caption(text.RETRIEVAL_NO_DOI.format(count=data.no_doi_paper_count))
  st.caption(text.RETRIEVAL_SOURCE_LABEL.format(source=data.source))
  for candidate in data.candidates:
    _render_retrieval_candidate(candidate)


def _render_retrieval_candidate(candidate: RetrievalCandidateView) -> None:
  """Render one out-of-corpus candidate with its citation overlap."""
  with st.container(border=True):
    st.markdown(f"**{candidate.title or candidate.openalex_id}**")
    if candidate.authors:
      st.caption(
        text.RETRIEVAL_AUTHORS_LABEL.format(authors=", ".join(candidate.authors))
      )
    st.caption(
      text.RETRIEVAL_META_LABEL.format(
        venue=candidate.venue,
        year=(
          text.RETRIEVAL_YEAR_PART.format(year=candidate.year)
          if candidate.year is not None
          else ""
        ),
        cited_by=(
          text.RETRIEVAL_CITED_BY_PART.format(count=candidate.cited_by_count)
          if candidate.cited_by_count
          else ""
        ),
      )
    )
    st.caption(
      text.RETRIEVAL_OVERLAP_LABEL.format(
        overlap=candidate.citation_overlap,
        papers=", ".join(candidate.citing_citation_keys),
      )
    )
    if candidate.doi:
      st.caption(text.RETRIEVAL_DOI_LABEL.format(doi=candidate.doi))
