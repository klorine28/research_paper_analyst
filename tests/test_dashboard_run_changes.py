"""
Behavior of the "changed since last run" summary (issue #93).

Detect keeps the artifact it replaces; the summary compares the two and names
the gaps closed by new Papers, the new gaps, and the verdicts left orphaned.
Cell-gap cases run the real detect stage twice over a synthetic Corpus that
gains a Paper; limitation cases write the two artifacts by hand, since grouping
needs the LLM.
"""

import json
import subprocess
from pathlib import Path

import pytest
from conftest import StubLlmClient, open_page
from streamlit.testing.v1 import AppTest
from test_detect import _PLACEMENTS, Placements, _write_corpus

import research_gap_dashboard.dashboard as dashboard_pkg
from research_gap_dashboard.dashboard import pipeline_runner, text

from research_gap_dashboard.dashboard.artifacts import (
  CANDIDATE_GAPS_NAME,
  PREVIOUS_CANDIDATE_GAPS_NAME,
)
from research_gap_dashboard.dashboard.judgments import record_judgment
from research_gap_dashboard.dashboard.run_changes import build_run_changes
from research_gap_dashboard.detect import detect_corpus

# Two Papers combining Takotsubo with an RCT fill that cell; the second also
# brings a new design (cohort), opening cells nobody fills yet.
_ADDED: Placements = {
  "p07": [("topic", "takotsubo"), ("method", "rct")],
  "p08": [("topic", "takotsubo"), ("method", "rct"), ("method", "cohort")],
}


def _detect(root: Path) -> None:
  detect_corpus(root, StubLlmClient({}))


def test_no_summary_before_a_second_run(tmp_path: Path):
  """One run has nothing to compare against, so there is no summary at all."""
  root = _write_corpus(tmp_path, _PLACEMENTS)
  _detect(root)

  assert build_run_changes(root) is None


def test_a_re_run_with_nothing_new_changes_nothing(tmp_path: Path):
  """The same Corpus twice closes and opens no gap."""
  root = _write_corpus(tmp_path, _PLACEMENTS)
  _detect(root)
  _detect(root)

  changes = build_run_changes(root)

  assert changes is not None
  assert not changes.has_changes
  assert changes.new_citation_keys == []


def test_an_added_paper_closes_its_cell_and_opens_new_ones(tmp_path: Path):
  """Papers studying Takotsubo with an RCT close that gap and are named for it."""
  root = _write_corpus(tmp_path, _PLACEMENTS)
  _detect(root)
  _write_corpus(tmp_path, {**_PLACEMENTS, **_ADDED})
  _detect(root)

  changes = build_run_changes(root)

  assert changes is not None
  assert changes.new_citation_keys == ["p07", "p08"]
  closed = {gap.gap_id: gap for gap in changes.closed_gaps}
  assert closed["topicxmethod:takotsubo:rct"].closing_citation_keys == ["p07", "p08"]
  assert closed["topicxmethod:takotsubo:rct"].gap_type_label == "Coverage Gap"
  assert any(
    gap.gap_id == "topicxmethod:heart-failure:cohort" for gap in changes.new_gaps
  )
  assert (changes.previous_paper_count, changes.current_paper_count) == (6, 8)


def test_a_verdict_on_a_closed_gap_is_reported_orphaned(tmp_path: Path):
  """The researcher learns which of their verdicts no longer reach a card."""
  root = _write_corpus(tmp_path, _PLACEMENTS)
  _detect(root)
  record_judgment(root, "topicxmethod:takotsubo:rct", "accepted", title="RCT gap")
  _write_corpus(tmp_path, {**_PLACEMENTS, **_ADDED})
  _detect(root)

  changes = build_run_changes(root)

  assert changes is not None
  assert [(v.gap_id, v.title, v.decision) for v in changes.orphaned_verdicts] == [
    ("topicxmethod:takotsubo:rct", "RCT gap", "accepted")
  ]


def _limitation(gap_id: str, key: str, passage: str) -> dict:
  return {
    "gap_id": gap_id,
    "gap_type": "unanswered_limitation",
    "title": f"Limitation of {key}",
    "explanation": "",
    "confidence": "low",
    "confidence_reason": "",
    "corpus_paper_count": 2,
    "source_citation_keys": [key],
    "evidence": [
      {"citation_key": key, "statement": passage, "evidence": {"passage": passage}}
    ],
  }


def _group(group_id: str, key: str, passage: str, follow_ups: list[str]) -> dict:
  return {
    "group_id": group_id,
    "statements": [
      {"citation_key": key, "statement": passage, "evidence": {"passage": passage}}
    ],
    "follow_ups": [{"citation_key": c} for c in follow_ups],
  }


def _write_artifacts(root: Path, previous: dict, current: dict) -> None:
  artifacts = root / "artifacts"
  artifacts.mkdir(parents=True, exist_ok=True)
  (artifacts / PREVIOUS_CANDIDATE_GAPS_NAME).write_text(json.dumps(previous))
  (artifacts / CANDIDATE_GAPS_NAME).write_text(json.dumps(current))


def test_a_regrouped_limitation_is_neither_closed_nor_new(tmp_path: Path):
  """Only the group id changed, so the summary reports nothing."""
  base = {"corpus_root": str(tmp_path), "paper_citation_keys": ["a", "b"]}
  _write_artifacts(
    tmp_path,
    {
      **base,
      "gaps": [_limitation("limitation:limitation-group-01", "a", "one centre")],
    },
    {
      **base,
      "gaps": [_limitation("limitation:limitation-group-07", "a", "one centre")],
    },
  )

  changes = build_run_changes(tmp_path)

  assert changes is not None
  assert not changes.has_changes


def test_a_limitation_followed_up_by_a_new_paper_closes(tmp_path: Path):
  """The new Paper that followed the limitation up is named as closing it."""
  _write_artifacts(
    tmp_path,
    {
      "corpus_root": str(tmp_path),
      "paper_citation_keys": ["a", "b"],
      "gaps": [_limitation("limitation:limitation-group-01", "a", "one centre")],
    },
    {
      "corpus_root": str(tmp_path),
      "paper_citation_keys": ["a", "b", "c"],
      "limitation_groups": [_group("limitation-group-01", "a", "one centre", ["c"])],
      "gaps": [],
    },
  )

  changes = build_run_changes(tmp_path)

  assert changes is not None
  assert changes.new_citation_keys == ["c"]
  assert [(g.title, g.closing_citation_keys) for g in changes.closed_gaps] == [
    ("Limitation of a", ["c"])
  ]


def _two_runs(tmp_path: Path) -> Path:
  """Detect a Corpus, alone in its folder, before and after it gains Papers."""
  root = _write_corpus(tmp_path / "corpus", _PLACEMENTS)
  _detect(root)
  _write_corpus(root, {**_PLACEMENTS, **_ADDED})
  _detect(root)
  return root


def _app(root: Path, monkeypatch: pytest.MonkeyPatch) -> AppTest:
  monkeypatch.setenv("RESEARCH_GAP_CORPORA_DIR", str(root.parent))
  app_path = Path(dashboard_pkg.__file__).parent / "app.py"
  return AppTest.from_file(str(app_path), default_timeout=30).run()


def test_home_shows_what_the_last_run_changed(
  tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
  """The Home screen carries the summary, with its denominator."""
  root = _two_runs(tmp_path)

  app = _app(root, monkeypatch)

  assert not app.exception
  assert text.RUN_CHANGES_HEADING in [header.value for header in app.subheader]
  markdown = "\n".join(element.value for element in app.markdown)
  assert "over 8 Papers (6 before)" in markdown
  assert "now covered by new Paper(s) p07, p08" in markdown


def test_a_detect_run_from_the_app_is_followed_by_the_summary(
  tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
  """Running detect from the Pipeline page shows what that run changed."""
  root = _two_runs(tmp_path)
  monkeypatch.setattr(
    pipeline_runner.subprocess,
    "run",
    lambda args, **_: subprocess.CompletedProcess(args, 0, "", ""),
  )
  monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
  monkeypatch.setenv("LLM_COST_CONFIRM_ABOVE", "1000000")
  app = open_page(_app(root, monkeypatch), text.PAGE_PIPELINE)
  assert text.RUN_CHANGES_HEADING not in [h.value for h in app.subheader]

  app.button(key="run-detect").click().run()

  assert not app.exception
  assert text.RUN_CHANGES_HEADING in [header.value for header in app.subheader]
