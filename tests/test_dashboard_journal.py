"""
Behaviour of the curated research journal: explicit promotion, provenance, reuse.

The journal is the only side channel that carries content between conversations,
so it is deliberately narrow (issue #50). These tests pin the integrity posture:
nothing enters without an explicit promotion, every entry records where it came
from, and when rendered for reuse it is labelled as the researcher's own note so
grounding still binds the model to cite only Corpus Papers (CODING_STANDARDS.md >
Research integrity).
"""

import json
from pathlib import Path

import pytest

from research_gap_dashboard.dashboard.journal import (
  JOURNAL_NAME,
  delete_entry,
  load_journal,
  promote_to_journal,
  render_notes_for_context,
)
from research_gap_dashboard.dashboard.judgments import JUDGMENTS_DIR


def test_no_journal_reads_as_empty(tmp_path: Path):
  """A Corpus with nothing kept reads back an empty journal, not an error."""
  assert load_journal(tmp_path).entries == []


def test_promoting_a_snippet_records_its_provenance(tmp_path: Path):
  """A kept snippet stores its text and the gap + Papers it came from."""
  entry = promote_to_journal(
    tmp_path,
    note="No RCT has tested beta-blockers in Takotsubo.",
    source_gap_id="takotsubo-rct",
    source_citation_keys=["hanna2019", "templin2015"],
  )

  path = tmp_path / JUDGMENTS_DIR / JOURNAL_NAME
  assert path.is_file()
  saved = json.loads(path.read_text(encoding="utf-8"))
  assert saved["entries"][0]["note"] == entry.note
  assert saved["entries"][0]["source_gap_id"] == "takotsubo-rct"
  assert saved["entries"][0]["source_citation_keys"] == ["hanna2019", "templin2015"]


def test_promoting_an_empty_note_is_rejected(tmp_path: Path):
  """Only real content is kept; a blank promotion never silently stores nothing."""
  with pytest.raises(ValueError):
    promote_to_journal(
      tmp_path, note="   ", source_gap_id=None, source_citation_keys=[]
    )


def test_entries_survive_a_reload(tmp_path: Path):
  """What one write leaves on disk, a later independent read restores."""
  promote_to_journal(
    tmp_path, note="First note", source_gap_id=None, source_citation_keys=[]
  )
  promote_to_journal(
    tmp_path, note="Second note", source_gap_id="g", source_citation_keys=["a"]
  )

  notes = [entry.note for entry in load_journal(tmp_path).entries]
  assert notes == ["First note", "Second note"]


def test_deleting_an_entry_removes_only_it(tmp_path: Path):
  """Deleting one kept note leaves the others intact."""
  first = promote_to_journal(
    tmp_path, note="Keep", source_gap_id=None, source_citation_keys=[]
  )
  promote_to_journal(tmp_path, note="Drop", source_gap_id=None, source_citation_keys=[])

  delete_entry(tmp_path, first.entry_id)

  assert [e.note for e in load_journal(tmp_path).entries] == ["Drop"]


def test_rendered_notes_are_labelled_as_the_researchers_own(tmp_path: Path):
  """Injected notes read as the researcher's note, not as Corpus text."""
  promote_to_journal(
    tmp_path,
    note="Beta-blocker trials look like the gap.",
    source_gap_id="takotsubo-rct",
    source_citation_keys=["hanna2019"],
  )

  rendered = render_notes_for_context(load_journal(tmp_path))

  assert "researcher" in rendered.lower()
  assert "Beta-blocker trials look like the gap." in rendered


def test_rendered_notes_are_empty_string_when_journal_is_empty(tmp_path: Path):
  """An empty journal injects nothing, so no stray label reaches the prompt."""
  assert render_notes_for_context(load_journal(tmp_path)) == ""
