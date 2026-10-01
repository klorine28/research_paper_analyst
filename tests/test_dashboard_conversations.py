"""
Behaviour of the multi-conversation store: many conversations, JSON out and back.

A Corpus is discussed over months across several threads \u2014 a general one and
one per Candidate Gap \u2014 so each conversation must outlive the session, be
listable, renamable, and deletable. These tests exercise the read/write seam
directly (ADR 0002: the dashboard owns this schema and imports no pipeline
stage), including that a reload restores what an earlier write left and that the
answer's citations survive it.
"""

import json
from pathlib import Path

from research_gap_dashboard.dashboard.conversations import (
  CONVERSATIONS_NAME,
  GENERAL_CONVERSATION_ID,
  append_turn,
  conversation_id_for_gap,
  delete_conversation,
  get_or_create_conversation,
  list_conversations,
  load_conversation,
  rename_conversation,
)
from research_gap_dashboard.dashboard.judgments import JUDGMENTS_DIR


def test_no_store_lists_no_conversations(tmp_path: Path):
  """A Corpus that was never discussed lists no conversations, not an error."""
  assert list_conversations(tmp_path) == []


def test_creating_a_conversation_persists_it_under_judgments(tmp_path: Path):
  """Creating a conversation writes a JSON file under judgments/ with its title."""
  get_or_create_conversation(
    tmp_path, GENERAL_CONVERSATION_ID, title="General", gap_id=None
  )

  path = tmp_path / JUDGMENTS_DIR / CONVERSATIONS_NAME
  assert path.is_file()
  saved = json.loads(path.read_text(encoding="utf-8"))
  assert saved["conversations"][0]["title"] == "General"
  assert saved["conversations"][0]["conversation_id"] == GENERAL_CONVERSATION_ID


def test_get_or_create_is_idempotent(tmp_path: Path):
  """Asking twice for the same conversation keeps one, not a duplicate."""
  get_or_create_conversation(tmp_path, "gap-x", title="First", gap_id="x")
  get_or_create_conversation(tmp_path, "gap-x", title="Ignored", gap_id="x")

  conversations = list_conversations(tmp_path)
  assert [c.conversation_id for c in conversations] == ["gap-x"]
  assert conversations[0].title == "First"


def test_appending_turns_persists_them_with_citations(tmp_path: Path):
  """Turns append to the right conversation and reload with their citations."""
  cid = conversation_id_for_gap("takotsubo-rct")
  get_or_create_conversation(tmp_path, cid, title="RCTs", gap_id="takotsubo-rct")

  append_turn(tmp_path, cid, "user", "Any RCTs?")
  append_turn(tmp_path, cid, "assistant", "None on Takotsubo.", ["hanna2019"])

  restored = load_conversation(tmp_path, cid)
  assert restored is not None
  assert [turn.role for turn in restored.turns] == ["user", "assistant"]
  assert restored.turns[1].citations == ["hanna2019"]
  assert restored.gap_id == "takotsubo-rct"


def test_appending_to_an_unknown_conversation_creates_it(tmp_path: Path):
  """A turn for an id never created opens that conversation rather than failing."""
  append_turn(tmp_path, GENERAL_CONVERSATION_ID, "user", "Opening question")

  restored = load_conversation(tmp_path, GENERAL_CONVERSATION_ID)
  assert restored is not None
  assert restored.turns[0].content == "Opening question"


def test_renaming_a_conversation_changes_its_title(tmp_path: Path):
  """Renaming changes the stored title and nothing else."""
  get_or_create_conversation(tmp_path, "gap-x", title="Old", gap_id="x")

  rename_conversation(tmp_path, "gap-x", "New name")

  renamed = load_conversation(tmp_path, "gap-x")
  assert renamed is not None
  assert renamed.title == "New name"


def test_deleting_a_conversation_removes_only_it(tmp_path: Path):
  """Deleting one conversation leaves the others intact."""
  get_or_create_conversation(tmp_path, "a", title="A", gap_id=None)
  get_or_create_conversation(tmp_path, "b", title="B", gap_id=None)

  delete_conversation(tmp_path, "a")

  remaining = [c.conversation_id for c in list_conversations(tmp_path)]
  assert remaining == ["b"]
  assert load_conversation(tmp_path, "a") is None


def test_conversations_list_most_recently_updated_first(tmp_path: Path):
  """The list orders conversations by last activity so the live thread is on top."""
  get_or_create_conversation(tmp_path, "a", title="A", gap_id=None)
  get_or_create_conversation(tmp_path, "b", title="B", gap_id=None)
  append_turn(tmp_path, "a", "user", "later activity on A")

  ordered = [c.conversation_id for c in list_conversations(tmp_path)]
  assert ordered == ["a", "b"]
