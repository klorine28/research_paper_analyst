"""
The Add Papers panel: upload PDFs and a paper list, see the pairing, ingest.

Uploads are placed through `dashboard.paper_upload` (Corpus inputs only, ADR
0007); the pairing check runs straight after, so unmatched entries and orphan
PDFs are on screen while the researcher can still fix them. The Ingest button
checks again first and launches `ingest` through the runner only when the
Corpus is inside the 10-75 Paper envelope, so a refused Corpus wastes no run.
"""

from pathlib import Path

import streamlit as st
from pydantic import BaseModel
from streamlit.runtime.uploaded_file_manager import UploadedFile

from research_gap_dashboard.dashboard import text
from research_gap_dashboard.dashboard.paper_upload import (
  PaperListPlacement,
  PairingCheck,
  PairingResult,
  PdfPlacement,
  UploadError,
  check_pairing,
  place_paper_list,
  place_pdfs,
)
from research_gap_dashboard.dashboard.pipeline_runner import RunOutcome, failure_reason
from research_gap_dashboard.dashboard.views.run_controls import (
  render_pending_confirmation,
  request_run,
)
from research_gap_dashboard.dashboard.views.session_keys import UPLOAD_STATE_KEY

_INGEST_KEY = "upload-ingest"


class _UploadState(BaseModel):
  """What the last upload placed and the pairing it was checked to, per Corpus."""

  corpus_root: str
  pdfs: PdfPlacement | None = None
  paper_list: PaperListPlacement | None = None
  paper_list_error: str | None = None
  pairing: PairingResult | None = None


def render_paper_upload(corpus_root: Path, *, expanded: bool) -> None:
  """Render the upload form, the last upload's outcome, and the pairing check."""
  with st.expander(text.UPLOAD_HEADING, expanded=expanded):
    st.caption(text.UPLOAD_INTRO)
    with st.form(f"upload_form_{corpus_root.name}", clear_on_submit=True):
      pdfs = st.file_uploader(
        text.UPLOAD_PDFS_LABEL, type=["pdf"], accept_multiple_files=True
      )
      paper_list = st.file_uploader(text.UPLOAD_PAPER_LIST_LABEL, type=["bib", "txt"])
      submitted = st.form_submit_button(text.UPLOAD_SUBMIT, type="primary")
    if submitted:
      _upload(corpus_root, pdfs or [], paper_list)
    state = _state(corpus_root)
    if state is not None:
      _render_placement(state)
    _render_pairing(corpus_root, state)


def _upload(
  corpus_root: Path, pdfs: list[UploadedFile], paper_list: UploadedFile | None
) -> None:
  """Place the uploaded files, check the pairing, and redraw with the outcome."""
  if not pdfs and paper_list is None:
    st.warning(text.UPLOAD_NOTHING)
    return
  state = _UploadState(corpus_root=str(corpus_root))
  if pdfs:
    state.pdfs = place_pdfs(corpus_root, [(pdf.name, pdf.getvalue()) for pdf in pdfs])
  if paper_list is not None:
    try:
      state.paper_list = place_paper_list(
        corpus_root, paper_list.name, paper_list.getvalue()
      )
    except UploadError as error:
      state.paper_list_error = str(error)
  state.pairing = check_pairing(corpus_root)
  st.session_state[UPLOAD_STATE_KEY] = state
  st.rerun()


def _state(corpus_root: Path) -> _UploadState | None:
  state = st.session_state.get(UPLOAD_STATE_KEY)
  if isinstance(state, _UploadState) and state.corpus_root == str(corpus_root):
    return state
  return None


def _render_placement(state: _UploadState) -> None:
  """Say what the last upload placed, replaced, and refused."""
  if state.pdfs is not None:
    if state.pdfs.added:
      st.success(text.UPLOAD_PDFS_ADDED.format(count=len(state.pdfs.added)))
    if state.pdfs.replaced:
      st.info(
        text.UPLOAD_PDFS_REPLACED.format(
          count=len(state.pdfs.replaced),
          names=", ".join(f"`{name}`" for name in state.pdfs.replaced),
        )
      )
    for refused in state.pdfs.refused:
      st.error(text.UPLOAD_PDF_REFUSED.format(name=refused.name, reason=refused.reason))
  if state.paper_list is not None:
    st.success(
      text.UPLOAD_LIST_ADDED.format(
        count=state.paper_list.added, name=state.paper_list.path.name
      )
    )
    if state.paper_list.already_listed:
      st.info(
        text.UPLOAD_LIST_ALREADY.format(
          keys=", ".join(f"`{key}`" for key in state.paper_list.already_listed)
        )
      )
  if state.paper_list_error is not None:
    st.error(state.paper_list_error)


def _render_pairing(corpus_root: Path, state: _UploadState | None) -> None:
  """Show the last pairing check, with buttons to check again and to ingest."""
  st.subheader(text.PAIRING_HEADING)
  st.caption(text.PAIRING_INTRO)
  pairing = state.pairing if state is not None else None
  if pairing is None:
    st.caption(text.PAIRING_NOT_CHECKED)
  else:
    _render_pairing_result(pairing)
  check_column, ingest_column = st.columns(2)
  if check_column.button(text.PAIRING_CHECK_BUTTON, key=f"pairing-check-{corpus_root}"):
    _store_pairing(corpus_root, state, check_pairing(corpus_root))
    st.rerun()
  with ingest_column:
    if render_pending_confirmation(corpus_root, _INGEST_KEY):
      return
    if st.button(text.INGEST_UPLOADED_BUTTON, key=_INGEST_KEY, type="primary"):
      _ingest(corpus_root, state)


def _ingest(corpus_root: Path, state: _UploadState | None) -> None:
  """Check the envelope again, then ingest only when the Corpus is inside it."""
  pairing = check_pairing(corpus_root)
  _store_pairing(corpus_root, state, pairing)
  if pairing.check is None or not pairing.check.within_envelope:
    st.rerun()
  request_run(corpus_root, ["ingest"], _INGEST_KEY)


def _store_pairing(
  corpus_root: Path, state: _UploadState | None, pairing: PairingResult
) -> None:
  updated = state or _UploadState(corpus_root=str(corpus_root))
  updated.pairing = pairing
  st.session_state[UPLOAD_STATE_KEY] = updated


def _render_pairing_result(pairing: PairingResult) -> None:
  """Render a pairing check, or why it could not run."""
  check = pairing.check
  if check is None:
    if pairing.run.outcome == RunOutcome.LOCKED:
      st.warning(text.PAIRING_LOCKED)
    else:
      reason = failure_reason(pairing.run) or pairing.run.outcome.value
      st.error(text.PAIRING_FAILED.format(reason=reason))
    return
  st.markdown(text.PAIRING_PAIRED.format(count=len(check.paired)))
  if check.unmatched_entries:
    rows = [
      text.PAIRING_UNMATCHED_ROW.format(
        key=unmatched.entry.citation_key,
        doi=f" ({unmatched.entry.doi})" if unmatched.entry.doi else "",
        title=f": {unmatched.entry.title}" if unmatched.entry.title else "",
      )
      for unmatched in check.unmatched_entries
    ]
    st.warning(
      "\n".join(
        [text.PAIRING_UNMATCHED.format(count=len(check.unmatched_entries)), "", *rows]
      )
    )
  if check.orphan_pdfs:
    rows = [
      text.PAIRING_ORPHAN_ROW.format(name=path.name) for path in check.orphan_pdfs
    ]
    st.warning(
      "\n".join([text.PAIRING_ORPHANS.format(count=len(check.orphan_pdfs)), "", *rows])
    )
  _render_envelope(check)


def _render_envelope(check: PairingCheck) -> None:
  count = len(check.paired)
  if check.within_envelope:
    st.success(
      text.PAIRING_READY.format(
        low=check.min_papers, high=check.max_papers, count=count
      )
    )
    return
  advice = text.PAIRING_TOO_MANY if count > check.max_papers else text.PAIRING_TOO_FEW
  st.error(
    text.PAIRING_REFUSED.format(
      count=count, low=check.min_papers, high=check.max_papers, advice=advice
    )
  )
