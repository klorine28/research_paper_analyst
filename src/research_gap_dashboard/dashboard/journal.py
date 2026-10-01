"""
The curated research journal: a deliberately narrow cross-conversation channel.

Conversations are grounded in one Corpus each. A file that carries content
between them is a side channel that could launder un-grounded claims, so the
journal (issue #50) is built around one integrity posture, not convenience:

1. Nothing enters without an explicit promotion by the researcher ("keep this");
   raw model output is never auto-appended.
2. Every entry records its provenance \u2014 the Candidate Gap it came from and the
   Corpus Papers behind it.
3. When injected into a later conversation it is labelled as the researcher's own
   note, and the answer it informs is still bound by cite-only-Corpus grounding,
   which drops invented citations (CODING_STANDARDS.md > Research integrity).

Like the other dashboard seams it is plain functions with no Streamlit
dependency, persisting one JSON file under the Corpus's `judgments/` directory;
the dashboard owns this schema and imports no pipeline stage (ADR 0002).
"""

from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel

from research_gap_dashboard.dashboard.judgments import JUDGMENTS_DIR

JOURNAL_NAME = "research_journal.json"


class JournalEntry(BaseModel):
  """One snippet the researcher chose to keep, with where it came from."""

  entry_id: str
  note: str
  source_gap_id: str | None = None
  source_citation_keys: list[str] = []
  kept_at: datetime


class ResearchJournal(BaseModel):
  """Every snippet kept for one Corpus, on disk as JSON, oldest first."""

  entries: list[JournalEntry] = []


def _journal_path(corpus_root: Path) -> Path:
  """Return where a corpus directory keeps its research journal."""
  return corpus_root / JUDGMENTS_DIR / JOURNAL_NAME


def load_journal(corpus_root: Path) -> ResearchJournal:
  """Read a Corpus's journal, returning an empty one when nothing was kept."""
  path = _journal_path(corpus_root)
  if not path.is_file():
    return ResearchJournal()
  return ResearchJournal.model_validate_json(path.read_text(encoding="utf-8"))


def _write_journal(corpus_root: Path, journal: ResearchJournal) -> None:
  """Write the journal, creating the judgments directory on first write."""
  path = _journal_path(corpus_root)
  path.parent.mkdir(parents=True, exist_ok=True)
  path.write_text(journal.model_dump_json(indent=2), encoding="utf-8")


def promote_to_journal(
  corpus_root: Path,
  *,
  note: str,
  source_gap_id: str | None,
  source_citation_keys: list[str],
) -> JournalEntry:
  """
  Keep one snippet in the journal, recording its provenance.

  Only an explicit promotion of real content reaches the journal: a blank note is
  rejected rather than silently stored, so the channel never fills with nothing.
  """
  text = note.strip()
  if not text:
    raise ValueError("A journal note must have content to keep.")
  entry = JournalEntry(
    entry_id=uuid4().hex,
    note=text,
    source_gap_id=source_gap_id,
    source_citation_keys=list(source_citation_keys),
    kept_at=datetime.now(timezone.utc),
  )
  journal = load_journal(corpus_root)
  _write_journal(corpus_root, ResearchJournal(entries=[*journal.entries, entry]))
  return entry


def delete_entry(corpus_root: Path, entry_id: str) -> None:
  """Remove one kept snippet, leaving the rest of the journal intact."""
  journal = load_journal(corpus_root)
  kept = [entry for entry in journal.entries if entry.entry_id != entry_id]
  if len(kept) != len(journal.entries):
    _write_journal(corpus_root, ResearchJournal(entries=kept))


def render_notes_for_context(journal: ResearchJournal) -> str:
  """
  Render the kept notes for injection, labelled as the researcher's own.

  The label is the integrity boundary: the model must treat these as the
  researcher's notes, not as Corpus text, and must still cite only Corpus Papers
  (invented citations are dropped downstream). An empty journal renders to the
  empty string so nothing \u2014 not even the label \u2014 reaches the prompt.
  """
  if not journal.entries:
    return ""
  lines = [
    "RESEARCHER NOTES (the researcher's own saved notes, not Corpus text; "
    "treat them as the researcher's and still cite only Corpus Papers):"
  ]
  for entry in journal.entries:
    sources = ", ".join(entry.source_citation_keys)
    suffix = f" (from {sources})" if sources else ""
    lines.append(f"- {entry.note}{suffix}")
  return "\n".join(lines)
