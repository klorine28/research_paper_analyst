"""
Behavior of the pipeline status panel (ADR 0007, issue #70).

Each stage is shown as done, not run, or stale, read only from which artifacts
exist and when they were written: stale means an input is newer than the
artifact it produced. A stage's estimated cost and the last run's actual cost
sit in a collapsed "Run details", and a run whose estimate is above
`LLM_COST_CONFIRM_ABOVE` asks for confirmation before it launches. The real
pipeline never runs here: `subprocess.run` is patched.
"""

import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

import research_gap_dashboard.dashboard as dashboard_pkg
from research_gap_dashboard.dashboard import pipeline_runner, text
from research_gap_dashboard.dashboard.pipeline_status import (
  StageState,
  build_pipeline_status,
)
from research_gap_dashboard.dashboard.run_cost import (
  CostConfigError,
  actual_stage_cost,
  cost_confirm_threshold,
  estimate_stage_cost,
  needs_confirmation,
)

_T0 = 1_700_000_000


def _touch(path: Path, mtime: int, content: str = "{}") -> Path:
  """Write a file and set its modification time."""
  path.parent.mkdir(parents=True, exist_ok=True)
  path.write_text(content, encoding="utf-8")
  os.utime(path, (mtime, mtime))
  return path


def _manifest(root: Path, keys: list[str]) -> str:
  """Return a CorpusManifest JSON body naming these Papers."""
  return json.dumps(
    {
      "corpus_root": str(root),
      "papers": [{"citation_key": key} for key in keys],
      "unmatched_entries": [],
      "orphan_pdfs": [],
    }
  )


@pytest.fixture(name="ingested")
def ingested_fixture(tmp_path: Path) -> Path:
  """Write a Corpus with its inputs and a manifest written after them, nothing else."""
  root = tmp_path / "corpus"
  _touch(root / "corpus.bib", _T0)
  _touch(root / "papers" / "a.pdf", _T0)
  _touch(
    root / "artifacts" / "corpus-manifest.json",
    _T0 + 10,
    _manifest(root, ["a", "b"]),
  )
  return root


def _states(root: Path) -> dict[str, StageState]:
  return {status.stage: status.state for status in build_pipeline_status(root)}


# --- Status from existence + modification time ---------------------------------


def test_stages_are_listed_in_pipeline_order(ingested: Path):
  """The panel lists every runnable stage in the order they are run."""
  stages = [status.stage for status in build_pipeline_status(ingested)]

  assert stages == [
    "ingest",
    "parse",
    "extract",
    "explain",
    "aggregate",
    "detect",
    "retrieve",
  ]


def test_a_stage_without_its_artifact_is_not_run(ingested: Path):
  """Only ingest has written its artifact; every later stage is not run."""
  states = _states(ingested)

  assert states["ingest"] == StageState.DONE
  assert all(
    state == StageState.NOT_RUN for stage, state in states.items() if stage != "ingest"
  )


def test_an_input_newer_than_the_artifact_makes_the_stage_stale(ingested: Path):
  """A PDF added after ingest ran makes ingest stale, naming the newer input."""
  _touch(ingested / "papers" / "new.pdf", _T0 + 20)

  status = next(s for s in build_pipeline_status(ingested) if s.stage == "ingest")

  assert status.state == StageState.STALE
  assert status.newer_inputs == ["papers/new.pdf"]


def test_staleness_follows_the_chain_of_artifacts(ingested: Path):
  """Re-extracting after aggregate ran makes aggregate and detect stale."""
  artifacts = ingested / "artifacts"
  _touch(artifacts / "parse-report.json", _T0 + 20)
  _touch(ingested / "paper-data" / "a.parsed.json", _T0 + 20)
  _touch(artifacts / "normalized_facts.json", _T0 + 40)
  _touch(artifacts / "candidate_gaps.json", _T0 + 50)
  _touch(artifacts / "extractions.json", _T0 + 60)

  states = _states(ingested)

  assert states["parse"] == StageState.DONE
  assert states["extract"] == StageState.DONE
  assert states["aggregate"] == StageState.STALE
  assert states["detect"] == StageState.STALE


def test_explain_is_done_once_explanations_exist(ingested: Path):
  """Explain writes one file per Paper; it is stale when a parse is newer."""
  data = ingested / "paper-data"
  _touch(data / "a.parsed.json", _T0 + 20)
  _touch(data / "a.explanation.json", _T0 + 30)
  assert _states(ingested)["explain"] == StageState.DONE

  _touch(data / "b.parsed.json", _T0 + 40)
  assert _states(ingested)["explain"] == StageState.STALE


# --- Cost: estimate, actual, and the confirmation threshold --------------------


def test_an_llm_stage_estimate_scales_with_the_papers(ingested: Path):
  """Extract on two Papers is estimated at twice the per-Paper cost."""
  env = {"LLM_DEFAULT_MODEL": "claude-sonnet-4-5"}

  estimate = estimate_stage_cost(ingested, "extract", env)

  # Per Paper: 15k input tokens at $3/M plus 5k output tokens at $15/M = $0.12.
  assert estimate.paper_count == 2
  assert estimate.model == "claude-sonnet-4-5"
  assert estimate.usd == pytest.approx(0.24)


def test_a_stage_without_the_llm_costs_nothing(ingested: Path):
  """Parse makes no LLM call, so it has no cost and never asks to confirm."""
  estimate = estimate_stage_cost(ingested, "parse", {})

  assert estimate.usd == 0
  assert not needs_confirmation(estimate, 0.0)


def test_an_unpriced_model_has_no_estimate_and_always_confirms(ingested: Path):
  """A model the dashboard has no price for can't be ruled cheap, so it confirms."""
  estimate = estimate_stage_cost(ingested, "extract", {"LLM_DEFAULT_MODEL": "mystery"})

  assert estimate.usd is None
  assert needs_confirmation(estimate, 1000.0)


def test_confirmation_is_needed_only_above_the_threshold(ingested: Path):
  """$0.24 asks for confirmation under a $0.10 threshold, not under $1."""
  estimate = estimate_stage_cost(ingested, "extract", {})

  assert needs_confirmation(estimate, 0.10)
  assert not needs_confirmation(estimate, 1.00)


@pytest.mark.parametrize(
  ("raw", "expected"), [(None, 1.0), ("", 1.0), ("2.5", 2.5), ("0", 0.0)]
)
def test_the_threshold_is_read_from_the_environment(raw: str | None, expected: float):
  """Unset means the $1.00 default; a number is dollars."""
  env = {} if raw is None else {"LLM_COST_CONFIRM_ABOVE": raw}

  assert cost_confirm_threshold(env) == expected


@pytest.mark.parametrize("raw", ["cheap", "-1", "nan"])
def test_an_invalid_threshold_is_an_explained_error(raw: str):
  """A threshold that isn't a non-negative number is refused with the reason."""
  with pytest.raises(CostConfigError, match="LLM_COST_CONFIRM_ABOVE"):
    cost_confirm_threshold({"LLM_COST_CONFIRM_ABOVE": raw})


def test_actual_cost_comes_from_the_recorded_usage(ingested: Path):
  """The last run's token usage, recorded by the pipeline, is priced per model."""
  _touch(
    ingested / "artifacts" / "llm-usage.json",
    _T0 + 30,
    json.dumps(
      {
        "extract": {
          "calls": 2,
          "cached_calls": 3,
          "by_model": {
            "claude-sonnet-4-5": {
              "calls": 2,
              "input_tokens": 1_000_000,
              "output_tokens": 100_000,
            }
          },
        }
      }
    ),
  )

  actual = actual_stage_cost(ingested, "extract")

  assert actual is not None
  assert actual.calls == 2
  assert actual.cached_calls == 3
  assert actual.usd == pytest.approx(4.5)
  assert actual_stage_cost(ingested, "detect") is None


# --- The panel, as the researcher meets it -------------------------------------


class _FakeRun:  # pylint: disable=too-few-public-methods
  """Stands in for `subprocess.run`, recording each argv."""

  def __init__(self) -> None:
    self.calls: list[list[str]] = []

  def __call__(self, args: list[str], **_kwargs: Any) -> subprocess.CompletedProcess:
    self.calls.append(list(args))
    return subprocess.CompletedProcess(args, 0, "", "")


@pytest.fixture(name="fake_run")
def fake_run_fixture(monkeypatch: pytest.MonkeyPatch) -> _FakeRun:
  """Patch the subprocess the runner launches; the real CLI never runs."""
  fake = _FakeRun()
  monkeypatch.setattr(pipeline_runner.subprocess, "run", fake)
  return fake


def _open_pipeline(corpus: Path, monkeypatch: pytest.MonkeyPatch):
  """Run the Streamlit shell on the Pipeline page for a corpus directory."""
  from streamlit.testing.v1 import AppTest

  monkeypatch.setenv("RESEARCH_GAP_CORPORA_DIR", str(corpus.parent))
  monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
  monkeypatch.delenv("LLM_DEFAULT_MODEL", raising=False)
  app_path = Path(dashboard_pkg.__file__).parent / "app.py"
  app = AppTest.from_file(str(app_path), default_timeout=30).run()
  app.radio[0].set_value(text.PAGE_PIPELINE).run()
  return app


def _button(app, label: str):
  return next(button for button in app.button if button.label == label)


def _markdown(app) -> str:
  return "\n".join(block.value for block in app.markdown)


def test_the_panel_shows_each_stage_status(
  ingested: Path, monkeypatch: pytest.MonkeyPatch, fake_run: _FakeRun
):
  """A stale stage says which input changed, and the rest are not run."""
  _touch(ingested / "papers" / "new.pdf", _T0 + 20)
  app = _open_pipeline(ingested, monkeypatch)

  assert not app.exception
  shown = _markdown(app)
  assert text.STAGE_STATE_LABELS["stale"] in shown
  assert text.STAGE_STATE_LABELS["not_run"] in shown
  assert "artifacts/corpus-manifest.json" in shown
  assert any("papers/new.pdf" in caption.value for caption in app.caption)
  assert not fake_run.calls


def test_every_stage_offers_run_or_re_run(
  ingested: Path, monkeypatch: pytest.MonkeyPatch, fake_run: _FakeRun
):
  """A done stage can be re-run, and clicking launches that stage."""
  app = _open_pipeline(ingested, monkeypatch)

  labels = [button.label for button in app.button]
  assert text.RERUN_STAGE_BUTTON.format(stage="ingest") in labels
  assert text.RUN_STAGE_BUTTON.format(stage="parse") in labels

  assert text.STAGE_STATE_LABELS["done"] in _markdown(app)

  _button(app, text.RERUN_STAGE_BUTTON.format(stage="ingest")).click().run()

  assert not app.exception
  assert fake_run.calls[0][2:4] == ["research-gap-dashboard", "ingest"]
  assert text.RUN_SUCCEEDED.format(stage="ingest") in [s.value for s in app.success]


def test_cost_appears_only_inside_the_collapsed_run_details(
  ingested: Path, monkeypatch: pytest.MonkeyPatch, fake_run: _FakeRun
):
  """The estimate is in a collapsed "Run details" expander, nowhere else."""
  app = _open_pipeline(ingested, monkeypatch)

  details = next(e for e in app.expander if e.label == text.RUN_DETAILS_LABEL)
  assert details.proto.expanded is False
  inside = [block.value for block in [*details.markdown, *details.caption]]
  assert any("$0.24" in value for value in inside)
  everywhere = [block.value for block in [*app.markdown, *app.caption]]
  outside = [value for value in everywhere if value not in inside]
  assert not any("$" in value for value in outside)
  assert not fake_run.calls


def test_an_expensive_run_asks_for_confirmation_first(
  ingested: Path, monkeypatch: pytest.MonkeyPatch, fake_run: _FakeRun
):
  """Above the threshold, Run asks first; Confirm launches, nothing ran before."""
  monkeypatch.setenv("LLM_COST_CONFIRM_ABOVE", "0.10")
  app = _open_pipeline(ingested, monkeypatch)

  _button(app, text.RUN_STAGE_BUTTON.format(stage="extract")).click().run()

  assert not app.exception
  assert not fake_run.calls
  assert text.RUN_CONFIRM_PROMPT.format(stage="extract") in [
    w.value for w in app.warning
  ]

  _button(app, text.RUN_CONFIRM_BUTTON).click().run()

  assert not app.exception
  assert fake_run.calls[0][2:4] == ["research-gap-dashboard", "extract"]


def test_a_confirmation_can_be_cancelled(
  ingested: Path, monkeypatch: pytest.MonkeyPatch, fake_run: _FakeRun
):
  """Cancel drops the pending run without launching it."""
  monkeypatch.setenv("LLM_COST_CONFIRM_ABOVE", "0.10")
  app = _open_pipeline(ingested, monkeypatch)
  _button(app, text.RUN_STAGE_BUTTON.format(stage="extract")).click().run()

  _button(app, text.RUN_CANCEL_BUTTON).click().run()

  assert not app.exception
  assert not fake_run.calls
  assert text.RUN_CONFIRM_PROMPT.format(stage="extract") not in [
    w.value for w in app.warning
  ]


def test_a_cheap_run_launches_without_confirmation(
  ingested: Path, monkeypatch: pytest.MonkeyPatch, fake_run: _FakeRun
):
  """Under the default $1.00 threshold, a $0.24 extract runs on the first click."""
  app = _open_pipeline(ingested, monkeypatch)

  _button(app, text.RUN_STAGE_BUTTON.format(stage="extract")).click().run()

  assert not app.exception
  assert fake_run.calls[0][2:4] == ["research-gap-dashboard", "extract"]
