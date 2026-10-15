"""
The Needs Attention page applies a pasted correction and re-extracts in one action.

The researcher pastes a broken Paper's text and clicks once: the page saves the
correction overlay (the dashboard's only write, ADR 0006), then the runner
launches `apply-parse-corrections` and `extract` (ADR 0007), and the queue is
rebuilt from the artifacts they wrote. The subprocess is patched; a stand-in
"pipeline" rewrites the artifacts the way a real run would, so the refresh is
observable without running the pipeline.
"""

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
from streamlit.testing.v1 import AppTest

import research_gap_dashboard.dashboard as dashboard_pkg
from research_gap_dashboard.dashboard import pipeline_runner, text
from research_gap_dashboard.dashboard.grounding import PARSED_SUFFIX
from research_gap_dashboard.dashboard.parse_corrections import load_parse_corrections
from research_gap_dashboard.parsing import ParsedPaper, ParsedSection

_TEXT = "We studied outcomes in older patients across a decade of registry data."


def _write_json(path: Path, payload: dict) -> None:
  path.parent.mkdir(parents=True, exist_ok=True)
  path.write_text(json.dumps(payload), encoding="utf-8")


def _write_parsed(root: Path, citation_key: str) -> None:
  parsed = ParsedPaper(
    source_pdf=Path(f"{citation_key}.pdf"),
    sections=[ParsedSection(label="other", heading="", text=_TEXT)],
  )
  path = root / "paper-data" / f"{citation_key}{PARSED_SUFFIX}"
  path.parent.mkdir(parents=True, exist_ok=True)
  path.write_text(parsed.model_dump_json(), encoding="utf-8")


def _write_extractions(root: Path, extracted: list[str]) -> None:
  _write_json(
    root / "artifacts" / "extractions.json",
    {
      "corpus_root": str(root),
      "extractions": [{"citation_key": key} for key in extracted],
      "failures": [],
      "unverified": [],
    },
  )


@pytest.fixture(name="corpus")
def corpus_fixture(tmp_path: Path) -> Path:
  """Write a two-Paper Corpus whose 'scan' PDF no parser could read."""
  root = tmp_path / "corpus"
  _write_json(
    root / "artifacts" / "corpus-manifest.json",
    {
      "corpus_root": str(root),
      "papers": [
        {"citation_key": key, "doi": "10.0/x", "title": f"Title {key}"}
        for key in ("scan", "ok")
      ],
    },
  )
  _write_json(
    root / "artifacts" / "parse-report.json",
    {
      "corpus_root": str(root),
      "parsed_paths": [],
      "failures": [
        {"citation_key": "scan", "pdf_path": "scan.pdf", "error": "no usable text"}
      ],
    },
  )
  _write_parsed(root, "ok")
  _write_extractions(root, ["ok"])
  return root


class _FakePipeline:
  """Stands in for `subprocess.run`, writing what each command would write."""

  def __init__(self, corpus: Path) -> None:
    self.corpus = corpus
    self.returncodes: dict[str, int] = {}
    self.commands: list[str] = []

  def __call__(self, args: list[str], **_kwargs: Any) -> subprocess.CompletedProcess:
    command = args[3]
    self.commands.append(command)
    code = self.returncodes.get(command, 0)
    if code == 0 and command == "apply-parse-corrections":
      _write_parsed(self.corpus, "scan")
    if code == 0 and command == "extract":
      _write_extractions(self.corpus, ["ok", "scan"])
    return subprocess.CompletedProcess(args, code, "", "")


@pytest.fixture(name="pipeline")
def pipeline_fixture(corpus: Path, monkeypatch: pytest.MonkeyPatch) -> _FakePipeline:
  """Patch the subprocess the runner launches; the real CLI never runs."""
  fake = _FakePipeline(corpus)
  monkeypatch.setattr(pipeline_runner.subprocess, "run", fake)
  return fake


def _open_attention(corpus: Path, monkeypatch: pytest.MonkeyPatch) -> AppTest:
  monkeypatch.setenv("RESEARCH_GAP_CORPORA_DIR", str(corpus.parent))
  app_path = Path(dashboard_pkg.__file__).parent / "app.py"
  app = AppTest.from_file(str(app_path), default_timeout=30).run()
  app.radio[0].set_value(text.PAGE_ATTENTION).run()
  return app


def _apply(app: AppTest, pasted: str) -> None:
  app.text_area(key="parse-correction-scan").input(pasted)
  apply = next(b for b in app.button if b.label == text.ATTENTION_APPLY_CORRECTION)
  apply.click().run()


def test_apply_saves_runs_apply_then_extract_and_refreshes_the_queue(
  corpus: Path, pipeline: _FakePipeline, monkeypatch: pytest.MonkeyPatch
):
  """One click: overlay saved, both seam commands run in order, queue redrawn."""
  monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
  app = _open_attention(corpus, monkeypatch)

  _apply(app, _TEXT)

  assert not app.exception
  correction = load_parse_corrections(corpus).correction_for("scan")
  assert correction is not None
  assert correction.corrected_text == _TEXT
  assert pipeline.commands == ["apply-parse-corrections", "extract"]
  successes = [s.value for s in app.success]
  assert text.RUN_SUCCEEDED.format(stage="apply-parse-corrections") in successes
  assert text.RUN_SUCCEEDED.format(stage="extract") in successes
  assert text.ATTENTION_ALL_CLEAR in successes


def test_a_failed_apply_never_re_extracts(
  corpus: Path, pipeline: _FakePipeline, monkeypatch: pytest.MonkeyPatch
):
  """If the correction can't be applied, extract is not launched and the queue stays."""
  monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
  pipeline.returncodes["apply-parse-corrections"] = 1
  app = _open_attention(corpus, monkeypatch)

  _apply(app, _TEXT)

  assert not app.exception
  assert pipeline.commands == ["apply-parse-corrections"]
  errors = [e.value for e in app.error]
  assert text.RUN_FAILED.format(stage="apply-parse-corrections") in errors
  assert text.ATTENTION_ALL_CLEAR not in [s.value for s in app.success]


def test_a_missing_key_saves_the_correction_but_launches_nothing(
  corpus: Path, pipeline: _FakePipeline, monkeypatch: pytest.MonkeyPatch
):
  """Re-extracting needs the key; the paste is kept so nothing is lost."""
  monkeypatch.setenv("ANTHROPIC_API_KEY", "")
  app = _open_attention(corpus, monkeypatch)

  _apply(app, _TEXT)

  assert not app.exception
  assert not pipeline.commands
  assert load_parse_corrections(corpus).correction_for("scan") is not None
  assert text.RUN_MISSING_KEY.format(stage="extract") in [e.value for e in app.error]


def test_an_empty_paste_is_refused_without_a_run(
  corpus: Path, pipeline: _FakePipeline, monkeypatch: pytest.MonkeyPatch
):
  """A blank correction is neither saved nor applied."""
  monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
  app = _open_attention(corpus, monkeypatch)

  _apply(app, "   ")

  assert not app.exception
  assert not pipeline.commands
  assert text.ATTENTION_CORRECTION_EMPTY in [w.value for w in app.warning]
