"""
Rendering pieces more than one dashboard page uses.

The tile gallery and mini bar are shared by the Corpus Overview and the Field
Meta-Analysis; the evidence body by Gap Cards and Verify Extractions; the Paper
label by Comparison and Verify. Page-specific rendering stays in its page module.
"""

from collections.abc import Callable

import plotly.graph_objects as go
import streamlit as st

from research_gap_dashboard.dashboard import text
from research_gap_dashboard.dashboard.artifacts import (
  ManifestArtifact,
)
from research_gap_dashboard.dashboard.evidence_context import ExpandedEvidence
from research_gap_dashboard.dashboard.overview import MiniBar

_TILE_TEXT = "#0b2545"


_PREVIEW_HEIGHT = 150


def render_tile_gallery(
  prefix: str,
  tiles: list[tuple[str, str, str]],
  open_key: str,
  render_preview: Callable[[str], None],
  render_panel: Callable[[str], None],
  *,
  columns: int = 2,
) -> None:
  """
  Render a grid of coloured preview tiles, expanding the ones the reader opens.

  The one gallery idiom the Corpus Overview and the Field Meta-Analysis share:
  each chart is a collapsed, tinted tile with a miniature, and clicking it opens
  the full chart below the grid. `prefix` namespaces the widget keys and the CSS
  so two galleries on different pages never collide; `render_preview` and
  `render_panel` supply the page-specific thumbnail and full chart for a tile id.
  """
  _inject_gallery_styles(prefix, tiles)
  open_charts: set[str] = st.session_state.setdefault(open_key, set())
  for row_start in range(0, len(tiles), columns):
    row = st.columns(columns)
    for (chart_id, title, _shade), column in zip(
      tiles[row_start : row_start + columns], row
    ):
      with column, st.container(key=f"{prefix}_{chart_id}", border=True):
        st.markdown(f"**{title}**")
        render_preview(chart_id)
        is_open = chart_id in open_charts
        label = text.OVERVIEW_TILE_COLLAPSE if is_open else text.OVERVIEW_TILE_EXPAND
        if st.button(label, key=f"{prefix}_btn_{chart_id}", use_container_width=True):
          open_charts.symmetric_difference_update({chart_id})
          st.rerun()

  for chart_id, title, shade in tiles:
    if chart_id in open_charts:
      _render_tile_panel_header(title, shade)
      render_panel(chart_id)


def _inject_gallery_styles(prefix: str, tiles: list[tuple[str, str, str]]) -> None:
  """
  Tint each gallery tile its own shade of blue, keeping its text readable.

  The tile background is a light blue, but Streamlit text inherits the active
  theme's font colour (light in dark mode), which would vanish on a light tile.
  So the tile's text is pinned dark and the expand button is given an explicit
  light background, making both legible on every shade and in either theme.
  """
  blocks = []
  for chart_id, _title, shade in tiles:
    key = f".st-key-{prefix}_{chart_id}"
    blocks.append(
      f"{key}{{background:{shade};border-radius:0.6rem;}}"
      f"{key} p,{key} label,{key} strong,{key} span,"
      f"{key} h1,{key} h2,{key} h3{{color:{_TILE_TEXT} !important;}}"
      f"{key} button{{background:#ffffff !important;"
      f"color:{_TILE_TEXT} !important;"
      "border:1px solid rgba(11,37,69,0.25) !important;}"
      f"{key} button:hover{{background:#eef4fc !important;"
      f"color:{_TILE_TEXT} !important;}}"
    )
  st.markdown(f"<style>{''.join(blocks)}</style>", unsafe_allow_html=True)


def _render_tile_panel_header(title: str, shade: str) -> None:
  """Render the coloured header that opens an expanded gallery panel."""
  st.markdown(
    f"<div style='background:{shade};color:{_TILE_TEXT};"
    "padding:0.45rem 0.9rem;border-radius:0.4rem;font-weight:600;"
    f"margin-top:0.6rem;'>{title}</div>",
    unsafe_allow_html=True,
  )


def render_mini_bar(bar: MiniBar | None, *, key: str) -> None:
  """
  Render a compact bar thumbnail: value labels on bars, its text alternative below.

  A miniature shows the shape of a distribution at a glance with a little
  detail. Long category names (paper titles, venue names) would swallow a fixed
  small chart, so the x ticks stay hidden and each bar instead carries its
  count; the full names are on hover, in the text alternative under the chart,
  and in the expanded panel below.
  """
  if bar is None:
    st.caption(text.OVERVIEW_TILE_PREVIEW_EMPTY)
    return
  labels, values = bar.labels, bar.values
  figure = go.Figure(
    go.Bar(
      x=list(range(len(labels))),
      y=values,
      marker_color="#2f6fb3",
      customdata=labels,
      text=values,
      textposition="outside",
      textfont={"size": 10, "color": "#0b2545"},
      cliponaxis=False,
      hovertemplate="%{customdata}: %{y}<extra></extra>",
    )
  )
  headroom = max(values) * 1.25 if values else 1
  figure.update_layout(
    height=_PREVIEW_HEIGHT,
    margin={"l": 6, "r": 6, "t": 14, "b": 6},
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="rgba(0,0,0,0)",
    showlegend=False,
    bargap=0.25,
    xaxis={"visible": False, "fixedrange": True},
    yaxis={"visible": False, "fixedrange": True, "range": [0, headroom]},
  )
  st.plotly_chart(
    figure,
    use_container_width=True,
    config={"displayModeBar": False},
    key=f"ovmini_{key}",
  )
  st.caption(bar.summary)


def render_evidence_body(
  passage: str,
  context: ExpandedEvidence | None,
  *,
  full_section_toggle_key: str | None = None,
) -> None:
  """
  Show the verbatim anchor inside its paragraph, highlighted, with pointers.

  The anchor stays ground truth (CODING_STANDARDS > Research integrity); the code
  only widens it to the containing paragraph and highlights the anchor, so the
  reader gets context the dashboard did not invent. Falls back to the bare quote
  when no parsed text grounds the passage.

  Streamlit forbids nested expanders, so a caller already inside one passes
  `full_section_toggle_key` and the full section opens behind a toggle instead.
  """
  if context is None or not context.grounded:
    st.markdown(f"> {passage}")
    return

  prefix = "\u2026 " if context.truncated_before else ""
  suffix = " \u2026" if context.truncated_after else ""
  body = (
    f"{prefix}{context.before}"
    f":orange-background[{context.anchor}]"
    f"{context.after}{suffix}"
  )
  st.markdown(f"> {body}")
  if context.pointers:
    st.caption(
      text.EVIDENCE_POINTERS_LABEL.format(
        pointers=", ".join(pointer.label for pointer in context.pointers)
      )
    )
  if not context.full_section_adds_more:
    return
  if full_section_toggle_key is not None:
    if st.toggle(text.EVIDENCE_SHOW_FULL_SECTION, key=full_section_toggle_key):
      st.markdown(f"> {context.full_section}")
  else:
    with st.expander(text.EVIDENCE_SHOW_FULL_SECTION):
      st.markdown(f"> {context.full_section}")


def paper_label(manifest: ManifestArtifact, citation_key: str) -> str:
  """Return a human label for a Paper in the comparison picker."""
  paper = next(p for p in manifest.papers if p.citation_key == citation_key)
  name = paper.title or paper.citation_key
  return f"{name} ({paper.year})" if paper.year is not None else name
