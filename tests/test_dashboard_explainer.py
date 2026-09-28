"""
Behavior of the Paper Explainer view: per-Paper explanation files in, view out.

The explain stage writes one explanation file per Paper under paper-data/; these
tests build real files with the stage and read them back through the dashboard's
own reader, so a schema drift between writer and reader would surface here. The
page pairs each explanation with the Paper's manifest metadata (title, venue,
DOI) so a reader can link back to the Paper.
"""

import shutil
from pathlib import Path

import pytest

from conftest import StubLlmClient

import research_gap_dashboard.dashboard as dashboard_pkg

from research_gap_dashboard.dashboard.artifacts import (
  ArtifactNotFoundError,
  explained_citation_keys,
  has_paper_explanation,
  load_manifest,
  load_paper_explanation,
)
from research_gap_dashboard.dashboard.explainer import (
  build_paper_explainer,
  build_paper_menu,
)
from research_gap_dashboard.dashboard.text import PAGE_EXPLAINER
from research_gap_dashboard.explain import explain_corpus
from research_gap_dashboard.ingest import ingest_corpus, read_manifest
from research_gap_dashboard.parsing import PARSED_SUFFIX, ParsedPaper, ParsedSection

_BOTH_REGISTERS = {
  "domain_explanation": "A retrospective cohort assessed mortality at 12%.",
  "lay_explanation": "Researchers reviewed records and found 12% of patients died.",
}


def _write_parsed(corpus: Path) -> None:
  """Write distinct parsed text for every ingested Paper so explain can run."""
  paper_data = corpus / "paper-data"
  for paper in read_manifest(corpus).papers:
    parsed = ParsedPaper(
      source_pdf=paper.pdf_path,
      sections=[
        ParsedSection(
          label="abstract",
          heading="Abstract",
          text=f"Study {paper.citation_key} of outcomes.",
        )
      ],
    )
    (paper_data / f"{paper.citation_key}{PARSED_SUFFIX}").write_text(
      parsed.model_dump_json(indent=2), encoding="utf-8"
    )


@pytest.fixture(name="corpus")
def corpus_fixture(cardiology_corpus: Path, tmp_path: Path) -> Path:
  """Ingest the fixture Corpus, parse it, and run explain over every Paper."""
  target = tmp_path / "corpus"
  shutil.copytree(cardiology_corpus, target)
  ingest_corpus(target)
  _write_parsed(target)
  explain_corpus(target, StubLlmClient(_BOTH_REGISTERS))
  return target


def test_menu_lists_every_paper_and_flags_the_explained(corpus: Path) -> None:
  """The picker offers every Paper and marks which ones have an explanation."""
  menu = build_paper_menu(load_manifest(corpus), explained_citation_keys(corpus))

  assert menu.paper_count == 10
  assert menu.explained_count == 10
  assert all(entry.has_explanation for entry in menu.entries)


def test_menu_flags_a_paper_without_an_explanation(corpus: Path) -> None:
  """A Paper whose explanation file is absent is offered but flagged unexplained."""
  key = load_manifest(corpus).papers[0].citation_key
  (corpus / "paper-data" / f"{key}.explanation.json").unlink()

  menu = build_paper_menu(load_manifest(corpus), explained_citation_keys(corpus))

  by_key = {entry.citation_key: entry for entry in menu.entries}
  assert menu.paper_count == 10
  assert menu.explained_count == 9
  assert not by_key[key].has_explanation


def test_explainer_view_carries_both_registers_and_metadata(corpus: Path) -> None:
  """The view pairs both register explanations with the Paper's link-back metadata."""
  manifest = load_manifest(corpus)
  paper = manifest.papers[0]

  view = build_paper_explainer(
    paper, load_paper_explanation(corpus, paper.citation_key)
  )

  assert view.citation_key == paper.citation_key
  assert view.doi == paper.doi
  assert view.domain_explanation == _BOTH_REGISTERS["domain_explanation"]
  assert view.lay_explanation == _BOTH_REGISTERS["lay_explanation"]


def test_load_explanation_without_a_file_is_a_clear_error(corpus: Path) -> None:
  """Opening a Paper that was never explained names what to run."""
  with pytest.raises(ArtifactNotFoundError, match="explain"):
    load_paper_explanation(corpus, "never-explained")


def test_has_paper_explanation_reports_presence(corpus: Path) -> None:
  """The presence check is true for an explained Paper, false otherwise."""
  key = load_manifest(corpus).papers[0].citation_key

  assert has_paper_explanation(corpus, key)
  assert not has_paper_explanation(corpus, "never-explained")


def test_app_renders_the_explainer_page(corpus: Path, monkeypatch) -> None:
  """The Explainer page shows both registers for the selected Paper."""
  from streamlit.testing.v1 import AppTest

  monkeypatch.setenv("RESEARCH_GAP_CORPORA_DIR", str(corpus.parent))
  app_path = Path(dashboard_pkg.__file__).parent / "app.py"
  app = AppTest.from_file(str(app_path), default_timeout=30).run()
  app.sidebar.radio[0].set_value(PAGE_EXPLAINER).run()

  assert not app.exception
  body = " ".join(block.value for block in app.markdown).lower()
  assert "domain language" in body
  assert "plain language" in body
