"""
Persisting the Conversational Analytics chat history for one Corpus.

A Corpus is analysed once but discussed over months, so a chat exchange must
outlive the Streamlit session and any restart. This module is the read/write
seam for that history; like the judgments seam it is plain functions with no
Streamlit dependency, so persistence can be tested directly.

The history lives in one JSON file under the Corpus's `judgments/` directory
(alongside the accept/reject judgments), a turn per user question and grounded
answer. Each answer keeps the citation keys behind it so a reloaded conversation
still shows what every claim was grounded in (CODING_STANDARDS.md > Research
integrity). The dashboard owns this schema and imports no pipeline stage
(ADR 0002).
"""

from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from research_gap_dashboard.dashboard.judgments import JUDGMENTS_DIR

CHAT_HISTORY_NAME = "chat_history.json"

Role = Literal["user", "assistant"]


class ChatTurn(BaseModel):
  """One turn of the conversation: a question asked or a grounded answer given."""

  role: Role
  content: str
  citations: list[str] = []
  at: datetime


class ChatHistory(BaseModel):
  """The whole conversation for one Corpus, on disk as JSON, oldest turn first."""

  turns: list[ChatTurn] = []


def _chat_history_path(corpus_root: Path) -> Path:
  """Return where a corpus directory keeps its Conversational Analytics history."""
  return corpus_root / JUDGMENTS_DIR / CHAT_HISTORY_NAME


def load_chat_history(corpus_root: Path) -> ChatHistory:
  """Read a Corpus's chat history, returning an empty one when none was saved."""
  path = _chat_history_path(corpus_root)
  if not path.is_file():
    return ChatHistory()
  return ChatHistory.model_validate_json(path.read_text(encoding="utf-8"))


def _write_history(corpus_root: Path, history: ChatHistory) -> None:
  """Write the chat history, creating the judgments directory on first write."""
  path = _chat_history_path(corpus_root)
  path.parent.mkdir(parents=True, exist_ok=True)
  path.write_text(history.model_dump_json(indent=2), encoding="utf-8")


def append_turn(
  corpus_root: Path,
  role: Role,
  content: str,
  citations: list[str] | None = None,
) -> ChatTurn:
  """Append one turn to the Corpus's chat history and persist it."""
  turn = ChatTurn(
    role=role,
    content=content,
    citations=list(citations or []),
    at=datetime.now(timezone.utc),
  )
  history = load_chat_history(corpus_root)
  _write_history(corpus_root, ChatHistory(turns=[*history.turns, turn]))
  return turn


def clear_chat_history(corpus_root: Path) -> None:
  """Erase a Corpus's conversation, returning it to an empty history."""
  history = load_chat_history(corpus_root)
  if history.turns:
    _write_history(corpus_root, ChatHistory())
