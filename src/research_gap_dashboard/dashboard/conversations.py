"""
Persisting many Conversational Analytics threads for one Corpus.

A Corpus is analysed once but discussed over months across several threads: a
general conversation and one scoped to each Candidate Gap (issue #50). This
module generalises the single-history seam (`chat_history`) into a store of named
conversations keyed by a stable id, each listable, renamable, and deletable, so a
restored session shows the same threads a researcher left behind.

Like the judgments seam it is plain functions with no Streamlit dependency, so
the layout layer stays thin and persistence is tested directly. Every thread
lives in one JSON file under the Corpus's `judgments/` directory; the dashboard
owns this schema and imports no pipeline stage (ADR 0002). Each answer keeps the
citation keys behind it so a reloaded conversation still shows what every claim
was grounded in (CODING_STANDARDS.md > Research integrity).
"""

from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel

from research_gap_dashboard.dashboard.chat_history import ChatTurn, Role
from research_gap_dashboard.dashboard.judgments import JUDGMENTS_DIR

CONVERSATIONS_NAME = "conversations.json"

# The id of the one whole-Corpus thread; per-gap threads are keyed off the gap id.
GENERAL_CONVERSATION_ID = "general"

# The prefix that marks a thread as scoped to one Candidate Gap, so a gap id with
# any characters maps to a single stable, collision-free conversation id.
_GAP_PREFIX = "gap:"


def conversation_id_for_gap(gap_id: str) -> str:
  """Return the stable conversation id for the thread scoped to one Candidate Gap."""
  return f"{_GAP_PREFIX}{gap_id}"


def new_conversation_id() -> str:
  """Mint a fresh id for a researcher-started general thread."""
  return f"thread:{uuid4().hex}"


class Conversation(BaseModel):
  """One analytics thread: its identity, title, scope, and turns, oldest first."""

  conversation_id: str
  title: str
  gap_id: str | None = None
  turns: list[ChatTurn] = []
  created_at: datetime
  updated_at: datetime


class ConversationSummary(BaseModel):
  """A conversation without its turns, for listing threads cheaply."""

  conversation_id: str
  title: str
  gap_id: str | None
  turn_count: int
  updated_at: datetime


class ConversationStore(BaseModel):
  """Every conversation recorded for one Corpus, on disk as JSON."""

  conversations: list[Conversation] = []

  def by_id(self) -> dict[str, Conversation]:
    """Index the conversations by their id, latest write winning."""
    return {
      conversation.conversation_id: conversation for conversation in self.conversations
    }


def _store_path(corpus_root: Path) -> Path:
  """Return where a corpus directory keeps its analytics conversations."""
  return corpus_root / JUDGMENTS_DIR / CONVERSATIONS_NAME


def _load_store(corpus_root: Path) -> ConversationStore:
  """Read a Corpus's conversation store, empty when none was saved."""
  path = _store_path(corpus_root)
  if not path.is_file():
    return ConversationStore()
  return ConversationStore.model_validate_json(path.read_text(encoding="utf-8"))


def _write_store(corpus_root: Path, store: ConversationStore) -> None:
  """Write the store, newest-updated first so the live thread sits on top."""
  path = _store_path(corpus_root)
  path.parent.mkdir(parents=True, exist_ok=True)
  ordered = sorted(
    store.conversations, key=lambda conversation: conversation.updated_at, reverse=True
  )
  path.write_text(
    ConversationStore(conversations=ordered).model_dump_json(indent=2),
    encoding="utf-8",
  )


def list_conversations(corpus_root: Path) -> list[ConversationSummary]:
  """List a Corpus's conversations without their turns, newest activity first."""
  store = _load_store(corpus_root)
  ordered = sorted(
    store.conversations, key=lambda conversation: conversation.updated_at, reverse=True
  )
  return [
    ConversationSummary(
      conversation_id=conversation.conversation_id,
      title=conversation.title,
      gap_id=conversation.gap_id,
      turn_count=len(conversation.turns),
      updated_at=conversation.updated_at,
    )
    for conversation in ordered
  ]


def load_conversation(corpus_root: Path, conversation_id: str) -> Conversation | None:
  """Read one conversation by id, or None when no such thread was saved."""
  return _load_store(corpus_root).by_id().get(conversation_id)


def get_or_create_conversation(
  corpus_root: Path,
  conversation_id: str,
  *,
  title: str,
  gap_id: str | None,
) -> Conversation:
  """Return the conversation for an id, creating an empty one when it is new."""
  store = _load_store(corpus_root)
  existing = store.by_id().get(conversation_id)
  if existing is not None:
    return existing
  now = datetime.now(timezone.utc)
  conversation = Conversation(
    conversation_id=conversation_id,
    title=title,
    gap_id=gap_id,
    created_at=now,
    updated_at=now,
  )
  _write_store(
    corpus_root, ConversationStore(conversations=[*store.conversations, conversation])
  )
  return conversation


def append_turn(
  corpus_root: Path,
  conversation_id: str,
  role: Role,
  content: str,
  citations: list[str] | None = None,
) -> ChatTurn:
  """Append one turn to a conversation, opening it when the id is new."""
  store = _load_store(corpus_root)
  by_id = store.by_id()
  now = datetime.now(timezone.utc)
  turn = ChatTurn(role=role, content=content, citations=list(citations or []), at=now)
  conversation = by_id.get(conversation_id)
  if conversation is None:
    conversation = Conversation(
      conversation_id=conversation_id,
      title=_default_title(conversation_id),
      gap_id=None,
      created_at=now,
      updated_at=now,
    )
  conversation = conversation.model_copy(
    update={"turns": [*conversation.turns, turn], "updated_at": now}
  )
  by_id[conversation_id] = conversation
  _write_store(corpus_root, ConversationStore(conversations=list(by_id.values())))
  return turn


def rename_conversation(corpus_root: Path, conversation_id: str, title: str) -> None:
  """Change a conversation's title, leaving its turns untouched."""
  store = _load_store(corpus_root)
  by_id = store.by_id()
  conversation = by_id.get(conversation_id)
  if conversation is None:
    return
  by_id[conversation_id] = conversation.model_copy(update={"title": title})
  _write_store(corpus_root, ConversationStore(conversations=list(by_id.values())))


def delete_conversation(corpus_root: Path, conversation_id: str) -> None:
  """Remove one conversation, leaving the other threads intact."""
  store = _load_store(corpus_root)
  kept = [c for c in store.conversations if c.conversation_id != conversation_id]
  if len(kept) != len(store.conversations):
    _write_store(corpus_root, ConversationStore(conversations=kept))


def _default_title(conversation_id: str) -> str:
  """Return a placeholder title for a thread opened by a turn before it was named."""
  if conversation_id == GENERAL_CONVERSATION_ID:
    return "General"
  return conversation_id.removeprefix(_GAP_PREFIX)
