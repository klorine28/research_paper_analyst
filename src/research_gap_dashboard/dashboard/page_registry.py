"""
The page registry: the one list of dashboard pages the shell dispatches through.

Each entry pairs a page's sidebar title with the function that renders it for a
chosen Corpus and the step of the guided path it belongs to (issue #76). Order
here is the sidebar order: the Home screen first, then every page in step order.
A step's first page is where the step bar, Previous/Next and the home screen's
recommendation take the researcher. Adding a page means adding a module under
`dashboard.views` and one entry here; the shell does not change.
"""

from collections.abc import Callable
from dataclasses import dataclass

from research_gap_dashboard.dashboard import text
from research_gap_dashboard.dashboard.artifacts import CorpusChoice
from research_gap_dashboard.dashboard.guided_flow import Step
from research_gap_dashboard.dashboard.views import (
  analytics,
  attention,
  comparison,
  explainer,
  gap_cards,
  home,
  limitations,
  meta_analysis,
  overview,
  pipeline,
  retrieval,
  verify,
)


@dataclass(frozen=True)
class Page:
  """One dashboard page: its title, how to render it, and its step (None: Home)."""

  title: str
  render: Callable[[CorpusChoice], None]
  step: Step | None


PAGES: tuple[Page, ...] = (
  Page(text.PAGE_HOME, home.render_page, None),
  Page(text.PAGE_OVERVIEW, overview.render_page, Step.SCOPE),
  Page(text.PAGE_PIPELINE, pipeline.render_page, Step.BUILD),
  Page(text.PAGE_ATTENTION, attention.render_page, Step.TRUST),
  Page(text.PAGE_VERIFY, verify.render_page, Step.TRUST),
  Page(text.PAGE_META, meta_analysis.render_page, Step.EXPLORE),
  Page(text.PAGE_LIMITATIONS, limitations.render_page, Step.EXPLORE),
  Page(text.PAGE_GAP_CARDS, gap_cards.render_page, Step.JUDGE),
  Page(text.PAGE_EXPLAINER, explainer.render_page, Step.JUDGE),
  Page(text.PAGE_COMPARISON, comparison.render_page, Step.JUDGE),
  Page(text.PAGE_RETRIEVAL, retrieval.render_page, Step.GROW),
  Page(text.PAGE_ANALYTICS, analytics.render_page, Step.WRITE),
)

_BY_TITLE = {page.title: page for page in PAGES}


def page_for(title: str) -> Page:
  """Return the page with this title, falling back to the first (the Home screen)."""
  return _BY_TITLE.get(title, PAGES[0])


def pages_for_step(step: Step) -> list[Page]:
  """Return a step's pages in sidebar order."""
  return [page for page in PAGES if page.step == step]


def first_page_title(step: Step) -> str:
  """Return the title of the page a step opens on."""
  return pages_for_step(step)[0].title
