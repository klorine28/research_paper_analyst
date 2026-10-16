"""
The soft Trust gate in the running app (issue #77).

On a Corpus where Papers fell out, Explore, Judge, Grow and Write show a lock
screen stating the denominator until the researcher fixes the fallout or
acknowledges it. The acknowledgement is remembered on disk, the incompleteness
banner stays, and a complete Corpus never sees the gate.
"""

from pathlib import Path

import pytest
from conftest import open_page
from streamlit.testing.v1 import AppTest
from test_dashboard_guided_flow import _build, _scope

import research_gap_dashboard.dashboard as dashboard_pkg
from research_gap_dashboard.dashboard import text
from research_gap_dashboard.dashboard.trust_gate import acknowledge_denominator
from research_gap_dashboard.dashboard.views.trust_gate import (
  ACKNOWLEDGE_BUTTON_KEY,
  FIX_BUTTON_KEY,
)

GATED_PAGES = (
  text.PAGE_META,
  text.PAGE_LIMITATIONS,
  text.PAGE_GAP_CARDS,
  text.PAGE_EXPLAINER,
  text.PAGE_COMPARISON,
  text.PAGE_RETRIEVAL,
  text.PAGE_ANALYTICS,
)


def _run(root: Path, monkeypatch: pytest.MonkeyPatch) -> AppTest:
  monkeypatch.setenv("RESEARCH_GAP_CORPORA_DIR", str(root.parent))
  app_path = Path(dashboard_pkg.__file__).parent / "app.py"
  return AppTest.from_file(str(app_path), default_timeout=30).run()


def _locked(app: AppTest) -> bool:
  return text.TRUST_GATE_HEADING in [element.value for element in app.subheader]


def _banner_shown(app: AppTest) -> bool:
  return any("1/2 Papers reached" in warning.value for warning in app.warning)


@pytest.fixture(name="fallout")
def fallout_fixture(tmp_path: Path) -> Path:
  """Write a scoped, built Corpus where one of its two Papers never parsed."""
  root = tmp_path / "corpus"
  _build(root, unparsed=("b",))
  _scope(root)
  return root


@pytest.mark.parametrize("title", GATED_PAGES)
def test_gated_pages_are_locked_with_the_denominator(
  title: str, fallout: Path, monkeypatch: pytest.MonkeyPatch
):
  """Every Explore-to-Write page shows the lock screen, stating 1 of 2 Papers."""
  app = open_page(_run(fallout, monkeypatch), title)

  assert not app.exception
  assert _locked(app)
  assert "1 of 2 Papers" in app.button(key=ACKNOWLEDGE_BUTTON_KEY).label
  assert _banner_shown(app)


@pytest.mark.parametrize("title", (text.PAGE_OVERVIEW, text.PAGE_PIPELINE))
def test_steps_before_the_gate_stay_open(
  title: str, fallout: Path, monkeypatch: pytest.MonkeyPatch
):
  """Scope and Build are how fallout gets fixed, so they are never locked."""
  app = open_page(_run(fallout, monkeypatch), title)

  assert not app.exception
  assert not _locked(app)


def test_the_fix_button_opens_needs_attention(
  fallout: Path, monkeypatch: pytest.MonkeyPatch
):
  """From the lock screen, fixing the fallout is one click away."""
  app = open_page(_run(fallout, monkeypatch), text.PAGE_GAP_CARDS)

  app.button(key=FIX_BUTTON_KEY).click().run()

  assert not app.exception
  assert text.ATTENTION_HEADING in [element.value for element in app.header]


def test_acknowledging_unlocks_the_page_and_keeps_the_banner(
  fallout: Path, monkeypatch: pytest.MonkeyPatch
):
  """After acknowledging, Gap Cards renders and the banner is still there."""
  app = open_page(_run(fallout, monkeypatch), text.PAGE_GAP_CARDS)

  app.button(key=ACKNOWLEDGE_BUTTON_KEY).click().run()

  assert not app.exception
  assert not _locked(app)
  assert _banner_shown(app)
  assert (fallout / "judgments" / "trust-acknowledgement.json").is_file()


def test_an_acknowledgement_is_remembered_in_a_new_session(
  fallout: Path, monkeypatch: pytest.MonkeyPatch
):
  """A returning researcher is not asked again for the same Corpus state."""
  acknowledge_denominator(fallout)

  app = open_page(_run(fallout, monkeypatch), text.PAGE_ANALYTICS)

  assert not app.exception
  assert not _locked(app)
  assert _banner_shown(app)
  assert any("1 of 2 Papers" in caption.value for caption in app.caption)


def test_a_complete_corpus_passes_without_prompting(
  tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
  """Nothing fell out: no lock screen, no acknowledgement written, no banner."""
  root = tmp_path / "corpus"
  _build(root)
  _scope(root)

  app = open_page(_run(root, monkeypatch), text.PAGE_GAP_CARDS)

  assert not app.exception
  assert not _locked(app)
  assert not app.warning
  assert not (root / "judgments" / "trust-acknowledgement.json").exists()
