"""
Behaviour of the Conversational Analytics page: threads and the research journal.

These AppTest smoke tests drive the Streamlit shell with an offline stub client
(CODING_STANDARDS.md > Test with fixtures, not live services) and assert the
issue-#50 acceptance: conversations are listable, renamable, and deletable, and
only snippets the researcher explicitly keeps enter the journal, with their
provenance.
"""

from pathlib import Path

import pytest
from conftest import StubLlmClient
from test_detect import _PLACEMENTS, _write_corpus

import research_gap_dashboard.analytics as analytics_module
import research_gap_dashboard.dashboard as dashboard_pkg
from research_gap_dashboard.dashboard.conversations import list_conversations
from research_gap_dashboard.dashboard.journal import load_journal
from research_gap_dashboard.dashboard.text import (
  ANALYTICS_NEW_CONVERSATION_BUTTON,
  ANALYTICS_RENAME_BUTTON,
  PAGE_ANALYTICS,
)
from research_gap_dashboard.detect import detect_corpus


@pytest.fixture(name="corpus")
def corpus_fixture(tmp_path: Path) -> Path:
  """Write a synthetic Corpus (manifest + facts) and detect its Candidate Gaps."""
  root = _write_corpus(tmp_path / "corpus", _PLACEMENTS)
  detect_corpus(root, StubLlmClient({}))
  return root


def _run_analytics(corpus: Path, monkeypatch: pytest.MonkeyPatch, answer: str):
  """Run the shell on the Analytics page with a keyless stub answering the chat."""
  from streamlit.testing.v1 import AppTest

  monkeypatch.setattr(
    analytics_module,
    "build_analytics_client",
    lambda _cache_dir: StubLlmClient({"answer": answer, "citations": []}),
  )
  monkeypatch.setenv("RESEARCH_GAP_CORPORA_DIR", str(corpus.parent))
  app_path = Path(dashboard_pkg.__file__).parent / "app.py"
  app = AppTest.from_file(str(app_path), default_timeout=30).run()
  app.radio[0].set_value(PAGE_ANALYTICS).run()
  return app


def test_general_conversation_exists_by_default(
  corpus: Path, monkeypatch: pytest.MonkeyPatch
):
  """Opening Analytics seeds a general whole-Corpus thread with no prior clicks."""
  app = _run_analytics(corpus, monkeypatch, "Grounded.")

  assert not app.exception
  ids = [summary.conversation_id for summary in list_conversations(corpus)]
  assert "general" in ids


def test_a_kept_answer_enters_the_journal_with_provenance(
  corpus: Path, monkeypatch: pytest.MonkeyPatch
):
  """'Keep this' promotes an answer to the journal, carrying its Corpus Papers."""
  app = _run_analytics(corpus, monkeypatch, "No RCTs on Takotsubo.")
  app.chat_input[0].set_value("What is missing?").run()
  assert not app.exception

  keep = next(button for button in app.button if (button.key or "").startswith("keep-"))
  keep.click().run()

  assert not app.exception
  entries = load_journal(corpus).entries
  assert [entry.note for entry in entries] == ["No RCTs on Takotsubo."]


def test_new_rename_and_delete_a_conversation(
  corpus: Path, monkeypatch: pytest.MonkeyPatch
):
  """A researcher can start, rename, and delete a conversation thread."""
  app = _run_analytics(corpus, monkeypatch, "Grounded.")

  new_button = next(
    button for button in app.button if button.label == ANALYTICS_NEW_CONVERSATION_BUTTON
  )
  new_button.click().run()
  assert not app.exception
  before = {summary.conversation_id for summary in list_conversations(corpus)}
  assert len(before) >= 2

  rename_input = next(
    box for box in app.text_input if (box.key or "").startswith("rename-")
  )
  rename_input.set_value("Beta-blocker thread").run()
  rename_button = next(
    button for button in app.button if button.label == ANALYTICS_RENAME_BUTTON
  )
  rename_button.click().run()
  assert not app.exception
  titles = {summary.title for summary in list_conversations(corpus)}
  assert "Beta-blocker thread" in titles
