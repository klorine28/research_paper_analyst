"""
The Verify Extractions page renders end to end in the Streamlit shell.

The offline seams (persistence, grounding, assembly) are covered directly; this
smoke test wires them through the app so a schema drift between the artifact
readers and the page would surface here. It builds a minimal Corpus with a
manifest, an Extraction, and parsed text, then drives the page's navigation.
"""

from conftest import open_page
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
from streamlit.testing.v1 import AppTest

import research_gap_dashboard.dashboard as dashboard_pkg
from research_gap_dashboard.dashboard import pipeline_runner
from research_gap_dashboard.dashboard.text import (
  PAGE_VERIFY,
  RUN_FAILED_WITH_REASON,
  RUN_SUCCEEDED,
  VERIFY_HEADING,
  VERIFY_PROMOTE_BUTTON,
)


def _build_corpus(root: Path) -> None:
  """Write the minimal artifacts the Verify page reads: manifest, Extraction, text."""
  (root / "artifacts").mkdir(parents=True)
  (root / "paper-data").mkdir(parents=True)
  (root / "papers").mkdir(parents=True)
  (root / "papers" / "paper-a.pdf").write_bytes(b"%PDF-1.4 fake")

  manifest = {
    "corpus_root": str(root),
    "papers": [
      {
        "citation_key": "paper-a",
        "doi": "10.1/x",
        "title": "A study",
        "year": 2020,
        "journal": "J",
        "pdf_path": str(root / "papers" / "paper-a.pdf"),
      }
    ],
    "unmatched_entries": [],
    "orphan_pdfs": [],
  }
  (root / "artifacts" / "corpus-manifest.json").write_text(
    json.dumps(manifest), encoding="utf-8"
  )

  extractions = {
    "corpus_root": str(root),
    "prompt_version": "extract-v1",
    "tier": "default",
    "extractions": [
      {
        "citation_key": "paper-a",
        "fields": {
          "research_question": [],
          "methods": [
            {
              "statement": "Retrospective cohort study.",
              "evidence": {
                "passage": "A retrospective cohort of 200 patients.",
                "section": "methods",
              },
            }
          ],
          "populations": [],
          "datasets": [],
          "key_findings": [],
          "limitations": [],
          "future_work": [],
        },
      }
    ],
    "failures": [],
  }
  (root / "artifacts" / "extractions.json").write_text(
    json.dumps(extractions), encoding="utf-8"
  )

  parsed = {
    "source_pdf": "paper-a.pdf",
    "sections": [
      {
        "label": "methods",
        "heading": "Methods",
        "text": "A retrospective cohort of 200 patients.",
      }
    ],
  }
  (root / "paper-data" / "paper-a.parsed.json").write_text(
    json.dumps(parsed), encoding="utf-8"
  )


def _run_verify_page(corpora_dir: Path, monkeypatch: pytest.MonkeyPatch) -> AppTest:
  """Run the shell against a corpora folder and switch to the Verify page."""
  # Keep the env var set for the whole test: later interactions rerun the app and
  # must still discover the corpus (restoring it here would blank the page).
  monkeypatch.setenv("RESEARCH_GAP_CORPORA_DIR", str(corpora_dir))
  app_path = Path(dashboard_pkg.__file__).parent / "app.py"
  app = AppTest.from_file(str(app_path), default_timeout=30)
  app.run()
  open_page(app, PAGE_VERIFY)
  return app


def test_verify_page_renders_facts_for_a_paper(
  tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
  """The page opens, shows its heading, and renders the Paper's extracted fact."""
  corpus = tmp_path / "corpus"
  _build_corpus(corpus)

  app = _run_verify_page(tmp_path, monkeypatch)

  assert not app.exception
  assert VERIFY_HEADING in [header.value for header in app.header]
  markdown = " ".join(block.value for block in app.markdown)
  assert "Retrospective cohort study." in markdown


def test_verify_page_approves_a_fact(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
  """Clicking approve persists a verdict to the review overlay."""
  corpus = tmp_path / "corpus"
  _build_corpus(corpus)

  app = _run_verify_page(tmp_path, monkeypatch)
  approve = next(button for button in app.button if button.label == "Approve")
  approve.click().run()

  assert not app.exception
  overlay = corpus / "judgments" / "extraction_review.json"
  assert overlay.is_file()
  saved = json.loads(overlay.read_text(encoding="utf-8"))
  assert saved["reviews"][0]["verdicts"][0]["verdict"] == "approved"


def _captions(app: AppTest) -> str:
  """Join every caption on the page into one searchable string."""
  return " ".join(caption.value for caption in app.caption)


def test_verify_page_shows_corpus_progress_that_updates_on_a_verdict(
  tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
  """Corpus-wide progress states its denominators and moves when a verdict lands."""
  corpus = tmp_path / "corpus"
  _build_corpus(corpus)

  app = _run_verify_page(tmp_path, monkeypatch)
  assert (
    "0 of 1 extracted facts reviewed across the Corpus; "
    "0 of 1 Papers fully reviewed." in _captions(app)
  )

  approve = next(button for button in app.button if button.label == "Approve")
  approve.click().run()

  assert not app.exception
  assert (
    "1 of 1 extracted facts reviewed across the Corpus; "
    "1 of 1 Papers fully reviewed." in _captions(app)
  )
  assert any("Every extracted fact" in block.value for block in app.success)


class _FakePromote:
  """Stands in for `subprocess.run`, recording the CLI command each launch ran."""

  def __init__(self, returncode: int = 0, stderr: str = "") -> None:
    self.returncode = returncode
    self.stderr = stderr
    self.commands: list[list[str]] = []

  def __call__(self, args: list[str], **_kwargs: Any) -> subprocess.CompletedProcess:
    self.commands.append(list(args))
    return subprocess.CompletedProcess(args, self.returncode, "", self.stderr)


def _click_promote(app: AppTest) -> None:
  promote = next(b for b in app.button if b.label == VERIFY_PROMOTE_BUTTON)
  promote.click().run()


def test_verify_page_promotes_to_gold_through_the_runner(
  tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
  """Promote runs the CLI's promote-gold seam command, without needing a key."""
  corpus = tmp_path / "corpus"
  _build_corpus(corpus)
  fake = _FakePromote()
  monkeypatch.setattr(pipeline_runner.subprocess, "run", fake)
  monkeypatch.setenv("ANTHROPIC_API_KEY", "")

  app = _run_verify_page(tmp_path, monkeypatch)
  _click_promote(app)

  assert not app.exception
  assert [command[3:] for command in fake.commands] == [
    ["promote-gold", str(corpus.resolve())]
  ]
  assert RUN_SUCCEEDED.format(stage="promote-gold") in [s.value for s in app.success]


def test_verify_page_shows_a_refused_promotion_with_its_reason(
  tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
  """The strict refusal's reason is in the error itself, not only in the output."""
  _build_corpus(tmp_path / "corpus")
  reason = (
    "Gold promotion refused, nothing written. Paper 'paper-a': Evidence in "
    "'methods': passage not found in the paper text."
  )
  fake = _FakePromote(returncode=1, stderr=f"Building\n{reason}\n")
  monkeypatch.setattr(pipeline_runner.subprocess, "run", fake)

  app = _run_verify_page(tmp_path, monkeypatch)
  _click_promote(app)

  assert not app.exception
  errors = [e.value for e in app.error]
  assert RUN_FAILED_WITH_REASON.format(stage="promote-gold", reason=reason) in errors
