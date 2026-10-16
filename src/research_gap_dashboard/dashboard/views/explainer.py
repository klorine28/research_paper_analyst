"""The Paper Explainer page: pick a Paper, show its domain and lay explanations."""

from pathlib import Path

import streamlit as st

from research_gap_dashboard.dashboard import text
from research_gap_dashboard.dashboard.artifacts import (
  CorpusChoice,
  ManifestArtifact,
  explained_citation_keys,
  has_paper_explanation,
  load_manifest,
  load_paper_explanation,
)
from research_gap_dashboard.dashboard.explainer import (
  PaperExplainerView,
  PaperMenuData,
  PaperMenuEntry,
  build_paper_explainer,
  build_paper_menu,
)
from research_gap_dashboard.dashboard.views.run_controls import (
  render_last_run,
  render_run_buttons,
)
from research_gap_dashboard.dashboard.views.session_keys import (
  EXPLAINER_PAPER_KEY,
)


def render_page(chosen: CorpusChoice) -> None:
  """Assemble the Paper Explainer page's data and render it."""
  corpus_root = chosen.root
  manifest = load_manifest(corpus_root)
  menu = build_paper_menu(manifest, explained_citation_keys(corpus_root))
  if not menu.entries:
    st.header(text.EXPLAINER_HEADING)
    render_last_run(corpus_root)
    st.warning(text.EXPLAINER_NO_PAPERS)
    render_run_buttons(corpus_root, ["ingest"], scope="explainer")
    return

  pending_key = st.session_state.pop(EXPLAINER_PAPER_KEY, None)
  default_index = 0
  if pending_key is not None:
    default_index = next(
      (i for i, entry in enumerate(menu.entries) if entry.citation_key == pending_key),
      0,
    )
  chosen_paper = st.selectbox(
    text.EXPLAINER_PAPER_PICKER_LABEL,
    menu.entries,
    index=default_index,
    format_func=lambda entry: entry.label,
  )
  view = _load_explainer_view(corpus_root, manifest, chosen_paper)
  render_paper_explainer(menu, view, corpus_root)


def render_paper_explainer(
  menu: PaperMenuData, view: PaperExplainerView | None, corpus_root: Path
) -> None:
  """Render the Paper Explainer page: pick a Paper, show both registers."""
  st.header(text.EXPLAINER_HEADING)
  render_last_run(corpus_root)
  st.info(text.EXPLAINER_INTRO)
  st.caption(
    text.EXPLAINER_SUMMARY.format(
      explained=menu.explained_count, total=menu.paper_count
    )
  )
  if view is None:
    st.warning(text.NO_EXPLANATION_FOR_PAPER)
    render_run_buttons(corpus_root, ["explain"], scope="explainer-paper")
    return

  st.subheader(view.title or view.citation_key)
  st.caption(
    text.EXPLAINER_METADATA_VENUE.format(
      journal=view.journal,
      year=(
        text.EXPLAINER_METADATA_YEAR_PART.format(year=view.year)
        if view.year is not None
        else ""
      ),
    )
  )
  if view.doi:
    st.markdown(
      f"[{text.EXPLAINER_DOI_LABEL.format(doi=view.doi)}](https://doi.org/{view.doi})"
    )

  st.markdown(f"**{text.EXPLAINER_DOMAIN_HEADING}**")
  st.write(view.domain_explanation)
  st.markdown(f"**{text.EXPLAINER_LAY_HEADING}**")
  st.write(view.lay_explanation)


def _load_explainer_view(
  corpus_root: Path, manifest: ManifestArtifact, chosen: PaperMenuEntry
) -> PaperExplainerView | None:
  """Load the chosen Paper's explanation view, or None when it has none yet."""
  if not has_paper_explanation(corpus_root, chosen.citation_key):
    return None
  paper = next(p for p in manifest.papers if p.citation_key == chosen.citation_key)
  explanation = load_paper_explanation(corpus_root, chosen.citation_key)
  return build_paper_explainer(paper, explanation)
