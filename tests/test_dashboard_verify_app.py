"""
The Verify Extractions page renders end to end in the Streamlit shell.

The offline seams (persistence, grounding, assembly) are covered directly; this
smoke test wires them through the app so a schema drift between the artifact
readers and the page would surface here. It builds a minimal Corpus with a
manifest, an Extraction, and parsed text, then drives the page's navigation.
"""

import json
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

import research_gap_dashboard.dashboard as dashboard_pkg
from research_gap_dashboard.dashboard.text import PAGE_VERIFY, VERIFY_HEADING


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
  app.radio[0].set_value(PAGE_VERIFY).run()
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
