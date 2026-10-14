"""
The page registry: the one list of dashboard pages the shell dispatches through.

Each entry pairs a page's sidebar title with the function that renders it for a
chosen Corpus. Order here is the sidebar order. Adding a page means adding a
module under `dashboard.views` and one entry here; the shell does not change.
"""

from collections.abc import Callable
from dataclasses import dataclass

from research_gap_dashboard.dashboard import text
from research_gap_dashboard.dashboard.artifacts import CorpusChoice
from research_gap_dashboard.dashboard.views import (
  analytics,
  attention,
  comparison,
  explainer,
  gap_cards,
  limitations,
  meta_analysis,
  overview,
  retrieval,
  verify,
)


@dataclass(frozen=True)
class Page:
  """One dashboard page: its sidebar title and how to render it for a Corpus."""

  title: str
  render: Callable[[CorpusChoice], None]


PAGES: tuple[Page, ...] = (
  Page(text.PAGE_OVERVIEW, overview.render_page),
  Page(text.PAGE_META, meta_analysis.render_page),
  Page(text.PAGE_GAP_CARDS, gap_cards.render_page),
  Page(text.PAGE_LIMITATIONS, limitations.render_page),
  Page(text.PAGE_RETRIEVAL, retrieval.render_page),
  Page(text.PAGE_EXPLAINER, explainer.render_page),
  Page(text.PAGE_COMPARISON, comparison.render_page),
  Page(text.PAGE_ANALYTICS, analytics.render_page),
  Page(text.PAGE_VERIFY, verify.render_page),
  Page(text.PAGE_ATTENTION, attention.render_page),
)

_BY_TITLE = {page.title: page for page in PAGES}


def page_titles() -> list[str]:
  """Return every page title in sidebar order."""
  return [page.title for page in PAGES]


def page_for(title: str) -> Page:
  """Return the page with this title, falling back to the first (the Overview)."""
  return _BY_TITLE.get(title, PAGES[0])
