"""
Behaviour of the guided flow read model (issue #76).

The guided path is Scope → Build → Trust → Explore → Judge → Grow → Write. Which
steps are complete, and which step the home screen recommends next, come only
from what is on disk: the scope record, the pipeline status (existence + mtime,
ADR 0007), the fallout the attention report finds, and the researcher's gap
verdicts. These tests write that state directly; nothing runs the pipeline.
"""

import json
import os
from pathlib import Path

import pytest

from research_gap_dashboard.dashboard.corpus_scope import save_scope
from research_gap_dashboard.dashboard.guided_flow import Step, build_flow_progress
from research_gap_dashboard.dashboard.judgments import record_judgment

_T0 = 1_700_000_000
_KEYS = ("a", "b")
_GAP_IDS = ("gap-1", "gap-2")


def _touch(path: Path, mtime: int, content: object = None) -> None:
  path.parent.mkdir(parents=True, exist_ok=True)
  path.write_text(json.dumps(content if content is not None else {}), encoding="utf-8")
  os.utime(path, (mtime, mtime))


def _gap(gap_id: str) -> dict[str, object]:
  return {
    "gap_id": gap_id,
    "gap_type": "coverage",
    "title": f"Gap {gap_id}",
    "explanation": "No Paper combines these.",
    "confidence": "low",
    "confidence_reason": "Empty cell.",
    "corpus_paper_count": 2,
  }


def _ingest(root: Path) -> None:
  """Write the Corpus inputs and a manifest written after them."""
  _touch(root / "corpus.bib", _T0)
  for key in _KEYS:
    _touch(root / "papers" / f"{key}.pdf", _T0)
  _touch(
    root / "artifacts" / "corpus-manifest.json",
    _T0 + 10,
    {
      "corpus_root": str(root),
      "papers": [{"citation_key": key, "title": f"Title {key}"} for key in _KEYS],
    },
  )


def _build(root: Path, *, unparsed: tuple[str, ...] = ()) -> None:
  """Write every core stage's artifact in pipeline order, so all are done."""
  _ingest(root)
  parsed = [key for key in _KEYS if key not in unparsed]
  _touch(
    root / "artifacts" / "parse-report.json",
    _T0 + 20,
    {
      "corpus_root": str(root),
      "parsed_paths": [],
      "failures": [
        {"citation_key": key, "pdf_path": f"{key}.pdf", "error": "no usable text"}
        for key in unparsed
      ],
    },
  )
  for key in parsed:
    _touch(root / "paper-data" / f"{key}.parsed.json", _T0 + 20)
  _touch(
    root / "artifacts" / "extractions.json",
    _T0 + 30,
    {
      "corpus_root": str(root),
      "extractions": [{"citation_key": key} for key in parsed],
      "failures": [],
      "unverified": [],
    },
  )
  _touch(
    root / "artifacts" / "normalized_facts.json",
    _T0 + 40,
    {"corpus_root": str(root), "normalized": []},
  )
  _touch(
    root / "artifacts" / "candidate_gaps.json",
    _T0 + 50,
    {"corpus_root": str(root), "gaps": [_gap(gap_id) for gap_id in _GAP_IDS]},
  )


def _scope(root: Path) -> None:
  save_scope(root, question="What is missing?", field="Cardiology", searched="PubMed")


@pytest.fixture(name="built")
def built_fixture(tmp_path: Path) -> Path:
  """Write a scoped Corpus whose every core stage ran cleanly, with no verdicts."""
  root = tmp_path / "corpus"
  _build(root)
  _scope(root)
  return root


def test_a_corpus_without_a_scope_is_sent_to_scope(tmp_path: Path):
  """With no scope recorded, the first thing to do is state it."""
  root = tmp_path / "corpus"
  _build(root)

  progress = build_flow_progress(root)

  assert progress.recommended == Step.SCOPE
  assert Step.SCOPE not in progress.completed


def test_an_ingested_corpus_is_sent_to_build_naming_the_next_stage(tmp_path: Path):
  """Only ingest has run: the recommendation is Build, naming the stage to run."""
  root = tmp_path / "corpus"
  _ingest(root)
  _scope(root)

  progress = build_flow_progress(root)

  assert progress.recommended == Step.BUILD
  assert "parse" in progress.reason
  assert progress.completed == [Step.SCOPE]


def test_a_stale_stage_sends_the_researcher_back_to_build(built: Path):
  """An input newer than its artifact makes Build the next step again."""
  _touch(built / "artifacts" / "extractions.json", _T0 + 100, {"corpus_root": "x"})

  progress = build_flow_progress(built)

  assert progress.recommended == Step.BUILD
  assert "aggregate" in progress.reason
  assert Step.BUILD not in progress.completed


def test_fallout_sends_the_researcher_to_trust_with_the_denominator(tmp_path: Path):
  """A Paper that fell out makes Trust the next step, stating how many reached."""
  root = tmp_path / "corpus"
  _build(root, unparsed=("b",))
  _scope(root)

  progress = build_flow_progress(root)

  assert progress.recommended == Step.TRUST
  assert "1 of 2" in progress.reason
  assert Step.TRUST not in progress.completed
  assert Step.BUILD in progress.completed


def test_a_clean_build_with_no_verdicts_recommends_explore(built: Path):
  """Nothing fell out and no gap is judged yet: look at the map first."""
  progress = build_flow_progress(built)

  assert progress.recommended == Step.EXPLORE
  assert progress.completed == [Step.SCOPE, Step.BUILD, Step.TRUST]


def test_undecided_gaps_recommend_judge_with_the_count(built: Path):
  """Once judging starts, the undecided gaps are what is left to do."""
  record_judgment(built, "gap-1", "accepted")

  progress = build_flow_progress(built)

  assert progress.recommended == Step.JUDGE
  assert "1 of 2" in progress.reason
  assert Step.EXPLORE in progress.completed
  assert Step.JUDGE not in progress.completed


def test_every_gap_decided_recommends_write(built: Path):
  """With every Candidate Gap judged, the write-up is next; Grow stays optional."""
  record_judgment(built, "gap-1", "accepted")
  record_judgment(built, "gap-2", "rejected")

  progress = build_flow_progress(built)

  assert progress.recommended == Step.WRITE
  assert progress.completed == [
    Step.SCOPE,
    Step.BUILD,
    Step.TRUST,
    Step.EXPLORE,
    Step.JUDGE,
  ]


def test_grow_is_complete_once_retrieval_ran(built: Path):
  """Grow is marked complete when the retrieve stage's artifact is current."""
  _touch(built / "artifacts" / "retrieval_gaps.json", _T0 + 60)

  assert Step.GROW in build_flow_progress(built).completed


def test_health_states_the_denominator_and_the_verdict_counts(tmp_path: Path):
  """The home screen's Corpus health counts Papers, gaps and verdicts."""
  root = tmp_path / "corpus"
  _build(root, unparsed=("b",))
  _scope(root)
  record_judgment(root, "gap-1", "accepted")

  health = build_flow_progress(root).health

  assert (health.total_papers, health.reached_papers) == (2, 1)
  assert (health.gap_count, health.accepted, health.rejected, health.undecided) == (
    2,
    1,
    0,
    1,
  )
  assert (health.stages_done, health.stages_total) == (5, 7)
