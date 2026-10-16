"""
Behaviour of the soft Trust gate read model (issue #77).

Explore, Judge, Grow and Write stay locked until the Corpus is complete or the
researcher acknowledges the denominator. The acknowledgement is recorded in
`judgments/`, keyed to the Corpus state it acknowledged, so a changed state asks
again. These tests write that state directly; nothing runs the pipeline.
"""

import json
from pathlib import Path

from test_dashboard_guided_flow import _build, _scope, _touch

from research_gap_dashboard.dashboard.guided_flow import (
  GATED_STEPS,
  Step,
  build_flow_progress,
)
from research_gap_dashboard.dashboard.trust_gate import (
  acknowledge_denominator,
  build_trust_gate,
)


def _fallout(tmp_path: Path) -> Path:
  root = tmp_path / "corpus"
  _build(root, unparsed=("b",))
  _scope(root)
  return root


def test_the_gate_guards_the_steps_after_trust():
  """Scope, Build and Trust stay open: they are how fallout gets fixed."""
  assert GATED_STEPS == (Step.EXPLORE, Step.JUDGE, Step.GROW, Step.WRITE)


def test_a_complete_corpus_passes_without_an_acknowledgement(tmp_path: Path):
  """Every Paper reached the artifact with all its facts: nothing to acknowledge."""
  root = tmp_path / "corpus"
  _build(root)

  gate = build_trust_gate(root)

  assert gate.state == "complete"
  assert gate.is_open
  assert not (root / "judgments" / "trust-acknowledgement.json").exists()


def test_fallout_locks_the_gate_and_states_the_denominator(tmp_path: Path):
  """One of two Papers never parsed: locked, with 1 of 2 as the denominator."""
  gate = build_trust_gate(_fallout(tmp_path))

  assert gate.state == "locked"
  assert not gate.is_open
  assert (gate.reached_papers, gate.total_papers) == (1, 2)


def test_acknowledging_opens_the_gate_and_is_remembered_on_disk(tmp_path: Path):
  """The acknowledgement survives a restart: it is read back from judgments/."""
  root = _fallout(tmp_path)

  acknowledge_denominator(root)

  gate = build_trust_gate(root)
  assert gate.state == "acknowledged"
  assert gate.is_open
  record = json.loads(
    (root / "judgments" / "trust-acknowledgement.json").read_text(encoding="utf-8")
  )
  assert (record["reached_papers"], record["total_papers"]) == (1, 2)


def test_a_changed_corpus_state_asks_again(tmp_path: Path):
  """A third Paper joins the manifest: the old 1-of-2 acknowledgement lapses."""
  root = _fallout(tmp_path)
  acknowledge_denominator(root)
  manifest = root / "artifacts" / "corpus-manifest.json"
  data = json.loads(manifest.read_text(encoding="utf-8"))
  data["papers"].append({"citation_key": "c", "title": "Title c"})
  _touch(manifest, 1_700_000_010, data)

  gate = build_trust_gate(root)

  assert gate.state == "locked"
  assert (gate.reached_papers, gate.total_papers) == (1, 3)


def test_an_acknowledged_corpus_moves_the_recommendation_past_trust(tmp_path: Path):
  """Home stops sending the researcher to Trust, but Trust is not ticked done."""
  root = _fallout(tmp_path)
  acknowledge_denominator(root)

  progress = build_flow_progress(root)

  assert progress.recommended == Step.EXPLORE
  assert Step.TRUST not in progress.completed


def test_a_corpus_not_yet_extracted_is_open(tmp_path: Path):
  """Before extract runs the fallout is unknown and the pages offer run buttons."""
  root = _fallout(tmp_path)
  (root / "artifacts" / "extractions.json").unlink()

  gate = build_trust_gate(root)

  assert gate.state == "unbuilt"
  assert gate.is_open
