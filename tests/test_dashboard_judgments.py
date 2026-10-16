"""
Behavior of the accept/reject persistence seam: judgments in, JSON out and back.

A Corpus is reviewed over months, so a judgment must outlive the session. These
tests exercise the read/write functions directly (the acceptance criterion),
including that a second read on a fresh path restores what the first write left.
"""

import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

from research_gap_dashboard.dashboard.artifacts import (
  CANDIDATE_GAPS_NAME,
  PREVIOUS_CANDIDATE_GAPS_NAME,
  load_candidate_gaps,
)
from research_gap_dashboard.dashboard.judgments import (
  JUDGMENTS_DIR,
  JUDGMENTS_NAME,
  SourceStatement,
  clear_judgment,
  gap_key,
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


# --- Limitation verdicts keyed by verbatim source statements (issue #93) -------

_FIXTURE = Path(__file__).parent / "fixtures" / "judgment-migration"
_BEFORE_VERDICTS = datetime(2025, 2, 1, tzinfo=timezone.utc).timestamp()
_AFTER_VERDICTS = datetime(2025, 4, 1, tzinfo=timezone.utc).timestamp()
_SINGLE_CENTRE = SourceStatement(
  citation_key="smith2020", passage="our cohort was drawn from a single centre"
)
_NO_FOLLOW_UP = SourceStatement(
  citation_key="jones2021", passage="patients were not followed beyond discharge"
)


def _legacy_corpus(tmp_path: Path) -> Path:
  """Copy the fixture, dating its artifact to before its verdicts were made."""
  root = tmp_path / "corpus"
  shutil.copytree(_FIXTURE, root)
  artifact = root / "artifacts" / CANDIDATE_GAPS_NAME
  os.utime(artifact, (_BEFORE_VERDICTS, _BEFORE_VERDICTS))
  return root


def _regroup(root: Path, groups: dict[str, list[str]], written_at: float) -> None:
  """
  Re-run detect by hand: keep the replaced artifact, write regrouped limitations.

  `groups` maps each new group id to the old group ids whose statements it now
  holds, which is how an LLM regrouping renumbers, merges or splits groups.
  """
  artifacts = root / "artifacts"
  current = artifacts / CANDIDATE_GAPS_NAME
  shutil.copy2(current, artifacts / PREVIOUS_CANDIDATE_GAPS_NAME)
  raw = json.loads(current.read_text(encoding="utf-8"))
  old = {gap["gap_id"]: gap for gap in raw["gaps"]}
  cells = [gap for gap in raw["gaps"] if gap["gap_type"] != "unanswered_limitation"]
  regrouped = [
    {
      **old[f"limitation:{members[0]}"],
      "gap_id": f"limitation:{group_id}",
      "evidence": [
        link for member in members for link in old[f"limitation:{member}"]["evidence"]
      ],
    }
    for group_id, members in groups.items()
  ]
  current.write_text(json.dumps({**raw, "gaps": cells + regrouped}), encoding="utf-8")
  os.utime(current, (written_at, written_at))


def _verdicts(root: Path) -> dict[str, str]:
  """Map each current card's gap id to the decision that applies to it."""
  log = load_judgments(root)
  verdicts: dict[str, str] = {}
  for record in load_candidate_gaps(root).gaps:
    verdict = log.verdict_for(gap_key(record))
    if verdict is not None:
      verdicts[record.gap_id] = verdict.decision
  return verdicts


def test_legacy_judgments_migrate_without_loss(tmp_path: Path):
  """Every old-keyed verdict survives, limitation ones gaining their statements."""
  root = _legacy_corpus(tmp_path)
  before = json.loads((root / JUDGMENTS_DIR / JUDGMENTS_NAME).read_text())

  log = load_judgments(root)

  assert [(j.gap_id, j.decision) for j in log.judgments] == [
    (j["gap_id"], j["decision"]) for j in before["judgments"]
  ]
  by_id = log.by_gap_id()
  assert by_id["limitation:limitation-group-01"].source_statements == [_SINGLE_CENTRE]
  assert by_id["limitation:limitation-group-02"].source_statements == [_NO_FOLLOW_UP]
  assert by_id["limitation:limitation-group-01"].title == "Small single-centre samples"
  assert by_id["topicxmethod:takotsubo:rct"].source_statements == []
  assert _verdicts(root) == {
    "topicxmethod:takotsubo:rct": "accepted",
    "limitation:limitation-group-01": "accepted",
    "limitation:limitation-group-02": "rejected",
  }


def test_the_migration_is_written_back_once(tmp_path: Path):
  """The migrated keys are persisted, so later reads need no artifact at all."""
  root = _legacy_corpus(tmp_path)
  load_judgments(root)
  (root / "artifacts" / CANDIDATE_GAPS_NAME).unlink()

  log = load_judgments(root)

  assert all(not judgment.is_legacy_limitation for judgment in log.judgments)


def test_regrouping_after_migration_keeps_every_verdict(tmp_path: Path):
  """Renumbered groups keep the verdicts of the statements they hold."""
  root = _legacy_corpus(tmp_path)
  load_judgments(root)

  _regroup(
    root,
    {
      "limitation-group-01": ["limitation-group-02"],
      "limitation-group-02": ["limitation-group-01"],
    },
    _AFTER_VERDICTS,
  )

  assert _verdicts(root) == {
    "topicxmethod:takotsubo:rct": "accepted",
    "limitation:limitation-group-01": "rejected",
    "limitation:limitation-group-02": "accepted",
  }
  assert (
    load_judgments(root).orphaned(
      gap_key(record) for record in load_candidate_gaps(root).gaps
    )
    == []
  )


def test_legacy_verdicts_migrate_against_the_artifact_they_were_made_on(
  tmp_path: Path,
):
  """A re-run before the migration still resolves old ids on the replaced artifact."""
  root = _legacy_corpus(tmp_path)

  _regroup(
    root,
    {
      "limitation-group-01": ["limitation-group-02"],
      "limitation-group-02": ["limitation-group-01"],
    },
    _AFTER_VERDICTS,
  )

  assert _verdicts(root) == {
    "topicxmethod:takotsubo:rct": "accepted",
    "limitation:limitation-group-01": "rejected",
    "limitation:limitation-group-02": "accepted",
  }


def test_a_merged_group_takes_its_latest_verdict(tmp_path: Path):
  """When two judged groups merge, the verdict set last applies to the merge."""
  root = _legacy_corpus(tmp_path)
  load_judgments(root)

  _regroup(
    root,
    {"limitation-group-01": ["limitation-group-01", "limitation-group-02"]},
    _AFTER_VERDICTS,
  )

  assert _verdicts(root)["limitation:limitation-group-01"] == "rejected"


def test_an_undatable_legacy_verdict_is_orphaned_not_guessed(tmp_path: Path):
  """A verdict older than every artifact on disk never lands on a reused group id."""
  root = _legacy_corpus(tmp_path)
  artifact = root / "artifacts" / CANDIDATE_GAPS_NAME
  os.utime(artifact, (_AFTER_VERDICTS, _AFTER_VERDICTS))

  log = load_judgments(root)

  assert "limitation:limitation-group-01" not in _verdicts(root)
  orphaned = log.orphaned(gap_key(record) for record in load_candidate_gaps(root).gaps)
  assert {judgment.gap_id for judgment in orphaned} == {
    "limitation:limitation-group-01",
    "limitation:limitation-group-02",
  }


def test_re_deciding_a_limitation_replaces_every_verdict_that_reached_it(
  tmp_path: Path,
):
  """Judging a merged group leaves one verdict, keyed by all its statements."""
  root = _legacy_corpus(tmp_path)
  load_judgments(root)
  _regroup(
    root,
    {"limitation-group-01": ["limitation-group-01", "limitation-group-02"]},
    _AFTER_VERDICTS,
  )
  merged = next(
    gap_key(record)
    for record in load_candidate_gaps(root).gaps
    if record.gap_id == "limitation:limitation-group-01"
  )

  record_judgment(root, merged, "accepted", title="Merged")

  limitation_verdicts = [
    judgment
    for judgment in load_judgments(root).judgments
    if judgment.source_statements
  ]
  assert len(limitation_verdicts) == 1
  assert set(limitation_verdicts[0].source_statements) == {
    _SINGLE_CENTRE,
    _NO_FOLLOW_UP,
  }
  assert limitation_verdicts[0].decision == "accepted"
