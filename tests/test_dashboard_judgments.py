"""
Behavior of the accept/reject persistence seam: judgments in, JSON out and back.

A Corpus is reviewed over months, so a judgment must outlive the session. These
tests exercise the read/write functions directly (the acceptance criterion),
including that a second read on a fresh path restores what the first write left.
"""

import json
from pathlib import Path

from research_gap_dashboard.dashboard.judgments import (
  JUDGMENTS_DIR,
  JUDGMENTS_NAME,
  clear_judgment,
  load_judgments,
  record_judgment,
)


def _judgments_dir(corpus_root: Path) -> Path:
  """Create and return the corpus directory's judgments folder."""
  path = corpus_root / JUDGMENTS_DIR
  path.mkdir(parents=True, exist_ok=True)
  return path


def test_no_saved_judgments_reads_as_an_empty_log(tmp_path: Path):
  """A Corpus that was never judged reads back with no judgments, not an error."""
  _judgments_dir(tmp_path)

  log = load_judgments(tmp_path)

  assert log.judgments == []
  assert log.by_gap_id() == {}


def test_recording_a_judgment_persists_it_to_json(tmp_path: Path):
  """Accepting a gap writes a JSON file under judgments/ with the decision."""
  _judgments_dir(tmp_path)

  record_judgment(tmp_path, "topicxmethod:takotsubo:rct", "accepted")

  path = tmp_path / JUDGMENTS_DIR / JUDGMENTS_NAME
  assert path.is_file()
  saved = json.loads(path.read_text(encoding="utf-8"))
  assert saved["judgments"][0]["gap_id"] == "topicxmethod:takotsubo:rct"
  assert saved["judgments"][0]["decision"] == "accepted"


def test_a_judgment_survives_a_reload(tmp_path: Path):
  """What one write leaves on disk, a later independent read restores."""
  _judgments_dir(tmp_path)
  record_judgment(tmp_path, "gap-a", "accepted")
  record_judgment(tmp_path, "gap-b", "rejected")

  restored = load_judgments(tmp_path).by_gap_id()

  assert restored["gap-a"].decision == "accepted"
  assert restored["gap-b"].decision == "rejected"


def test_re_deciding_a_gap_overwrites_its_verdict(tmp_path: Path):
  """A researcher who changes their mind leaves one judgment, the latest."""
  _judgments_dir(tmp_path)
  record_judgment(tmp_path, "gap-a", "accepted")

  record_judgment(tmp_path, "gap-a", "rejected")

  log = load_judgments(tmp_path)
  assert len(log.judgments) == 1
  assert log.by_gap_id()["gap-a"].decision == "rejected"


def test_clearing_a_judgment_returns_the_gap_to_undecided(tmp_path: Path):
  """Undoing a verdict removes the entry, so the gap reads as undecided again."""
  _judgments_dir(tmp_path)
  record_judgment(tmp_path, "gap-a", "accepted")
  record_judgment(tmp_path, "gap-b", "accepted")

  clear_judgment(tmp_path, "gap-a")

  restored = load_judgments(tmp_path).by_gap_id()
  assert "gap-a" not in restored
  assert restored["gap-b"].decision == "accepted"


def test_clearing_an_unjudged_gap_is_a_no_op(tmp_path: Path):
  """Clearing a gap that was never judged neither errors nor writes a file."""
  _judgments_dir(tmp_path)

  clear_judgment(tmp_path, "never-judged")

  assert not (tmp_path / JUDGMENTS_DIR / JUDGMENTS_NAME).exists()


def test_write_creates_the_judgments_directory(tmp_path: Path):
  """Recording works even before the judgments/ folder exists on disk."""
  record_judgment(tmp_path, "gap-a", "accepted")

  assert (tmp_path / JUDGMENTS_DIR / JUDGMENTS_NAME).is_file()
  assert load_judgments(tmp_path).by_gap_id()["gap-a"].decision == "accepted"
