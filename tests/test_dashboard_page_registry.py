"""
Behaviour of the page registry the dashboard shell dispatches through.

The registry is the single list of dashboard pages: the sidebar offers exactly
its pages, in its order, the Home screen first and then every page in the order
of the guided path's steps, and an unknown page falls back to Home.
"""

import shutil
from pathlib import Path

import research_gap_dashboard.dashboard as dashboard_pkg
from research_gap_dashboard.dashboard import text
from research_gap_dashboard.dashboard.guided_flow import Step
from research_gap_dashboard.dashboard.page_registry import PAGES, page_for
from research_gap_dashboard.dashboard.views.navigation import nav_button_key
from research_gap_dashboard.ingest import ingest_corpus

_SIDEBAR_ORDER = [
  (text.PAGE_HOME, None),
  (text.PAGE_OVERVIEW, Step.SCOPE),
  (text.PAGE_PIPELINE, Step.BUILD),
  (text.PAGE_ATTENTION, Step.TRUST),
  (text.PAGE_VERIFY, Step.TRUST),
  (text.PAGE_META, Step.EXPLORE),
  (text.PAGE_LIMITATIONS, Step.EXPLORE),
  (text.PAGE_GAP_CARDS, Step.JUDGE),
  (text.PAGE_EXPLAINER, Step.JUDGE),
  (text.PAGE_COMPARISON, Step.JUDGE),
  (text.PAGE_RETRIEVAL, Step.GROW),
  (text.PAGE_ANALYTICS, Step.WRITE),
]


def test_registry_lists_every_page_once_in_sidebar_order():
  """Every page is registered exactly once, in step order, under its step."""
  assert [(page.title, page.step) for page in PAGES] == _SIDEBAR_ORDER


def test_unknown_page_falls_back_to_home():
  """A title the registry doesn't know dispatches to the Home screen."""
  assert page_for("No such page").title == text.PAGE_HOME
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
  for page in PAGES:
    assert app.button(key=nav_button_key(page.title)).label.endswith(page.title)
