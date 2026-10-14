"""
Behaviour of the page registry the dashboard shell dispatches through.

The registry is the single list of dashboard pages: the sidebar offers exactly
its pages, in its order, and an unknown page falls back to the first one.
"""

import shutil
from pathlib import Path

import research_gap_dashboard.dashboard as dashboard_pkg
from research_gap_dashboard.dashboard import text
from research_gap_dashboard.dashboard.page_registry import PAGES, page_for
from research_gap_dashboard.ingest import ingest_corpus

_SIDEBAR_ORDER = [
  text.PAGE_OVERVIEW,
  text.PAGE_META,
  text.PAGE_GAP_CARDS,
  text.PAGE_LIMITATIONS,
  text.PAGE_RETRIEVAL,
  text.PAGE_EXPLAINER,
  text.PAGE_COMPARISON,
  text.PAGE_ANALYTICS,
  text.PAGE_VERIFY,
  text.PAGE_ATTENTION,
]


def test_registry_lists_every_page_once_in_sidebar_order():
  """Every dashboard page is registered exactly once, in the sidebar's order."""
  assert [page.title for page in PAGES] == _SIDEBAR_ORDER


def test_unknown_page_falls_back_to_the_overview():
  """A title the registry doesn't know dispatches to the Corpus Overview."""
  assert page_for("No such page").title == text.PAGE_OVERVIEW
  assert page_for(text.PAGE_VERIFY).title == text.PAGE_VERIFY


def test_sidebar_offers_the_registered_pages(
  cardiology_corpus: Path, tmp_path: Path, monkeypatch
):
  """The shell's page picker is built from the registry, nothing else."""
  from streamlit.testing.v1 import AppTest

  corpus = tmp_path / "corpus"
  shutil.copytree(cardiology_corpus, corpus)
  ingest_corpus(corpus)
  monkeypatch.setenv("RESEARCH_GAP_CORPORA_DIR", str(tmp_path))
  app_path = Path(dashboard_pkg.__file__).parent / "app.py"
  app = AppTest.from_file(str(app_path), default_timeout=30).run()

  assert not app.exception
  assert list(app.radio[0].options) == [page.title for page in PAGES]
