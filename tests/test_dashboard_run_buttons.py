"""
Every page's empty or guard state offers the button that runs the missing stage.

Issue #71 (UI audit H9): no page tells the researcher to go to a terminal.
Each test opens a page through `AppTest` on a Corpus that lacks the stage's
artifact, clicks the run button the guard offers, and checks the runner launched
the right CLI stage. `subprocess.run` is patched, so the real pipeline never runs.
"""

import ast
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from conftest import StubLlmClient

import research_gap_dashboard.analytics as analytics_module
import research_gap_dashboard.dashboard as dashboard_pkg
from research_gap_dashboard.dashboard import pipeline_runner, text
from research_gap_dashboard.ingest import ingest_corpus


class _FakeRun:
  """Stands in for `subprocess.run`, recording each launched command."""

  def __init__(self) -> None:
    self.calls: list[list[str]] = []

  def __call__(self, args: list[str], **_kwargs: Any) -> subprocess.CompletedProcess:
    self.calls.append(list(args))
    return subprocess.CompletedProcess(args, 0, "", "")


@pytest.fixture(name="fake_run")
def fake_run_fixture(monkeypatch: pytest.MonkeyPatch) -> _FakeRun:
  """Patch the subprocess the runner launches and provide an API key."""
  fake = _FakeRun()
  monkeypatch.setattr(pipeline_runner.subprocess, "run", fake)
  monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
  return fake


@pytest.fixture(name="ingested")
def ingested_fixture(cardiology_corpus: Path, tmp_path: Path) -> Path:
  """Copy the fixture Corpus and ingest it (DOIs unresolved), nothing more."""
  target = tmp_path / "corpora" / "corpus"
  shutil.copytree(cardiology_corpus, target)
  ingest_corpus(target)
  return target


def _open(corpora_dir: Path, monkeypatch: pytest.MonkeyPatch, page: str | None):
  """Run the Streamlit shell against a corpora folder, on one page."""
  from streamlit.testing.v1 import AppTest

  monkeypatch.setenv("RESEARCH_GAP_CORPORA_DIR", str(corpora_dir))
  app_path = Path(dashboard_pkg.__file__).parent / "app.py"
  app = AppTest.from_file(str(app_path), default_timeout=30).run()
  if page is not None:
    app.radio[0].set_value(page).run()
  return app


def _click(app, label: str) -> None:
  """Click the button with this label and let the page rerun."""
  labels = [button.label for button in app.button]
  assert label in labels, labels
  next(button for button in app.button if button.label == label).click().run()


def _launched(fake_run: _FakeRun) -> list[str]:
  """Return the stage (and any options) of the one command the runner launched."""
  assert len(fake_run.calls) == 1, fake_run.calls
  command = fake_run.calls[0]
  assert command[:3] == ["uv", "run", "research-gap-dashboard"]
  return [command[3], *command[5:]]


def _run(stage: str) -> str:
  """Return the run-button label for a stage."""
  return text.run_stage_button(stage)


@pytest.mark.parametrize(
  ("page", "label", "stage"),
  [
    (text.PAGE_LIMITATIONS, _run("detect"), "detect"),
    (text.PAGE_RETRIEVAL, _run("retrieve"), "retrieve"),
    (text.PAGE_COMPARISON, _run("extract"), "extract"),
    (text.PAGE_COMPARISON, _run("aggregate"), "aggregate"),
    (text.PAGE_VERIFY, _run("extract"), "extract"),
    (text.PAGE_EXPLAINER, _run("explain"), "explain"),
    (text.PAGE_ATTENTION, _run("apply-parse-corrections"), "apply-parse-corrections"),
    (text.PAGE_ATTENTION, _run("extract"), "extract"),
  ],
)
def test_guard_state_runs_the_missing_stage(
  ingested: Path,
  monkeypatch: pytest.MonkeyPatch,
  fake_run: _FakeRun,
  page: str,
  label: str,
  stage: str,
):
  """Each page's guard offers a run button that launches the stage it lacks."""
  app = _open(ingested.parent, monkeypatch, page)
  assert not app.exception

  _click(app, label)

  assert not app.exception
  assert _launched(fake_run) == [stage]
  assert text.RUN_SUCCEEDED.format(stage=stage) in [s.value for s in app.success]


@pytest.mark.parametrize(
  ("tile", "label", "stage"),
  [("axes", _run("aggregate"), "aggregate"), ("facts", _run("extract"), "extract")],
)
def test_overview_empty_charts_run_their_stage(
  ingested: Path,
  monkeypatch: pytest.MonkeyPatch,
  fake_run: _FakeRun,
  tile: str,
  label: str,
  stage: str,
):
  """An empty Overview chart offers the stage that would fill it."""
  app = _open(ingested.parent, monkeypatch, text.PAGE_OVERVIEW)
  app.button(key=f"ovtile_btn_{tile}").click().run()

  _click(app, label)

  assert not app.exception
  assert _launched(fake_run) == [stage]


@pytest.mark.parametrize(
  ("tile", "label", "launched"),
  [
    ("citation", text.RUN_INGEST_RESOLVE_BUTTON, ["ingest", "--resolve"]),
    ("authors", text.RUN_INGEST_RESOLVE_BUTTON, ["ingest", "--resolve"]),
    ("topic", _run("aggregate"), ["aggregate"]),
    ("trends", _run("aggregate"), ["aggregate"]),
    ("limitation", _run("detect"), ["detect"]),
  ],
)
def test_meta_analysis_empty_graphs_run_their_stage(
  ingested: Path,
  monkeypatch: pytest.MonkeyPatch,
  fake_run: _FakeRun,
  tile: str,
  label: str,
  launched: list[str],
):
  """An empty or thin meta-analysis graph offers the run that would fill it."""
  app = _open(ingested.parent, monkeypatch, text.PAGE_META)
  app.button(key=f"metatile_btn_{tile}").click().run()

  _click(app, label)

  assert not app.exception
  assert _launched(fake_run) == launched


def test_analytics_summary_without_gaps_runs_detect(
  ingested: Path, monkeypatch: pytest.MonkeyPatch, fake_run: _FakeRun
):
  """The narrative summary's no-gaps state offers to run detect."""
  stub = StubLlmClient({"answer": "Grounded.", "citations": []})
  monkeypatch.setattr(analytics_module, "build_analytics_client", lambda _dir: stub)
  app = _open(ingested.parent, monkeypatch, text.PAGE_ANALYTICS)

  _click(app, _run("detect"))

  assert not app.exception
  assert _launched(fake_run) == ["detect"]


def test_no_analysed_corpus_offers_to_ingest_each_folder(
  cardiology_corpus: Path,
  tmp_path: Path,
  monkeypatch: pytest.MonkeyPatch,
  fake_run: _FakeRun,
):
  """With nothing ingested, each corpus folder gets its own ingest button."""
  corpora = tmp_path / "corpora"
  shutil.copytree(cardiology_corpus, corpora / "cardio")
  app = _open(corpora, monkeypatch, None)
  assert not app.exception

  _click(app, text.RUN_INGEST_CORPUS_BUTTON.format(name="cardio"))

  assert not app.exception
  assert _launched(fake_run) == ["ingest"]
  assert fake_run.calls[0][4] == str((corpora / "cardio").resolve())


def test_no_dashboard_string_sends_the_researcher_to_a_terminal():
  """No user-facing string names a CLI command or the command line."""
  source = (Path(dashboard_pkg.__file__).parent / "text.py").read_text(encoding="utf-8")
  strings = [
    node.value
    for node in ast.walk(ast.parse(source))
    if isinstance(node, ast.Constant) and isinstance(node.value, str)
  ]
  terminal = re.compile(r"`[a-z-]+`|--[a-z]|command[- ]line|terminal", re.IGNORECASE)
  offenders = [string for string in strings if terminal.search(string)]
  assert not offenders
