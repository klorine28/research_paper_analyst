"""The Conversational Analytics page: named grounded threads, a narrative, a journal."""

from pathlib import Path

import streamlit as st

from research_gap_dashboard import analytics as analytics_api
from research_gap_dashboard.analytics import (
  CorpusPaper,
  GapFact,
  GroundedChatAnswer,
  GroundedNarrative,
  GroundingContext,
  LlmClient,
  answer_question,
  summarize_gaps,
)
from research_gap_dashboard.dashboard import conversations as convo
from research_gap_dashboard.dashboard import text
from research_gap_dashboard.dashboard.artifacts import (
  CorpusChoice,
  ManifestArtifact,
  has_candidate_gaps,
  has_manifest,
  load_candidate_gaps,
  load_manifest,
)
from research_gap_dashboard.dashboard.chat_history import ChatTurn
from research_gap_dashboard.dashboard.conversations import Conversation
from research_gap_dashboard.dashboard.corpus_scope import load_scope
from research_gap_dashboard.dashboard.entry_log import reconcile_entry_log
from research_gap_dashboard.dashboard.export import build_write_up
from research_gap_dashboard.dashboard.fact_pool import load_fact_pool
from research_gap_dashboard.dashboard.gaps import (
  GapCard,
  NarrativeScope,
  build_gap_cards,
  narrative_scope,
  scope_context_to_gap,
)
from research_gap_dashboard.dashboard.journal import (
  delete_entry,
  load_journal,
  promote_to_journal,
  render_notes_for_context,
)
from research_gap_dashboard.dashboard.judgments import (
  load_judgments,
)
from research_gap_dashboard.dashboard.views.run_controls import (
  render_last_run,
  render_run_buttons,
)
from research_gap_dashboard.dashboard.views.session_keys import (
  DISCUSS_GAP_KEY,
  NARRATIVE_KEY,
)

# The dashboard caches Conversational Analytics answers alongside the pipeline's
# other LLM calls, keyed by their inputs (ADR 0001), so a repeat question is free.
LLM_CACHE_DIR = ".llm-cache"


# Which conversation thread the Analytics page is showing. Persisted threads live
# on disk (issue #50); this only remembers the researcher's current selection.
ACTIVE_CONVERSATION_KEY = "active_conversation_id"


def render_page(chosen: CorpusChoice) -> None:
  """Render Conversational Analytics: named threads, a journal, a narrative summary."""
  corpus_root = chosen.root
  st.header(text.ANALYTICS_HEADING)
  render_last_run(corpus_root)
  st.info(text.ANALYTICS_INTRO)
  if not has_manifest(corpus_root):
    st.warning(text.ANALYTICS_NO_MANIFEST)
    render_run_buttons(corpus_root, ["ingest"], scope="analytics")
    return

  # Looked up on the module at call time, not bound at import: this page module
  # outlives Streamlit's per-run re-execution of the shell, and tests swap the
  # factory for a keyless stub.
  client = analytics_api.build_analytics_client(corpus_root / LLM_CACHE_DIR)
  if client is None:
    st.warning(text.ANALYTICS_NO_KEY)
    return

  manifest = load_manifest(corpus_root)
  has_gaps = has_candidate_gaps(corpus_root)

  conversation = _active_conversation(corpus_root)
  _render_conversation_picker(corpus_root, conversation)
  context = _conversation_context(corpus_root, manifest, has_gaps, conversation)
  _render_conversation(corpus_root, conversation, context, client)

  st.divider()
  _render_narrative_summary(
    chosen,
    manifest,
    _grounding_context(corpus_root, manifest, has_gaps),
    client,
    has_gaps,
  )
  st.divider()
  _render_journal(corpus_root)


def _active_conversation(corpus_root: Path) -> Conversation:
  """
  Resolve which conversation to show, opening it when it is new (issue #50).

  A pending 'Discuss this gap' click (issue #48) opens or reopens the thread
  scoped to that Candidate Gap and makes it active; otherwise the thread remembered
  in session state is shown, defaulting to the general whole-Corpus thread. Both
  the per-gap and general threads persist across sessions.
  """
  pending_gap = st.session_state.pop(DISCUSS_GAP_KEY, None)
  if pending_gap:
    card = _scoped_gap_card(corpus_root, pending_gap)
    if card is not None:
      cid = convo.conversation_id_for_gap(card.gap_id)
      convo.get_or_create_conversation(
        corpus_root,
        cid,
        title=text.ANALYTICS_SCOPED_TITLE.format(title=card.title),
        gap_id=card.gap_id,
      )
      st.session_state[ACTIVE_CONVERSATION_KEY] = cid

  convo.get_or_create_conversation(
    corpus_root,
    convo.GENERAL_CONVERSATION_ID,
    title=text.ANALYTICS_GENERAL_TITLE,
    gap_id=None,
  )
  active_id = st.session_state.get(
    ACTIVE_CONVERSATION_KEY, convo.GENERAL_CONVERSATION_ID
  )
  conversation = convo.load_conversation(corpus_root, active_id)
  if conversation is None:
    st.session_state[ACTIVE_CONVERSATION_KEY] = convo.GENERAL_CONVERSATION_ID
    conversation = convo.load_conversation(corpus_root, convo.GENERAL_CONVERSATION_ID)
  assert conversation is not None  # general is created just above
  return conversation


def _render_conversation_picker(corpus_root: Path, conversation: Conversation) -> None:
  """Pick, start, rename, or delete a conversation thread (issue #50)."""
  summaries = convo.list_conversations(corpus_root)
  ids = [summary.conversation_id for summary in summaries]
  labels = {
    summary.conversation_id: text.ANALYTICS_CONVERSATION_OPTION.format(
      title=summary.title, turns=summary.turn_count
    )
    for summary in summaries
  }
  index = (
    ids.index(conversation.conversation_id)
    if conversation.conversation_id in ids
    else 0
  )

  def _label(conversation_id: str) -> str:
    return labels.get(conversation_id, conversation_id)

  chosen = st.selectbox(
    text.ANALYTICS_CONVERSATION_PICKER_LABEL,
    ids,
    index=index,
    format_func=_label,
  )
  if chosen != conversation.conversation_id:
    st.session_state[ACTIVE_CONVERSATION_KEY] = chosen
    st.rerun()

  new_col, delete_col = st.columns(2)
  if new_col.button(text.ANALYTICS_NEW_CONVERSATION_BUTTON):
    cid = convo.new_conversation_id()
    convo.get_or_create_conversation(
      corpus_root, cid, title=text.ANALYTICS_NEW_CONVERSATION_TITLE, gap_id=None
    )
    st.session_state[ACTIVE_CONVERSATION_KEY] = cid
    st.rerun()
  if delete_col.button(text.ANALYTICS_DELETE_CONVERSATION_BUTTON):
    convo.delete_conversation(corpus_root, conversation.conversation_id)
    st.session_state.pop(ACTIVE_CONVERSATION_KEY, None)
    st.rerun()

  with st.expander(text.ANALYTICS_RENAME_LABEL):
    new_title = st.text_input(
      text.ANALYTICS_RENAME_LABEL,
      value=conversation.title,
      key=f"rename-{conversation.conversation_id}",
      label_visibility="collapsed",
    )
    if st.button(text.ANALYTICS_RENAME_BUTTON) and new_title.strip():
      convo.rename_conversation(
        corpus_root, conversation.conversation_id, new_title.strip()
      )
      st.rerun()


def _conversation_context(
  corpus_root: Path,
  manifest: ManifestArtifact,
  has_gaps: bool,
  conversation: Conversation,
) -> GroundingContext:
  """
  Build the grounded context for one thread, scoped and carrying journal notes.

  A per-gap thread narrows to that gap's slice (issue #48); every thread also
  carries the researcher's kept journal notes, labelled as their own so grounding
  still binds the model to cite only Corpus Papers (issue #50).
  """
  base = _grounding_context(corpus_root, manifest, has_gaps)
  notes = render_notes_for_context(load_journal(corpus_root))
  note_list = [notes] if notes else []
  if conversation.gap_id:
    card = _scoped_gap_card(corpus_root, conversation.gap_id)
    if card is not None:
      return scope_context_to_gap(card, base).model_copy(update={"notes": note_list})
  return base.model_copy(update={"notes": note_list})


def _scoped_gap_card(corpus_root: Path, gap_id: str) -> GapCard | None:
  """Find the card a scoped chat is focused on, or None when it is gone."""
  if not has_candidate_gaps(corpus_root):
    return None
  data = build_gap_cards(
    load_candidate_gaps(corpus_root), load_judgments(corpus_root), corpus_root
  )
  return next((card for card in data.cards if card.gap_id == gap_id), None)


def _grounding_context(
  corpus_root: Path, manifest: ManifestArtifact, has_gaps: bool
) -> GroundingContext:
  """Map this Corpus's artifact read models onto the analytics grounding context."""
  papers = [
    CorpusPaper(
      citation_key=paper.citation_key,
      title=paper.title,
      year=paper.year,
      authors=paper.authors,
    )
    for paper in manifest.papers
  ]
  gap_facts: list[GapFact] = []
  if has_gaps:
    for gap in load_candidate_gaps(corpus_root).gaps:
      gap_facts.append(
        GapFact(
          gap_type=gap.gap_type,
          title=gap.title,
          explanation=gap.explanation,
          confidence=gap.confidence,
          source_citation_keys=gap.source_citation_keys,
        )
      )
  return GroundingContext(
    papers=papers, gaps=gap_facts, fact_pool=load_fact_pool(corpus_root)
  )


def _render_narrative_summary(
  chosen: CorpusChoice,
  manifest: ManifestArtifact,
  context: GroundingContext,
  client: LlmClient,
  has_gaps: bool,
) -> None:
  """Render the narrative summary over accepted gaps only, stating its denominator."""
  corpus_root = chosen.root
  st.subheader(text.ANALYTICS_SUMMARY_HEADING)
  st.caption(text.ANALYTICS_SUMMARY_INTRO)
  if not has_gaps:
    st.info(text.ANALYTICS_SUMMARY_NEEDS_GAPS)
    render_run_buttons(corpus_root, ["detect"], scope="analytics-summary")
    return
  scope = narrative_scope(
    build_gap_cards(load_candidate_gaps(corpus_root), load_judgments(corpus_root)),
    context,
  )
  if scope.accepted_count == 0:
    st.info(
      text.ANALYTICS_SUMMARY_NEEDS_ACCEPTED.format(undecided=scope.undecided_count)
    )
    return
  st.caption(
    text.ANALYTICS_SUMMARY_DENOMINATOR.format(
      accepted=scope.accepted_count,
      papers=scope.paper_count,
      rejected=scope.rejected_count,
      undecided=scope.undecided_count,
    )
  )
  signature = (str(corpus_root), _narrative_signature(scope))
  if st.button(text.ANALYTICS_GENERATE_SUMMARY_BUTTON):
    with st.spinner(text.ANALYTICS_THINKING):
      narrative = summarize_gaps(scope.context, client)
    st.session_state[NARRATIVE_KEY] = (signature, narrative)
  stored = st.session_state.get(NARRATIVE_KEY)
  if stored is None or stored[0] != signature:
    return
  narrative = stored[1]
  _render_narrative(narrative)
  _render_export(chosen, manifest, scope, narrative)


def _render_narrative(narrative: GroundedNarrative) -> None:
  """Render a generated narrative summary with its grounded citations."""
  for paragraph in narrative.paragraphs:
    st.write(paragraph)
  if narrative.citations:
    st.caption(
      text.ANALYTICS_CITATIONS_LABEL.format(papers=", ".join(narrative.citations))
    )
  else:
    st.caption(text.ANALYTICS_NO_CITATIONS)
  _render_grounding_flags(narrative.dropped_citations)


def _narrative_signature(scope: NarrativeScope) -> str:
  """
  Fingerprint what a narrative was generated over: accepted gaps, counts, Papers.

  A stored narrative is shown and exported only while this still matches, so a
  changed verdict never exports a write-up built from the old judgments.
  """
  return scope.model_dump_json(exclude={"context": {"fact_pool", "facts"}})


def _render_export(
  chosen: CorpusChoice,
  manifest: ManifestArtifact,
  scope: NarrativeScope,
  narrative: GroundedNarrative,
) -> None:
  """Offer the narrative, its cited Papers and the scope statement as files (#82)."""
  write_up = build_write_up(
    corpus_name=chosen.name,
    narrative=narrative,
    coverage=scope,
    manifest=manifest,
    scope=load_scope(chosen.root),
    entry_log=reconcile_entry_log(chosen.root, manifest),
  )
  st.markdown(f"**{text.EXPORT_HEADING}**")
  st.caption(text.EXPORT_INTRO)
  markdown_col, bibtex_col, scope_col = st.columns(3)
  markdown_col.download_button(
    text.EXPORT_MARKDOWN_BUTTON,
    write_up.markdown,
    file_name=f"{chosen.name}-gaps.md",
    mime="text/markdown",
    on_click="ignore",
  )
  bibtex_col.download_button(
    text.EXPORT_BIBTEX_BUTTON,
    write_up.bibtex,
    file_name=f"{chosen.name}-cited.bib",
    mime="application/x-bibtex",
    on_click="ignore",
  )
  scope_col.download_button(
    text.EXPORT_SCOPE_BUTTON,
    write_up.scope_statement,
    file_name=f"{chosen.name}-scope.md",
    mime="text/markdown",
    on_click="ignore",
  )


def _render_conversation(
  corpus_root: Path,
  conversation: Conversation,
  context: GroundingContext,
  client: LlmClient,
) -> None:
  """Render one persisted thread: prior turns, a fresh grounded answer, its save."""
  if conversation.gap_id:
    st.subheader(text.ANALYTICS_SCOPED_HEADING.format(title=conversation.title))
    st.info(text.ANALYTICS_SCOPED_INTRO)
  else:
    st.subheader(text.ANALYTICS_CHAT_HEADING)

  for index, turn in enumerate(conversation.turns):
    _render_conversation_turn(corpus_root, conversation, turn, index)

  question = st.chat_input(
    text.ANALYTICS_CHAT_INPUT_LABEL, key=f"chat-{conversation.conversation_id}"
  )
  if not question:
    return
  transcript = [f"{turn.role}: {turn.content}" for turn in conversation.turns]
  convo.append_turn(corpus_root, conversation.conversation_id, "user", question)
  with st.spinner(text.ANALYTICS_THINKING):
    answer = answer_question(question, context, client, history=transcript)
  convo.append_turn(
    corpus_root,
    conversation.conversation_id,
    "assistant",
    answer.answer,
    answer.citations,
  )
  _render_answer_flags(answer)
  st.rerun()


def _render_conversation_turn(
  corpus_root: Path, conversation: Conversation, turn: ChatTurn, index: int
) -> None:
  """Render a turn, offering to keep an answer in the journal (issues #48, #50)."""
  _render_chat_turn(turn)
  if turn.role != "assistant":
    return
  key = f"keep-{conversation.conversation_id}-{index}"
  if st.button(text.ANALYTICS_KEEP_BUTTON, key=key):
    promote_to_journal(
      corpus_root,
      note=turn.content,
      source_gap_id=conversation.gap_id,
      source_citation_keys=turn.citations,
    )
    st.toast(text.ANALYTICS_KEEP_CONFIRM)
    st.rerun()


def _render_chat_turn(turn: ChatTurn) -> None:
  """Render one persisted chat turn with the citations behind an answer."""
  with st.chat_message(turn.role):
    st.markdown(turn.content)
    if turn.role == "assistant":
      if turn.citations:
        st.caption(
          text.ANALYTICS_CITATIONS_LABEL.format(papers=", ".join(turn.citations))
        )
      else:
        st.caption(text.ANALYTICS_NO_CITATIONS)


def _render_journal(corpus_root: Path) -> None:
  """
  Render the curated research journal: kept notes with provenance (issue #50).

  Only snippets the researcher explicitly kept appear here; each shows the gap and
  Corpus Papers behind it, and the whole journal is injected into every thread as
  the researcher's own notes (still cite-only-Corpus grounded).
  """
  st.subheader(text.ANALYTICS_JOURNAL_HEADING)
  st.caption(text.ANALYTICS_JOURNAL_INTRO)
  journal = load_journal(corpus_root)
  if not journal.entries:
    st.info(text.ANALYTICS_JOURNAL_EMPTY)
    return
  for entry in journal.entries:
    with st.container(border=True):
      st.markdown(f"> {entry.note}")
      st.caption(
        text.journal_source_label(entry.source_gap_id, entry.source_citation_keys)
      )
      if st.button(
        text.ANALYTICS_JOURNAL_DELETE_BUTTON, key=f"journal-del-{entry.entry_id}"
      ):
        delete_entry(corpus_root, entry.entry_id)
        st.rerun()


def _render_answer_flags(answer: GroundedChatAnswer) -> None:
  """Surface any dropped, ungrounded citations before the chat reruns."""
  _render_grounding_flags(answer.dropped_citations)


def _render_grounding_flags(dropped: list[str]) -> None:
  """Warn when generation cited work that does not resolve to a Corpus Paper."""
  if dropped:
    st.warning(
      text.ANALYTICS_DROPPED_LABEL.format(count=len(dropped), papers=", ".join(dropped))
    )
