"""
Resume where you left off (issue #83).

A Corpus is reviewed over months. The dashboard remembers, alongside the
judgments, the furthest step reached on each Corpus and when it was last
opened, so reopening the app returns to the last Corpus and its Home screen
offers to resume at the furthest step, with the verdicts and journal intact.
"""

from datetime import datetime, timezone
from pathlib import Path

import pytest
from conftest import open_page
from streamlit.testing.v1 import AppTest
from test_dashboard_guided_flow import _build, _scope

import research_gap_dashboard.dashboard as dashboard_pkg
from research_gap_dashboard.dashboard import text
from research_gap_dashboard.dashboard.guided_flow import Step
from research_gap_dashboard.dashboard.journal import promote_to_journal
from research_gap_dashboard.dashboard.judgments import record_judgment
from research_gap_dashboard.dashboard.resume import (
  last_opened_corpus,
  load_resume_point,
  record_visit,
)
from research_gap_dashboard.dashboard.views.home import RESUME_BUTTON_KEY
from research_gap_dashboard.dashboard.views.session_keys import CORPUS_PICKER_KEY

_JAN = datetime(2026, 1, 1, tzinfo=timezone.utc)
_FEB = datetime(2026, 2, 1, tzinfo=timezone.utc)


def test_a_corpus_never_opened_has_no_resume_point(tmp_path: Path):
  """Nothing recorded reads as no furthest step and no last opening."""
  point = load_resume_point(tmp_path)

  assert point.furthest_step is None
  assert point.last_opened_at is None


def test_the_furthest_step_survives_a_reload_and_never_moves_back(tmp_path: Path):
  """Going back to an earlier step keeps the furthest one reached."""
  record_visit(tmp_path, Step.JUDGE, now=_JAN)
  record_visit(tmp_path, Step.TRUST, now=_FEB)

  point = load_resume_point(tmp_path)
  assert point.furthest_step == Step.JUDGE
  assert point.last_opened_at == _FEB


def test_the_resume_point_lives_alongside_the_judgments(tmp_path: Path):
  """It is a researcher-review record under judgments/, not a pipeline artifact."""
  record_visit(tmp_path, Step.BUILD)

  assert (tmp_path / "judgments" / "resume_point.json").is_file()


def test_visiting_home_records_the_opening_but_no_step(tmp_path: Path):
  """The Home screen belongs to no step, so it never sets the furthest step."""
  record_visit(tmp_path, None, now=_JAN)

  point = load_resume_point(tmp_path)
  assert point.furthest_step is None
  assert point.last_opened_at == _JAN


def test_the_last_corpus_is_the_one_opened_most_recently(tmp_path: Path):
  """Among the Corpora, the latest opening wins; unopened ones never do."""
  first, second, never = tmp_path / "a", tmp_path / "b", tmp_path / "c"
  record_visit(first, Step.SCOPE, now=_FEB)
  record_visit(second, Step.WRITE, now=_JAN)

  assert last_opened_corpus([first, second, never]) == first
  assert last_opened_corpus([never]) is None


def _run(corpora_dir: Path, monkeypatch: pytest.MonkeyPatch) -> AppTest:
  monkeypatch.setenv("RESEARCH_GAP_CORPORA_DIR", str(corpora_dir))
  app_path = Path(dashboard_pkg.__file__).parent / "app.py"
  return AppTest.from_file(str(app_path), default_timeout=30).run()


@pytest.fixture(name="corpora")
def corpora_fixture(tmp_path: Path) -> Path:
  """Write two built, scoped Corpora; the picker lists 'alpha' first."""
  for name in ("alpha", "beta"):
    _build(tmp_path / name)
    _scope(tmp_path / name)
  return tmp_path


def test_reopening_returns_to_the_last_corpus_and_offers_to_resume(
  corpora: Path, monkeypatch: pytest.MonkeyPatch
):
  """After a restart the last Corpus is open and Home resumes at its furthest step."""
  app = _run(corpora, monkeypatch)
  app.sidebar.selectbox(key=CORPUS_PICKER_KEY).select("beta").run()
  open_page(app, text.PAGE_GAP_CARDS)
  open_page(app, text.PAGE_ATTENTION)

  restarted = _run(corpora, monkeypatch)

  assert not restarted.exception
  assert restarted.sidebar.selectbox(key=CORPUS_PICKER_KEY).value == "beta"
  assert [header.value for header in restarted.header] == [text.HOME_HEADING]
  resume = restarted.button(key=RESUME_BUTTON_KEY)
  assert "5 · Judge" in resume.label

  resume.click().run()
  assert text.GAP_CARDS_HEADING in [header.value for header in restarted.header]


def test_the_resume_offer_states_the_saved_verdicts_and_journal(
  corpora: Path, monkeypatch: pytest.MonkeyPatch
):
  """The researcher sees their verdicts and journal entries are still there."""
  root = corpora / "alpha"
  record_visit(root, Step.JUDGE)
  record_judgment(root, "gap-1", "accepted")
  promote_to_journal(
    root,
    note="A note worth keeping.",
    source_gap_id="gap-1",
    source_citation_keys=["a"],
  )

  app = _run(corpora, monkeypatch)

  offer = " ".join(markdown.value for markdown in app.markdown)
  assert text.HOME_RESUME_SAVED.format(verdicts=1, entries=1) in offer


def test_home_offers_no_resume_on_a_corpus_never_worked_on(
  corpora: Path, monkeypatch: pytest.MonkeyPatch
):
  """A first visit has nowhere to resume to."""
  app = _run(corpora, monkeypatch)

  assert RESUME_BUTTON_KEY not in [button.key for button in app.button]
