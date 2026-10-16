"""
The Verify Extractions page: review one Paper's extracted facts against its text.

Verdicts go to a `judgments/` overlay (ADR 0003). Promoting the reviewed
Extraction to a Gold Extraction is the pipeline's `promote-gold` command, which
the page launches through the runner (ADR 0007) rather than doing itself.
"""

from pathlib import Path

import streamlit as st

from research_gap_dashboard.dashboard import text
from research_gap_dashboard.dashboard.artifacts import (
  CorpusChoice,
  has_extractions,
  load_extractions,
  load_manifest,
)
from research_gap_dashboard.dashboard.extraction_review import (
  UnknownFieldError,
  add_fact,
  clear_fact_verdict,
  load_extraction_review,
  record_fact_verdict,
  remove_added_fact,
)
from research_gap_dashboard.dashboard.grounding import (
  UngroundedEvidenceError,
  has_parsed_paper,
  read_parsed_paper,
)
from research_gap_dashboard.dashboard.verify import (
  CorpusVerificationProgress,
  PaperReviewView,
  ReviewFactView,
  ReviewFieldView,
  build_corpus_progress,
  build_extraction_review,
)
from research_gap_dashboard.dashboard.views.run_controls import (
  render_last_run,
  render_run_button,
  render_run_buttons,
)
from research_gap_dashboard.dashboard.views.widgets import (
  paper_label,
  render_evidence_body,
)


def render_page(chosen: CorpusChoice) -> None:
  """Assemble the Verify Extractions page's data for a chosen Paper and render it."""
  corpus_root = chosen.root
  st.header(text.VERIFY_HEADING)
  render_last_run(corpus_root)
  if not has_extractions(corpus_root):
    st.warning(text.VERIFY_NO_EXTRACTIONS)
    render_run_buttons(corpus_root, ["extract"], scope="verify")
    return

  extractions = load_extractions(corpus_root)
  manifest = load_manifest(corpus_root)
  by_key = {paper.citation_key: paper for paper in manifest.papers}
  choices = [
    extraction
    for extraction in extractions.extractions
    if extraction.citation_key in by_key
  ]
  if not choices:
    st.warning(text.VERIFY_NO_EXTRACTIONS)
    render_run_buttons(corpus_root, ["extract"], scope="verify")
    return

  review_log = load_extraction_review(corpus_root)
  _render_corpus_progress(build_corpus_progress(choices, review_log, set(by_key)))
  _render_promote_gold(corpus_root)
  extraction = st.selectbox(
    text.VERIFY_PAPER_PICKER_LABEL,
    choices,
    format_func=lambda record: paper_label(manifest, record.citation_key),
  )
  citation_key = extraction.citation_key
  if not has_parsed_paper(corpus_root, citation_key):
    st.warning(text.VERIFY_NO_PARSED_TEXT)
    render_run_buttons(corpus_root, ["parse"], scope="verify-paper")
    return

  review = review_log.review_for(citation_key)
  view = build_extraction_review(extraction, review, by_key[citation_key], corpus_root)
  sections = read_parsed_paper(corpus_root, citation_key).section_labels
  render_verify(view, corpus_root, sections)


def _render_corpus_progress(progress: CorpusVerificationProgress) -> None:
  """Render how much of the whole Corpus has been verified, with its denominators."""
  summary = text.VERIFY_CORPUS_PROGRESS.format(
    reviewed=progress.reviewed_facts,
    total=progress.total_facts,
    fully=progress.fully_reviewed_papers,
    papers=progress.total_papers,
  )
  st.progress(progress.fraction)
  st.caption(summary)
  if progress.is_complete:
    st.success(text.VERIFY_CORPUS_COMPLETE)


def _render_promote_gold(corpus_root: Path) -> None:
  """Offer the promote-gold seam command and show how the last run went."""
  render_last_run(corpus_root)
  render_run_button(corpus_root, "promote-gold", text.VERIFY_PROMOTE_BUTTON)
  st.caption(text.VERIFY_PROMOTE_HELP)


def render_verify(
  view: PaperReviewView, corpus_root: Path, sections: list[str]
) -> None:
  """Render one Paper's review under the page heading, from already-assembled data."""
  st.info(text.VERIFY_INTRO)
  st.subheader(view.title or view.citation_key)
  _render_pdf_link(view)
  st.caption(
    text.VERIFY_SUMMARY.format(
      reviewed=view.reviewed_count,
      extracted=view.extracted_count,
      added=view.added_count,
    )
  )
  for field in view.fields:
    _render_verify_field(field, corpus_root, view.citation_key, sections)


def _render_verify_field(
  field: ReviewFieldView, corpus_root: Path, citation_key: str, sections: list[str]
) -> None:
  """Render one Extraction field: its facts, their verdicts, and an add-fact form."""
  st.subheader(field.label)
  if not field.facts:
    st.caption(text.VERIFY_NO_FACTS)
  for fact in field.facts:
    with st.container(border=True):
      if fact.is_added:
        _render_added_fact(fact, corpus_root, citation_key)
      else:
        _render_extracted_fact(fact, corpus_root, citation_key, sections)
  _render_add_fact(field, corpus_root, citation_key, sections)


def _render_extracted_fact(
  fact: ReviewFactView, corpus_root: Path, citation_key: str, sections: list[str]
) -> None:
  """Render one extracted fact with its Evidence and the five review verbs."""
  key = f"{citation_key}-{fact.field}-{fact.fact_index}"
  st.markdown(f"- {fact.effective_statement}")
  st.caption(text.VERIFY_SECTION_LABEL.format(section=fact.effective_section))
  render_evidence_body(fact.effective_passage, fact.context)
  st.caption(_verdict_label(fact.verdict))

  approve_col, flag_col, remove_col, clear_col = st.columns(4)
  if approve_col.button(text.VERIFY_APPROVE_BUTTON, key=f"approve-{key}"):
    record_fact_verdict(
      corpus_root, citation_key, fact.field, fact.fact_index, "approved"
    )
    st.rerun()
  if flag_col.button(text.VERIFY_FLAG_BUTTON, key=f"flag-{key}"):
    record_fact_verdict(
      corpus_root, citation_key, fact.field, fact.fact_index, "flagged"
    )
    st.rerun()
  if remove_col.button(text.VERIFY_REMOVE_BUTTON, key=f"remove-{key}"):
    record_fact_verdict(
      corpus_root, citation_key, fact.field, fact.fact_index, "removed"
    )
    st.rerun()
  if clear_col.button(text.VERIFY_CLEAR_BUTTON, key=f"clear-{key}"):
    clear_fact_verdict(corpus_root, citation_key, fact.field, fact.fact_index)
    st.rerun()

  _render_edit_fact(fact, corpus_root, citation_key, sections, key)


def _render_edit_fact(
  fact: ReviewFactView,
  corpus_root: Path,
  citation_key: str,
  sections: list[str],
  key: str,
) -> None:
  """Render the edit form for one extracted fact, refusing ungrounded Evidence."""
  with st.expander(text.VERIFY_EDIT_BUTTON):
    statement = st.text_area(
      text.VERIFY_EDIT_STATEMENT_LABEL,
      value=fact.effective_statement,
      key=f"edit-statement-{key}",
    )
    passage = st.text_area(
      text.VERIFY_EDIT_PASSAGE_LABEL,
      value=fact.effective_passage,
      key=f"edit-passage-{key}",
    )
    section = _section_picker(
      text.VERIFY_EDIT_SECTION_LABEL, sections, fact.effective_section, f"edit-{key}"
    )
    if st.button(text.VERIFY_EDIT_BUTTON, key=f"save-edit-{key}"):
      try:
        record_fact_verdict(
          corpus_root,
          citation_key,
          fact.field,
          fact.fact_index,
          "edited",
          edited_statement=statement,
          edited_passage=passage,
          edited_section=section,
        )
      except UngroundedEvidenceError:
        st.error(text.VERIFY_UNGROUNDED_ERROR)
        return
      st.rerun()


def _render_added_fact(
  fact: ReviewFactView, corpus_root: Path, citation_key: str
) -> None:
  """Render one reviewer-added fact, with a control to delete it again."""
  st.markdown(f"- {fact.effective_statement} *({text.VERIFY_ADDED_BADGE})*")
  st.caption(text.VERIFY_SECTION_LABEL.format(section=fact.effective_section))
  st.markdown(f"> {fact.effective_passage}")
  if st.button(text.VERIFY_DELETE_ADDED_BUTTON, key=f"delete-{fact.fact_id}"):
    remove_added_fact(corpus_root, citation_key, fact.fact_id)
    st.rerun()


def _render_add_fact(
  field: ReviewFieldView, corpus_root: Path, citation_key: str, sections: list[str]
) -> None:
  """Render the add-a-missing-fact form for one field, refusing ungrounded Evidence."""
  with st.expander(text.VERIFY_ADD_FACT_HEADING):
    key = f"add-{citation_key}-{field.field}"
    statement = st.text_area(text.VERIFY_ADD_STATEMENT_LABEL, key=f"{key}-statement")
    passage = st.text_area(text.VERIFY_ADD_PASSAGE_LABEL, key=f"{key}-passage")
    section = _section_picker(
      text.VERIFY_ADD_SECTION_LABEL,
      sections,
      sections[0] if sections else "other",
      f"{key}-section",
    )
    if st.button(text.VERIFY_ADD_BUTTON, key=f"{key}-button"):
      try:
        add_fact(corpus_root, citation_key, field.field, statement, passage, section)
      except UngroundedEvidenceError:
        st.error(text.VERIFY_UNGROUNDED_ERROR)
        return
      except UnknownFieldError:
        return
      st.rerun()


def _section_picker(label: str, sections: list[str], current: str, key: str) -> str:
  """Render a section picker seeded with the current section, falling back to text."""
  options = sections or [current]
  index = options.index(current) if current in options else 0
  return st.selectbox(label, options, index=index, key=key)


def _verdict_label(verdict: str | None) -> str:
  """Return the human label for a fact's current review verdict."""
  return {
    "approved": text.VERIFY_VERDICT_APPROVED,
    "edited": text.VERIFY_VERDICT_EDITED,
    "flagged": text.VERIFY_VERDICT_FLAGGED,
    "removed": text.VERIFY_VERDICT_REMOVED,
  }.get(verdict or "", text.VERIFY_VERDICT_UNREVIEWED)


def _render_pdf_link(view: PaperReviewView) -> None:
  """Render a link back to the Paper's PDF when its path is known."""
  if view.pdf_path is None:
    return
  # Manifest PDF paths may be relative to the corpus; resolve so the link is a
  # valid file URI, and fall back to showing the path when it cannot be a URI.
  try:
    uri = view.pdf_path.resolve().as_uri()
  except ValueError:
    st.caption(f"{text.VERIFY_PDF_LINK}: {view.pdf_path}")
    return
  st.markdown(f"[{text.VERIFY_PDF_LINK}]({uri})")
