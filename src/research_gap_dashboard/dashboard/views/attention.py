"""The Needs Manual Attention page: Papers that fell out, with correction boxes."""

from pathlib import Path

import streamlit as st

from research_gap_dashboard.dashboard import text
from research_gap_dashboard.dashboard.artifacts import CorpusChoice
from research_gap_dashboard.dashboard.attention import (
  AttentionReport,
  build_attention,
)
from research_gap_dashboard.dashboard.parse_corrections import (
  EmptyCorrectionError,
  record_parse_correction,
)


def render_page(chosen: CorpusChoice) -> None:
  """Assemble and render the needs-manual-attention queue for a Corpus."""
  corpus_root = chosen.root
  render_attention(build_attention(corpus_root), corpus_root)


def render_attention(report: AttentionReport, corpus_root: Path) -> None:
  """Render the needs-manual-attention queue and per-Paper correction boxes."""
  st.header(text.ATTENTION_HEADING)
  banner = report.banner
  if banner.total_papers == 0:
    st.info(text.ATTENTION_NO_PIPELINE)
    return
  if banner.is_complete:
    st.success(text.ATTENTION_ALL_CLEAR)
    return

  st.warning(
    text.incompleteness_banner(
      banner.missing_papers,
      banner.papers_with_dropped_facts,
      banner.total_papers,
      banner.reached_artifact,
    )
  )

  _render_attention_section(
    report, corpus_root, "unparsed", text.ATTENTION_UNPARSED_HEADING, correct=True
  )
  _render_attention_section(
    report,
    corpus_root,
    "extraction_failed",
    text.ATTENTION_REJECTED_HEADING,
    correct=True,
  )
  _render_attention_section(
    report, corpus_root, "facts_dropped", text.ATTENTION_DROPPED_HEADING, correct=False
  )


def _render_attention_section(
  report: AttentionReport,
  corpus_root: Path,
  kind: str,
  heading: str,
  *,
  correct: bool,
) -> None:
  """Render one group of the queue, offering a correction box where it helps."""
  items = [item for item in report.items if item.kind == kind]
  if not items:
    return
  st.subheader(f"{heading} ({len(items)})")
  for item in items:
    with st.expander(f"{item.citation_key} — {item.title}"):
      st.write(item.reason)
      if correct:
        _render_correction_box(corpus_root, item.citation_key)


def _render_correction_box(corpus_root: Path, citation_key: str) -> None:
  """Render the paste-the-correct-text box that writes a judgments/ overlay."""
  pasted = st.text_area(
    text.ATTENTION_CORRECTION_LABEL,
    key=f"parse-correction-{citation_key}",
    help=text.ATTENTION_CORRECTION_HELP,
  )
  if not st.button(
    text.ATTENTION_SAVE_CORRECTION, key=f"save-correction-{citation_key}"
  ):
    return
  try:
    record_parse_correction(corpus_root, citation_key, pasted)
  except EmptyCorrectionError:
    st.warning(text.ATTENTION_CORRECTION_EMPTY)
    return
  st.success(text.ATTENTION_CORRECTION_SAVED)
