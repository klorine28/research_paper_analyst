"""The Field Meta-Analysis page: the six graph tiles and their full-size panels."""

from dataclasses import dataclass
from typing import Literal

import plotly.graph_objects as go
import streamlit as st

from research_gap_dashboard.dashboard import text
from research_gap_dashboard.dashboard.artifacts import (
  CorpusChoice,
  has_candidate_gaps,
  has_manifest,
  has_normalized_facts,
  load_candidate_gaps,
  load_manifest,
  load_normalized_facts,
)
from research_gap_dashboard.dashboard.coverage import (
  TrendsData,
  build_trends,
  build_trends_figure,
)
from research_gap_dashboard.dashboard.limitations import (
  build_limitations,
)
from research_gap_dashboard.dashboard.meta_analysis import (
  MISSING_PAIRS_SHOWN,
  AuthorCollaborationTable,
  LimitationFollowUpView,
  NetworkGraph,
  build_author_table,
  build_citation_network,
  build_cooccurrence,
  build_limitation_bar_figure,
  build_limitation_flow,
  build_network_figure,
)
from research_gap_dashboard.dashboard.views.session_keys import (
  EXPLAINER_PAPER_KEY,
  PENDING_NAV_KEY,
)
from research_gap_dashboard.dashboard.views.widgets import (
  render_mini_bar,
  render_tile_gallery,
)

# The Field Meta-Analysis gallery: the six graphs as the same collapsible tiles
# the Corpus Overview uses, so a reader scans thumbnails and opens the ones they
# want full size instead of scrolling six interactive charts (shared idiom).
_META_TILES: list[tuple[str, str, str]] = [
  ("citation", text.META_CITATION_TITLE, "#e8f1fc"),
  ("topic", text.META_TOPIC_TITLE, "#cfe3f8"),
  ("method", text.META_METHOD_TITLE, "#b6d4f2"),
  ("trends", text.TRENDS_HEADING, "#9dc4ec"),
  ("authors", text.META_COLLABORATION_TITLE, "#86b4e6"),
  ("limitation", text.META_LIMITATION_TITLE, "#6fa4df"),
]


META_OPEN_KEY = "meta_open_charts"


_META_THUMB_HEIGHT = 170


def render_page(chosen: CorpusChoice) -> None:
  """Render the Field Meta-Analysis page as a gallery of the six graph tiles."""
  st.header(text.META_HEADING)
  if not has_manifest(chosen.root):
    st.warning(text.META_NO_MANIFEST)
    return
  st.info(text.META_INTRO)
  st.caption(text.META_GALLERY_HINT)
  manifest = load_manifest(chosen.root)
  normalized = (
    load_normalized_facts(chosen.root) if has_normalized_facts(chosen.root) else None
  )
  has_gaps = has_candidate_gaps(chosen.root)

  # Every builder here is a pure read over on-disk artifacts (no LLM), so building
  # the base graphs up front for the thumbnails is cheap; the panels rebuild the
  # co-occurrence graphs with the reader's slider state.
  bundle = _MetaBundle(
    citation=build_citation_network(manifest),
    authors=build_author_table(manifest),
    topic=build_cooccurrence(normalized, manifest, axis="topic", max_nodes=25)
    if normalized is not None
    else None,
    method=build_cooccurrence(normalized, manifest, axis="method", max_nodes=25)
    if normalized is not None
    else None,
    trends=build_trends(normalized, manifest) if normalized is not None else None,
    limitation=build_limitation_flow(
      build_limitations(load_candidate_gaps(chosen.root))
    )
    if has_gaps
    else None,
  )

  render_tile_gallery(
    "metatile",
    _META_TILES,
    META_OPEN_KEY,
    lambda chart_id: _render_meta_preview(chart_id, bundle),
    lambda chart_id: _render_meta_panel(
      chart_id, manifest, normalized, has_gaps, bundle
    ),
  )


@dataclass
class _MetaBundle:
  """The pre-built graphs a meta gallery tile needs for its thumbnail and panel."""

  citation: NetworkGraph
  authors: AuthorCollaborationTable
  topic: NetworkGraph | None
  method: NetworkGraph | None
  trends: TrendsData | None
  limitation: LimitationFollowUpView | None


def _render_meta_thumb(figure: go.Figure, key: str) -> None:
  """Render a small, non-interactive thumbnail of a meta graph inside its tile."""
  figure.update_layout(
    height=_META_THUMB_HEIGHT,
    margin={"l": 4, "r": 4, "t": 8, "b": 4},
    showlegend=False,
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="rgba(0,0,0,0)",
  )
  st.plotly_chart(
    figure,
    use_container_width=True,
    config={"displayModeBar": False, "staticPlot": True},
    key=f"metathumb_{key}",
  )


def _render_meta_preview(chart_id: str, bundle: _MetaBundle) -> None:
  """Render a tile's thumbnail: a miniature graph with a stat, or why it's empty."""
  if chart_id == "citation":
    _render_network_preview(bundle.citation)
  elif chart_id == "topic":
    _render_network_preview(bundle.topic)
  elif chart_id == "method":
    _render_network_preview(bundle.method)
  elif chart_id == "trends":
    if bundle.trends is not None and bundle.trends.has_data:
      _render_meta_thumb(build_trends_figure(bundle.trends), "trends")
    else:
      st.caption(text.META_PREVIEW_EMPTY)
  elif chart_id == "authors":
    rows = bundle.authors.rows[:8]
    if rows:
      render_mini_bar(
        [row.author for row in rows], [row.paper_count for row in rows], key="authors"
      )
    else:
      st.caption(text.META_PREVIEW_EMPTY)
  elif chart_id == "limitation":
    if bundle.limitation is not None and not bundle.limitation.is_empty:
      _render_meta_thumb(build_limitation_bar_figure(bundle.limitation), "limitation")
    else:
      st.caption(text.META_PREVIEW_EMPTY)


def _render_network_preview(graph: NetworkGraph | None) -> None:
  """Render a small network thumbnail with its node/edge/isolated counts."""
  if graph is None or graph.is_empty:
    st.caption(text.META_PREVIEW_EMPTY)
    return
  _render_meta_thumb(build_network_figure(graph, thumbnail=True), graph.slug)
  st.caption(
    text.META_FIGURE_SUMMARY.format(
      nodes=len(graph.nodes),
      edges=graph.edge_count,
      isolated=len(graph.isolated_labels),
    )
  )


def _render_meta_panel(  # noqa: ANN001
  chart_id: str, manifest, normalized, has_gaps: bool, bundle: _MetaBundle
) -> None:
  """Render a tile's full chart below the grid (the coloured header is generic)."""
  if chart_id == "citation":
    render_network_graph(bundle.citation, show_title=False)
  elif chart_id in ("topic", "method"):
    if normalized is None:
      st.info(text.META_NO_NORMALIZED)
    else:
      _render_cooccurrence(normalized, manifest, axis=chart_id, show_title=False)
  elif chart_id == "trends":
    if bundle.trends is None:
      st.info(text.META_NO_NORMALIZED)
    else:
      _render_trends(bundle.trends)
  elif chart_id == "authors":
    render_author_table(bundle.authors, show_title=False)
  elif chart_id == "limitation":
    if not has_gaps or bundle.limitation is None:
      st.info(text.NO_CANDIDATE_GAPS_ARTIFACT_LIMITATIONS)
    else:
      render_limitation_flow(bundle.limitation, show_title=False)


def _render_cooccurrence(  # noqa: ANN001
  normalized, manifest, *, axis: Literal["topic", "method"], show_title: bool = True
) -> None:
  """Render one co-occurrence graph with its min-weight and node-count controls."""
  weight_key = f"meta_minw_{axis}"
  nodes_key = f"meta_maxn_{axis}"
  st.session_state.setdefault(weight_key, 1)
  st.session_state.setdefault(nodes_key, 25)
  # Build once unfiltered to learn the heaviest link, so the threshold slider's
  # range stays stable even after the filter thins the visible edges.
  base = build_cooccurrence(
    normalized, manifest, axis=axis, max_nodes=st.session_state[nodes_key]
  )
  peak = max((edge.weight for edge in base.edges), default=1)
  if st.session_state[weight_key] > peak:
    st.session_state[weight_key] = peak
  graph = build_cooccurrence(
    normalized,
    manifest,
    axis=axis,
    max_nodes=st.session_state[nodes_key],
    min_edge_weight=st.session_state[weight_key],
  )
  render_network_graph(graph, axis=axis, peak=peak, show_title=show_title)


def _render_graph_controls(peak: int, axis: str) -> None:
  """Draw the min-weight and max-node sliders for a co-occurrence graph."""
  with st.expander(text.META_COOCCURRENCE_CONTROLS_LABEL):
    if peak > 1:
      st.slider(
        text.META_MIN_EDGE_LABEL, min_value=1, max_value=peak, key=f"meta_minw_{axis}"
      )
    st.slider(
      text.META_MAX_NODES_LABEL, min_value=5, max_value=25, key=f"meta_maxn_{axis}"
    )


def render_network_graph(
  graph: NetworkGraph,
  *,
  axis: str | None = None,
  peak: int = 1,
  show_title: bool = True,
) -> None:
  """Render one meta-analysis network graph with its question, caveat, and table."""
  if show_title:
    st.subheader(graph.title)
  st.caption(f"**{text.META_QUESTION_LABEL}:** {graph.question}")
  st.caption(f"**{text.META_GAP_LENS_LABEL}:** {graph.gap_lens}")
  if graph.is_empty:
    if axis is not None:
      st.info(text.META_COOCCURRENCE_EMPTY_REASON.format(axis=axis))
    else:
      st.info(text.META_EMPTY_GRAPH)
    for note in graph.notes:
      st.caption(note)
    return
  if axis is not None:
    _render_graph_controls(peak, axis)
  st.caption(text.META_CLICK_HINT)
  _render_interactive_network(graph)
  st.caption(
    text.META_FIGURE_SUMMARY.format(
      nodes=len(graph.nodes),
      edges=graph.edge_count,
      isolated=len(graph.isolated_labels),
    )
  )
  for note in graph.notes:
    st.caption(note)
  if graph.isolated_labels:
    st.caption(text.META_ISOLATED_LEGEND + ": " + ", ".join(graph.isolated_labels))
  _render_missing_pairs(graph)
  st.caption(graph.caveat)
  _render_graph_data_table(graph)


def _render_interactive_network(graph: NetworkGraph) -> None:
  """Draw the network, handle a node click, and show the selection controls."""
  select_key = f"meta_sel_{graph.slug}"
  selected = st.session_state.get(select_key)
  event = st.plotly_chart(
    build_network_figure(graph, selected_id=selected),
    use_container_width=True,
    key=f"meta-{graph.slug}",
    on_select="rerun",
    selection_mode="points",
  )
  clicked = _clicked_node_id(event)
  if clicked != selected:
    st.session_state[select_key] = clicked
    st.rerun()
  if selected and selected in graph.node_ids:
    _render_node_selection(graph, selected)


def _render_node_selection(graph: NetworkGraph, selected: str) -> None:
  """Show the selected node's neighbours, a clear button, and any Paper jump."""
  node = next(n for n in graph.nodes if n.node_id == selected)
  neighbours = sorted(graph.neighbors(selected))
  if neighbours:
    st.caption(
      text.META_SELECTED_CAPTION.format(
        label=node.label, count=len(neighbours), neighbours=", ".join(neighbours)
      )
    )
  else:
    st.caption(text.META_SELECTED_NONE.format(label=node.label))
  clear_col, jump_col = st.columns(2)
  if clear_col.button(text.META_CLEAR_SELECTION, key=f"meta-clear-{graph.slug}"):
    st.session_state[f"meta_sel_{graph.slug}"] = None
    st.rerun()
  # A citation-network node *is* a Corpus Paper (node id is its citation key), so
  # it can open that Paper's Explainer; co-occurrence nodes are categories, not
  # Papers, and offer the missing-pair jump instead.
  if graph.kind == "citation" and jump_col.button(
    text.META_OPEN_NODE_JUMP, key=f"meta-open-{graph.slug}"
  ):
    st.session_state[EXPLAINER_PAPER_KEY] = selected
    st.session_state[PENDING_NAV_KEY] = text.PAGE_EXPLAINER
    st.rerun()


def _clicked_node_id(event: object) -> str | None:
  """Read the clicked node id out of a plotly selection event, or None."""
  selection = getattr(event, "selection", None)
  if selection is None and isinstance(event, dict):
    selection = event.get("selection")
  points = (selection or {}).get("points") if selection else None
  if not points:
    return None
  custom = points[0].get("customdata")
  if isinstance(custom, list):
    return str(custom[0]) if custom else None
  return str(custom) if custom is not None else None


def _render_missing_pairs(graph: NetworkGraph) -> None:
  """List the strongest absent edges, each a button jumping to the Gap Cards page."""
  if not graph.missing_pairs:
    if graph.kind == "cooccurrence":
      st.caption(text.META_NO_MISSING_PAIRS)
    return
  shown = graph.missing_pairs[:MISSING_PAIRS_SHOWN]
  label = f"{text.META_MISSING_PAIRS_LABEL} ({len(graph.missing_pairs)})"
  with st.expander(label):
    st.caption(text.META_MISSING_PAIRS_HINT)
    for index, pair in enumerate(shown):
      label_col, button_col = st.columns([4, 1])
      strength = (
        "  \u00b7  " + text.META_MISSING_STRENGTH.format(strength=pair.strength)
        if pair.strength > 1
        else ""
      )
      label_col.markdown(
        text.META_MISSING_PAIR_ROW.format(
          source=pair.source_label, target=pair.target_label
        )
        + strength
      )
      if button_col.button(
        text.META_MISSING_PAIR_JUMP,
        key=f"meta-jump-{graph.slug}-{index}",
        width="stretch",
      ):
        st.session_state[PENDING_NAV_KEY] = text.PAGE_GAP_CARDS
        st.rerun()
    if len(graph.missing_pairs) > len(shown):
      st.caption(
        text.META_MISSING_MORE.format(more=len(graph.missing_pairs) - len(shown))
      )


def render_author_table(
  table: AuthorCollaborationTable, *, show_title: bool = True
) -> None:
  """Render the author collaboration view as a ranked table, not a hairball."""
  if show_title:
    st.subheader(table.title)
  st.caption(f"**{text.META_QUESTION_LABEL}:** {table.question}")
  st.caption(f"**{text.META_GAP_LENS_LABEL}:** {table.gap_lens}")
  if table.is_empty:
    st.info(text.META_EMPTY_GRAPH)
    for note in table.notes:
      st.caption(note)
    return
  st.dataframe(
    [
      {
        text.META_COLLABORATION_COL_AUTHOR: row.author,
        text.META_COLLABORATION_COL_PAPERS: row.paper_count,
        text.META_COLLABORATION_COL_COLLABORATORS: row.collaborator_count,
      }
      for row in table.rows
    ],
    use_container_width=True,
    hide_index=True,
  )
  for note in table.notes:
    st.caption(note)
  st.caption(table.caveat)


def _render_graph_data_table(graph: NetworkGraph) -> None:
  """Show the graph's edges as a table: the accessible, non-interactive fallback."""
  rows = [
    {"Source": edge.source, "Target": edge.target, "Papers": edge.weight}
    for edge in graph.edges
  ]
  with st.expander(text.META_DATA_TABLE_LABEL):
    if rows:
      st.dataframe(rows, use_container_width=True, hide_index=True)
    else:
      st.caption(text.META_EMPTY_GRAPH)


def render_limitation_flow(
  view: LimitationFollowUpView, *, show_title: bool = True
) -> None:
  """Render the limitation follow-up status bar with its question and caveat."""
  if show_title:
    st.subheader(view.title)
  st.caption(f"**{text.META_QUESTION_LABEL}:** {view.question}")
  st.caption(f"**{text.META_GAP_LENS_LABEL}:** {view.gap_lens}")
  if view.is_empty:
    st.info(text.META_LIMITATION_NO_DATA)
    return
  st.plotly_chart(
    build_limitation_bar_figure(view), use_container_width=True, key="meta-limitation"
  )
  st.caption(
    text.META_LIMITATION_SUMMARY.format(
      total=view.total,
      addressed=view.addressed_count,
      open=view.open_checked_count,
      no_later=view.no_later_count,
    )
  )
  if view.top_addressed:
    st.caption(text.META_LIMITATION_TOP_ADDRESSED)
    for label, count in view.top_addressed:
      st.caption(text.META_LIMITATION_TOP_ROW.format(label=label, count=count))
  elif view.all_open:
    st.info(text.META_LIMITATION_ALL_OPEN_NOTE.format(no_later=view.no_later_count))
  st.caption(view.caveat)


def _render_trends(trends: TrendsData) -> None:
  """Render the Trends chart with its denominator and emerging/abandoned lines."""
  with st.expander(text.HOW_TO_READ_LABEL):
    st.markdown(text.TRENDS_EXPLAINER)
  if not trends.has_data:
    st.info(text.TRENDS_NO_DATA)
    return
  st.caption(
    text.TRENDS_DENOMINATOR.format(
      with_year=trends.papers_with_year, total=trends.corpus_paper_count
    )
  )
  if trends.papers_without_year:
    st.caption(text.TRENDS_MISSING_YEAR.format(without_year=trends.papers_without_year))
  st.plotly_chart(build_trends_figure(trends), use_container_width=True)

  emerging = trends.emerging
  abandoned = trends.abandoned
  if not emerging and not abandoned:
    st.caption(text.TRENDS_NO_HIGHLIGHTS)
    return
  if emerging:
    st.caption(
      text.TRENDS_EMERGING_LABEL.format(
        topics=", ".join(topic.label for topic in emerging)
      )
    )
  if abandoned:
    st.caption(
      text.TRENDS_ABANDONED_LABEL.format(
        topics=", ".join(topic.label for topic in abandoned)
      )
    )
