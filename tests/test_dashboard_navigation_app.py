"""
Guided navigation in the running app (issue #76).

The dashboard opens on a Home screen with Corpus health and a status-driven
recommended next step. The sidebar groups the pages under numbered steps and
phases, every page carries a step bar (completed steps ticked, the current one
highlighted) and Previous/Next buttons naming the adjacent steps. Free
navigation stays: any page opens from the sidebar whatever the Corpus state.
"""

from pathlib import Path

import pytest
from conftest import open_page
from streamlit.testing.v1 import AppTest
from test_dashboard_guided_flow import _build, _scope

import research_gap_dashboard.dashboard as dashboard_pkg
from research_gap_dashboard.dashboard import text
from research_gap_dashboard.dashboard.guided_flow import Step
from research_gap_dashboard.dashboard.page_registry import PAGES
from research_gap_dashboard.dashboard.views.home import RECOMMENDED_BUTTON_KEY
from research_gap_dashboard.dashboard.views.navigation import (
  NEXT_BUTTON_KEY,
  PREVIOUS_BUTTON_KEY,
  nav_button_key,
  step_bar_key,
)


def _run(root: Path, monkeypatch: pytest.MonkeyPatch) -> AppTest:
  monkeypatch.setenv("RESEARCH_GAP_CORPORA_DIR", str(root.parent))
  app_path = Path(dashboard_pkg.__file__).parent / "app.py"
  return AppTest.from_file(str(app_path), default_timeout=30).run()


def _headers(app: AppTest) -> list[str]:
  return [element.value for element in app.header]


@pytest.fixture(name="built")
def built_fixture(tmp_path: Path) -> Path:
  """Write a scoped Corpus whose core stages all ran and where nothing fell out."""
  root = tmp_path / "corpus"
  _build(root)
  _scope(root)
  return root


@pytest.fixture(name="fallout")
def fallout_fixture(tmp_path: Path) -> Path:
  """Write a scoped, built Corpus where one of its two Papers never parsed."""
  root = tmp_path / "corpus"
  _build(root, unparsed=("b",))
  _scope(root)
  return root


def test_the_app_opens_on_home_with_health_and_a_recommendation(
  fallout: Path, monkeypatch: pytest.MonkeyPatch
):
  """Home states the denominator and recommends Trust when a Paper fell out."""
  app = _run(fallout, monkeypatch)

  assert not app.exception
  assert _headers(app) == [text.HOME_HEADING]
  metrics = {metric.label: metric.value for metric in app.metric}
  assert metrics[text.HOME_METRIC_REACHED] == "1 / 2"
  assert metrics[text.HOME_METRIC_GAPS] == "2"
  recommended = app.button(key=RECOMMENDED_BUTTON_KEY)
  assert "3 · Trust" in recommended.label


def test_the_recommended_step_button_opens_that_steps_first_page(
  fallout: Path, monkeypatch: pytest.MonkeyPatch
):
  """Following the recommendation lands on the Trust step's Needs Attention page."""
  app = _run(fallout, monkeypatch)

  app.button(key=RECOMMENDED_BUTTON_KEY).click().run()

  assert not app.exception
  assert text.ATTENTION_HEADING in _headers(app)


def test_the_sidebar_numbers_the_steps_and_groups_them_into_phases(
  built: Path, monkeypatch: pytest.MonkeyPatch
):
  """Phases and numbered steps appear in path order, with every page under one."""
  app = _run(built, monkeypatch)

  captions = [caption.value for caption in app.sidebar.caption]
  assert captions[-4:] == ["SET UP", "CHECK", "ANALYSE", "EXTEND"]
  steps = [markdown.value for markdown in app.sidebar.markdown]
  assert steps == [
    "**✓ 1 · Scope**",
    "**✓ 2 · Build**",
    "**✓ 3 · Trust**",
    "**4 · Explore**",
    "**5 · Judge**",
    "**6 · Grow**",
    "**7 · Write**",
  ]
  for page in PAGES:
    assert app.button(key=nav_button_key(page.title))


def test_the_step_bar_marks_completed_steps_and_the_current_one(
  built: Path, monkeypatch: pytest.MonkeyPatch
):
  """On the Pipeline page, Build is current and Scope to Trust are ticked."""
  app = open_page(_run(built, monkeypatch), text.PAGE_PIPELINE)

  bar = {step: app.button(key=step_bar_key(step)) for step in Step}
  assert bar[Step.SCOPE].label == "✓ 1 · Scope"
  assert bar[Step.TRUST].label == "✓ 3 · Trust"
  assert bar[Step.EXPLORE].label == "4 · Explore"
  assert bar[Step.BUILD].proto.type == "primary"
  assert bar[Step.SCOPE].proto.type == "secondary"


def test_previous_and_next_name_the_adjacent_steps_and_move_between_them(
  built: Path, monkeypatch: pytest.MonkeyPatch
):
  """From Build, Next goes to Trust and Previous comes back to Build."""
  app = open_page(_run(built, monkeypatch), text.PAGE_PIPELINE)
  assert app.button(key=PREVIOUS_BUTTON_KEY).label == "← Previous: 1 · Scope"
  assert app.button(key=NEXT_BUTTON_KEY).label == "Next: 3 · Trust →"

  app.button(key=NEXT_BUTTON_KEY).click().run()
  assert text.ATTENTION_HEADING in _headers(app)

  app.button(key=PREVIOUS_BUTTON_KEY).click().run()
  assert text.PIPELINE_HEADING in _headers(app)


def test_the_first_step_goes_back_home_and_the_last_has_no_next(
  built: Path, monkeypatch: pytest.MonkeyPatch
):
  """Before Scope is Home; Write is the end of the path."""
  app = _run(built, monkeypatch)
  assert app.button(key=NEXT_BUTTON_KEY).label == "Next: 1 · Scope →"

  app.button(key=step_bar_key(Step.WRITE)).click().run()
  assert not app.exception
  assert NEXT_BUTTON_KEY not in [button.key for button in app.button]

  open_page(app, text.PAGE_OVERVIEW)
  app.button(key=PREVIOUS_BUTTON_KEY).click().run()
  assert _headers(app) == [text.HOME_HEADING]


def test_any_page_opens_from_the_sidebar_whatever_the_corpus_state(
  fallout: Path, monkeypatch: pytest.MonkeyPatch
):
  """Free navigation: Gap Cards opens even though Trust still needs work."""
  app = open_page(_run(fallout, monkeypatch), text.PAGE_GAP_CARDS)

  assert not app.exception
  assert app.button(key=step_bar_key(Step.JUDGE)).proto.type == "primary"
