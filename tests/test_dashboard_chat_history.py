"""
Behavior of the chat-history persistence seam: turns in, JSON out and back.

A Corpus is discussed over months, so a conversation must outlive the session.
These tests exercise the read/write functions directly (the acceptance
criterion), including that a second read on a fresh path restores what an
earlier write left, and that a reloaded answer keeps the citations behind it.
"""

import json
from pathlib import Path

from research_gap_dashboard.dashboard.chat_history import (
  CHAT_HISTORY_NAME,
  append_turn,
  clear_chat_history,
  load_chat_history,
)
from research_gap_dashboard.dashboard.judgments import JUDGMENTS_DIR


def test_no_saved_history_reads_as_empty(tmp_path: Path):
  """A Corpus that was never discussed reads back with no turns, not an error."""
  history = load_chat_history(tmp_path)

  assert history.turns == []


def test_appending_a_turn_persists_it_under_judgments(tmp_path: Path):
  """Appending a turn writes a JSON file under judgments/ with its content."""
  append_turn(tmp_path, "user", "What did no Paper study?")

  path = tmp_path / JUDGMENTS_DIR / CHAT_HISTORY_NAME
  assert path.is_file()
  saved = json.loads(path.read_text(encoding="utf-8"))
  assert saved["turns"][0]["role"] == "user"
  assert saved["turns"][0]["content"] == "What did no Paper study?"


def test_history_survives_a_reload_with_its_citations(tmp_path: Path):
  """What one write leaves on disk, a later independent read restores in order."""
  append_turn(tmp_path, "user", "Any gaps in RCTs?")
  append_turn(tmp_path, "assistant", "No RCTs on Takotsubo.", ["hanna2019"])

  restored = load_chat_history(tmp_path).turns

  assert [turn.role for turn in restored] == ["user", "assistant"]
  assert restored[1].content == "No RCTs on Takotsubo."
  assert restored[1].citations == ["hanna2019"]


def test_clearing_history_returns_it_to_empty(tmp_path: Path):
  """Clearing a conversation removes every turn, so it reads back empty."""
  append_turn(tmp_path, "user", "First question")
  append_turn(tmp_path, "assistant", "First answer")

  clear_chat_history(tmp_path)

  assert load_chat_history(tmp_path).turns == []
