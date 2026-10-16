"""
Behavior of the pipeline runner: the dashboard's one seam onto the CLI (ADR 0007).

The real pipeline never runs here: `subprocess.run` is patched, so these tests
assert the exact command the runner launches, that a second concurrent run on
the same Corpus is refused, that a missing API key stops a run before launch,
and how exit codes map to the outcome the researcher sees. The Gap Cards empty
state is driven through `AppTest`, the way the researcher meets it.
"""

from conftest import open_page
import subprocess
from pathlib import Path
from typing import Any

import pytest

from test_detect import _PLACEMENTS, _write_corpus

from research_gap_dashboard.dashboard.trust_gate import acknowledge_denominator
import research_gap_dashboard.dashboard as dashboard_pkg
from research_gap_dashboard.dashboard import pipeline_runner, text
from research_gap_dashboard.dashboard.pipeline_runner import (
  RunOutcome,
  failure_reason,
  run_stage,
  run_stages,
)

_KEY_ENV = {"ANTHROPIC_API_KEY": "sk-test"}


class _FakeRun:
  """Stands in for `subprocess.run`, recording each call and its arguments."""

  def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = ""):
    self.returncode = returncode
    self.stdout = stdout
    self.stderr = stderr
    self.calls: list[tuple[list[str], dict[str, Any]]] = []
    self.during: Any = None

  def __call__(self, args: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
    self.calls.append((list(args), kwargs))
    if self.during is not None:
      self.during()
    return subprocess.CompletedProcess(args, self.returncode, self.stdout, self.stderr)


@pytest.fixture(name="fake_run")
def fake_run_fixture(monkeypatch: pytest.MonkeyPatch) -> _FakeRun:
  """Patch the subprocess the runner launches; the real CLI never runs."""
  fake = _FakeRun()
  monkeypatch.setattr(pipeline_runner.subprocess, "run", fake)
  return fake


def test_detect_runs_the_cli_as_a_subprocess(tmp_path: Path, fake_run: _FakeRun):
  """The runner shells out to the CLI's detect command on the Corpus."""
  result = run_stage(tmp_path, "detect", env=_KEY_ENV)

  assert len(fake_run.calls) == 1
  args, kwargs = fake_run.calls[0]
  expected = ["uv", "run", "research-gap-dashboard", "detect", str(tmp_path.resolve())]
  assert args == expected
  assert result.command == expected
  assert kwargs["capture_output"] is True
  assert kwargs["text"] is True
  assert kwargs["check"] is False


def test_stage_options_follow_the_corpus_path(tmp_path: Path, fake_run: _FakeRun):
  """Options such as ingest's `--resolve` are passed after the Corpus path."""
  result = run_stage(tmp_path, "ingest", env={}, args=["--resolve"])

  expected = [
    "uv",
    "run",
    "research-gap-dashboard",
    "ingest",
    str(tmp_path.resolve()),
    "--resolve",
  ]
  assert fake_run.calls[0][0] == expected
  assert result.command == expected


@pytest.mark.parametrize(
  ("returncode", "outcome"),
  [
    (0, RunOutcome.SUCCEEDED),
    (2, RunOutcome.INCOMPLETE),
    (1, RunOutcome.FAILED),
    (137, RunOutcome.FAILED),
  ],
)
def test_exit_codes_map_to_outcomes(
  tmp_path: Path, fake_run: _FakeRun, returncode: int, outcome: RunOutcome
):
  """0 succeeded, 2 completed but incomplete (ADR 0006), anything else failed."""
  fake_run.returncode = returncode
  fake_run.stdout = "out text"
  fake_run.stderr = "err text"

  result = run_stage(tmp_path, "detect", env=_KEY_ENV)

  assert result.outcome == outcome
  assert result.exit_code == returncode
  assert result.stdout == "out text"
  assert result.stderr == "err text"


def test_missing_api_key_is_caught_before_launch(tmp_path: Path, fake_run: _FakeRun):
  """A stage that needs the LLM is never launched without ANTHROPIC_API_KEY."""
  result = run_stage(tmp_path, "detect", env={"ANTHROPIC_API_KEY": ""})

  assert result.outcome == RunOutcome.MISSING_KEY
  assert result.exit_code is None
  assert not fake_run.calls


def test_a_stage_without_the_llm_needs_no_key(tmp_path: Path, fake_run: _FakeRun):
  """`ingest` makes no LLM call, so it launches without a key."""
  result = run_stage(tmp_path, "ingest", env={})

  assert result.outcome == RunOutcome.SUCCEEDED
  assert len(fake_run.calls) == 1


def test_a_second_concurrent_run_on_the_same_corpus_is_refused(
  tmp_path: Path, fake_run: _FakeRun
):
  """While one run holds a Corpus, another is refused without launching."""
  nested: list[Any] = []
  fake_run.during = lambda: nested.append(run_stage(tmp_path, "detect", env=_KEY_ENV))

  first = run_stage(tmp_path, "detect", env=_KEY_ENV)

  assert first.outcome == RunOutcome.SUCCEEDED
  assert nested[0].outcome == RunOutcome.LOCKED
  assert len(fake_run.calls) == 1


def test_the_lock_is_per_corpus(tmp_path: Path, fake_run: _FakeRun):
  """A run on one Corpus doesn't block a run on another."""
  other = tmp_path / "other"
  other.mkdir()
  nested: list[Any] = []

  def _run_other_once() -> None:
    fake_run.during = None
    nested.append(run_stage(other, "detect", env=_KEY_ENV))

  fake_run.during = _run_other_once

  run_stage(tmp_path / "first", "detect", env=_KEY_ENV)

  assert nested[0].outcome == RunOutcome.SUCCEEDED


def test_the_lock_is_released_after_a_run(tmp_path: Path, fake_run: _FakeRun):
  """A finished run, even a failed one, frees the Corpus for the next run."""
  fake_run.returncode = 1
  run_stage(tmp_path, "detect", env=_KEY_ENV)
  fake_run.returncode = 0

  assert run_stage(tmp_path, "detect", env=_KEY_ENV).outcome == RunOutcome.SUCCEEDED


def test_a_missing_uv_executable_is_a_failed_run(
  tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
  """If the CLI can't be launched at all, the run fails with a reason, not a crash."""

  def _missing(*_args: Any, **_kwargs: Any) -> subprocess.CompletedProcess:
    raise FileNotFoundError("uv")

  monkeypatch.setattr(pipeline_runner.subprocess, "run", _missing)

  result = run_stage(tmp_path, "detect", env=_KEY_ENV)

  assert result.outcome == RunOutcome.FAILED
  assert result.exit_code is None
  assert "uv" in result.stderr


# --- Chained runs (a seam command, then the stage it feeds) -------------------


def test_a_chain_runs_each_command_in_order(tmp_path: Path, fake_run: _FakeRun):
  """Apply-then-extract launches both CLI commands, in that order."""
  results = run_stages(tmp_path, ["apply-parse-corrections", "extract"], env=_KEY_ENV)

  assert [args[3] for args, _ in fake_run.calls] == [
    "apply-parse-corrections",
    "extract",
  ]
  assert [result.outcome for result in results] == [
    RunOutcome.SUCCEEDED,
    RunOutcome.SUCCEEDED,
  ]


def test_a_chain_stops_at_the_first_failure(tmp_path: Path, fake_run: _FakeRun):
  """If applying the correction fails, extract is never launched."""
  fake_run.returncode = 1

  results = run_stages(tmp_path, ["apply-parse-corrections", "extract"], env=_KEY_ENV)

  assert len(fake_run.calls) == 1
  assert [result.outcome for result in results] == [RunOutcome.FAILED]


def test_a_chain_checks_the_key_before_launching_anything(
  tmp_path: Path, fake_run: _FakeRun
):
  """A later stage's missing key stops the chain before its first command runs."""
  results = run_stages(tmp_path, ["apply-parse-corrections", "extract"], env={})

  assert not fake_run.calls
  assert [(r.stage, r.outcome) for r in results] == [
    ("extract", RunOutcome.MISSING_KEY)
  ]


def test_a_chain_holds_the_lock_throughout(tmp_path: Path, fake_run: _FakeRun):
  """No other run can slip in between the two commands of a chain."""
  nested: list[Any] = []
  fake_run.during = lambda: nested.append(run_stage(tmp_path, "detect", env=_KEY_ENV))

  run_stages(tmp_path, ["apply-parse-corrections", "extract"], env=_KEY_ENV)

  assert [result.outcome for result in nested] == [RunOutcome.LOCKED] * 2
  assert len(fake_run.calls) == 2


def test_promote_gold_needs_no_key(tmp_path: Path, fake_run: _FakeRun):
  """promote-gold makes no LLM call, so it launches without a key."""
  result = run_stage(tmp_path, "promote-gold", env={})

  assert result.outcome == RunOutcome.SUCCEEDED
  assert fake_run.calls[0][0][3] == "promote-gold"


def test_a_failure_reason_is_the_last_line_of_stderr(
  tmp_path: Path, fake_run: _FakeRun
):
  """The CLI logs its reason last, so that line is what the page shows."""
  fake_run.returncode = 1
  fake_run.stderr = "Building...\nGold promotion refused: Paper 'a'.\n\n"

  result = run_stage(tmp_path, "promote-gold", env={})

  assert failure_reason(result) == "Gold promotion refused: Paper 'a'."


# --- The Gap Cards empty state -------------------------------------------------


@pytest.fixture(name="undetected_corpus")
def undetected_corpus_fixture(tmp_path: Path) -> Path:
  """Write a Corpus with a manifest but no CandidateGaps artifact yet."""
  return _write_corpus(tmp_path / "corpus", _PLACEMENTS)


def _open_gap_cards(corpus: Path, monkeypatch: pytest.MonkeyPatch):
  """Run the Streamlit shell on the Gap Cards page for a corpus directory."""
  from streamlit.testing.v1 import AppTest

  # Past the soft Trust gate (#77): this fixture Corpus is partial.
  acknowledge_denominator(corpus)
  monkeypatch.setenv("RESEARCH_GAP_CORPORA_DIR", str(corpus.parent))
  app_path = Path(dashboard_pkg.__file__).parent / "app.py"
  app = AppTest.from_file(str(app_path), default_timeout=30).run()
  open_page(app, text.PAGE_GAP_CARDS)
  return app


def _run_detect_button(app):
  """Return the empty state's Run detect button."""
  return next(button for button in app.button if button.label == text.RUN_DETECT_BUTTON)


def test_empty_gap_cards_offers_run_detect(
  undetected_corpus: Path, monkeypatch: pytest.MonkeyPatch, fake_run: _FakeRun
):
  """With no candidate gaps, the page offers a button instead of a CLI command."""
  monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
  app = _open_gap_cards(undetected_corpus, monkeypatch)

  assert not app.exception
  _run_detect_button(app).click().run()

  assert not app.exception
  args, _ = fake_run.calls[0]
  assert args[2:4] == ["research-gap-dashboard", "detect"]
  assert text.RUN_SUCCEEDED.format(stage="detect") in [s.value for s in app.success]


def test_empty_gap_cards_reports_an_incomplete_run(
  undetected_corpus: Path, monkeypatch: pytest.MonkeyPatch, fake_run: _FakeRun
):
  """Exit code 2 is shown as completed but incomplete, with the output available."""
  monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
  fake_run.returncode = 2
  fake_run.stderr = "3 Papers fell out"
  app = _open_gap_cards(undetected_corpus, monkeypatch)

  _run_detect_button(app).click().run()

  assert not app.exception
  assert text.RUN_INCOMPLETE.format(stage="detect") in [w.value for w in app.warning]
  assert any("3 Papers fell out" in block.value for block in app.code)


def test_empty_gap_cards_reports_a_failed_run(
  undetected_corpus: Path, monkeypatch: pytest.MonkeyPatch, fake_run: _FakeRun
):
  """Exit code 1 is shown as failed."""
  monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
  fake_run.returncode = 1
  app = _open_gap_cards(undetected_corpus, monkeypatch)

  _run_detect_button(app).click().run()

  assert not app.exception
  assert text.RUN_FAILED.format(stage="detect") in [e.value for e in app.error]


def test_empty_gap_cards_explains_a_missing_key(
  undetected_corpus: Path, monkeypatch: pytest.MonkeyPatch, fake_run: _FakeRun
):
  """Without a key, the button explains how to fix it and launches nothing."""
  monkeypatch.setenv("ANTHROPIC_API_KEY", "")
  app = _open_gap_cards(undetected_corpus, monkeypatch)

  _run_detect_button(app).click().run()

  assert not app.exception
  assert not fake_run.calls
  assert text.RUN_MISSING_KEY.format(stage="detect") in [e.value for e in app.error]
