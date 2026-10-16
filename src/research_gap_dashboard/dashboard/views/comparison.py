"""The Paper Comparison page: chosen Papers side by side, with axis blind spots."""

import streamlit as st

from research_gap_dashboard.dashboard import text
from research_gap_dashboard.dashboard.artifacts import (
  CorpusChoice,
  has_extractions,
  has_normalized_facts,
  load_extractions,
  load_manifest,
  load_normalized_facts,
)
from research_gap_dashboard.dashboard.comparison import (
  MAX_SELECTION,
  AxisComparison,
  ComparisonResult,
  FieldComparison,
  build_comparison,
  selection_error,
)
from research_gap_dashboard.dashboard.views.run_controls import (
  render_last_run,
  render_run_buttons,
)
from research_gap_dashboard.dashboard.views.widgets import (
  paper_label,
)


def render_page(chosen: CorpusChoice) -> None:
  """Assemble the Paper Comparison page's data and render it."""
  corpus_root = chosen.root
  st.header(text.COMPARISON_HEADING)
  render_last_run(corpus_root)
  st.info(text.COMPARISON_INTRO)
  if not has_extractions(corpus_root) or not has_normalized_facts(corpus_root):
    st.warning(text.COMPARISON_NO_ARTIFACTS)
    render_run_buttons(corpus_root, ["extract", "aggregate"], scope="comparison")
    return

  manifest = load_manifest(corpus_root)
  entries = manifest.papers
  selection_keys = st.multiselect(
    text.COMPARISON_PICKER_LABEL,
    [paper.citation_key for paper in entries],
    format_func=lambda key: paper_label(manifest, key),
    max_selections=MAX_SELECTION,
  )
  if selection_error(selection_keys, manifest) is not None:
    st.info(text.COMPARISON_TOO_FEW)
    return

  result = build_comparison(
    selection_keys,
    load_extractions(corpus_root),
    load_normalized_facts(corpus_root),
    manifest,
  )
  render_comparison(result)


def render_comparison(result: ComparisonResult) -> None:
  """Render the Paper Comparison page from already-computed data."""
  if result.is_chunked:
    st.warning(
      text.COMPARISON_CHUNK_NOTICE.format(
        count=len(result.citation_keys), sets=len(result.chunks)
      )
    )

  st.subheader(text.COMPARISON_FIELDS_HEADING)
  for field in result.fields:
    _render_field_comparison(field)

  st.subheader(text.COMPARISON_AXES_HEADING)
  if not any(axis.agreements or axis.differences for axis in result.axes):
    st.caption(text.COMPARISON_NO_AXIS_SIGNAL)
  for axis in result.axes:
    _render_axis_comparison(axis)

  st.subheader(text.COMPARISON_BLIND_SPOTS_HEADING)
  st.caption(
    text.COMPARISON_BLIND_SPOTS_DENOMINATOR.format(total=result.corpus_paper_count)
  )
  blind = [axis for axis in result.axes if axis.blind_spots]
  if not blind:
    st.success(text.COMPARISON_NO_BLIND_SPOTS)
    return
  for axis in blind:
    st.markdown(
      text.COMPARISON_BLIND_SPOT_AXIS.format(
        axis=axis.axis_label,
        labels=", ".join(item.label for item in axis.blind_spots),
      )
    )


def _render_axis_comparison(axis: AxisComparison) -> None:
  """Render one axis's agreements and differences from finished data."""
  if not (axis.agreements or axis.differences):
    return
  st.markdown(f"**{axis.axis_label}**")
  if axis.agreements:
    st.caption(
      text.COMPARISON_AGREEMENTS_LABEL
      + " "
      + ", ".join(item.label for item in axis.agreements)
    )
  if axis.differences:
    st.caption(text.COMPARISON_DIFFERENCES_LABEL)
    for item in axis.differences:
      st.caption(
        "\u00b7 "
        + text.COMPARISON_DIFFERENCE_ITEM.format(
          label=item.label, keys=", ".join(item.covering_keys)
        )
      )


def _render_field_comparison(field: FieldComparison) -> None:
  """Render one field's statements, one column per compared unit."""
  st.markdown(f"**{field.label}**")
  columns = st.columns(len(field.units))
  for column, unit in zip(columns, field.units, strict=True):
    column.caption(unit.label)
    if not unit.statements:
      column.markdown(text.COMPARISON_NO_STATEMENTS)
      continue
    for statement in unit.statements:
      column.markdown(f"- {statement}")
