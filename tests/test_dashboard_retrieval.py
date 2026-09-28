"""
Behavior of the Retrieval Gaps view: RetrievalGaps artifact in, candidates out.

The dashboard reads the artifact the retrieve stage writes, so these tests build
a real artifact with detect_retrieval_gaps and then read it back through the
dashboard's own reader: a schema drift between writer and reader would surface
here. The view must carry the out-of-corpus label and every candidate's citation
overlap, and stay separate from the evidence-linked gap cards.
"""

from pathlib import Path

import pytest

from test_retrieval import _PAPERS, _WORKS, _openalex, _write_manifest

import research_gap_dashboard.dashboard as dashboard_pkg

from research_gap_dashboard.dashboard.artifacts import load_retrieval_gaps
from research_gap_dashboard.dashboard.retrieval import build_retrieval_gaps
from research_gap_dashboard.retrieval import OUT_OF_CORPUS_LABEL, detect_retrieval_gaps


@pytest.fixture(name="corpus")
def corpus_fixture(tmp_path: Path) -> Path:
  """
  Write a synthetic Corpus and run retrieval so a RetrievalGaps artifact exists.

  The corpus lives in its own subdirectory so its parent holds only this Corpus,
  keeping the app's corpus picker deterministic under the shared pytest tmp dir.
  """
  root = tmp_path / "corpus"
  root.mkdir()
  _write_manifest(root, _PAPERS)
  detect_retrieval_gaps(root, _openalex(_WORKS), min_overlap=1)
  return root


def test_every_candidate_becomes_a_view(corpus: Path):
  """The view lists one candidate per out-of-corpus candidate the artifact holds."""
  artifact = load_retrieval_gaps(corpus)
  data = build_retrieval_gaps(artifact)

  assert data.candidate_count == len(artifact.candidates)
  assert data.candidate_count > 0


def test_candidates_keep_the_ranked_order_and_overlap(corpus: Path):
  """Candidates stay ranked by citation overlap, each with the Papers citing it."""
  data = build_retrieval_gaps(load_retrieval_gaps(corpus))

  assert [c.title for c in data.candidates][:2] == ["Shared A", "Shared B"]
  first = data.candidates[0]
  assert first.citation_overlap == 3
  assert first.citing_citation_keys == ["p01", "p02", "p03"]


def test_the_view_carries_the_out_of_corpus_label(corpus: Path):
  """The view keeps the artifact's out-of-corpus banner and its denominators."""
  data = build_retrieval_gaps(load_retrieval_gaps(corpus))

  assert data.label == OUT_OF_CORPUS_LABEL
  assert data.source == "openalex"
  assert data.corpus_paper_count == 4
  assert data.coupled_paper_count == 4


def test_no_candidate_exposes_in_corpus_evidence(corpus: Path):
  """Out-of-corpus candidates expose no Evidence field to link into the Corpus."""
  from research_gap_dashboard.dashboard.retrieval import RetrievalCandidateView

  assert "evidence" not in RetrievalCandidateView.model_json_schema()["properties"]


def _run_retrieval_app(corpus: Path, monkeypatch: pytest.MonkeyPatch):
  """Run the Streamlit shell on the Retrieval Gaps page for a corpus directory."""
  from streamlit.testing.v1 import AppTest

  monkeypatch.setenv("RESEARCH_GAP_CORPORA_DIR", str(corpus.parent))
  app_path = Path(dashboard_pkg.__file__).parent / "app.py"
  app = AppTest.from_file(str(app_path), default_timeout=30).run()
  app.radio[0].set_value("Retrieval Gaps").run()
  return app


def test_retrieval_page_renders_and_is_separated(
  corpus: Path, monkeypatch: pytest.MonkeyPatch
):
  """The page shows candidates and warns they are not evidence-linked gaps."""
  app = _run_retrieval_app(corpus, monkeypatch)

  assert not app.exception
  assert "Retrieval Gaps (out-of-corpus)" in [header.value for header in app.header]
  warnings = " ".join(warning.value for warning in app.warning)
  assert "not evidence-linked gaps" in warnings
