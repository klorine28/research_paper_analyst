"""
The Retrieval Gaps page: out-of-corpus candidates, kept apart from gap cards.

Each candidate can be added to the Corpus through the shared Grow path; one
marked unavailable is no longer offered and sits in a collapsed list (#91).
"""

from pathlib import Path

import streamlit as st

from research_gap_dashboard.dashboard import text
from research_gap_dashboard.dashboard.artifacts import (
  CorpusChoice,
  has_retrieval_gaps,
  load_retrieval_gaps,
  load_saturation_checks,
)
from research_gap_dashboard.dashboard.retrieval import (
  RetrievalCandidateView,
  RetrievalGapsData,
  build_retrieval_gaps,
)
from research_gap_dashboard.dashboard.suggestions import load_suggested_papers
from research_gap_dashboard.dashboard.views.run_controls import (
  render_last_run,
  render_run_buttons,
)
from research_gap_dashboard.dashboard.views.suggestions import (
  render_suggestion_actions,
)


def render_page(chosen: CorpusChoice) -> None:
  """Render the Retrieval Gaps page for the chosen Corpus."""
  if not has_retrieval_gaps(chosen.root):
    st.header(text.RETRIEVAL_HEADING)
    render_last_run(chosen.root)
    st.warning(text.NO_RETRIEVAL_GAPS_ARTIFACT)
    render_run_buttons(chosen.root, ["retrieve"], scope="retrieval")
    return
  data = build_retrieval_gaps(
    load_retrieval_gaps(chosen.root),
    load_saturation_checks(chosen.root),
    load_suggested_papers(chosen.root),
  )
  render_retrieval_gaps(data, chosen.root)


def render_retrieval_gaps(
  data: RetrievalGapsData, corpus_root: Path | None = None
) -> None:
  """
  Render the Retrieval Gaps page, kept clearly separate from gap cards.

  Without a Corpus root the candidates are shown read-only, with no Grow path.
  """
  st.header(text.RETRIEVAL_HEADING)
  if corpus_root is not None:
    render_last_run(corpus_root)
  st.warning(text.RETRIEVAL_SEPARATION_NOTICE)
  if not data.candidates and not data.unavailable:
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
    _render_retrieval_candidate(candidate, corpus_root)
  if data.unavailable:
    with st.expander(
      text.RETRIEVAL_UNAVAILABLE_HEADING.format(count=len(data.unavailable))
    ):
      for candidate in data.unavailable:
        _render_retrieval_candidate(candidate, corpus_root)


def _render_retrieval_candidate(
  candidate: RetrievalCandidateView, corpus_root: Path | None
) -> None:
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
    if candidate.saturation_gap_ids:
      st.caption(
        text.RETRIEVAL_ALSO_HIT.format(
          count=len(candidate.saturation_gap_ids),
          gaps=", ".join(f"`{gap}`" for gap in candidate.saturation_gap_ids),
        )
      )
    if corpus_root is not None:
      render_suggestion_actions(
        corpus_root, candidate.suggestion, candidate.state, scope="retrieval"
      )
