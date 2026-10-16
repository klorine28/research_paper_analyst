"""
The saturation check on the Gap Cards page (ADR 0009 §14, issue #85).

Read-model tests build cards over the saturation fixture Corpus after real
checks against recorded responses; `AppTest` drives the card's badge, its
queries/hits drill-down, and the "Check wider literature" button, whose
subprocess is patched so the real CLI never runs.
"""

from conftest import open_page
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from test_saturate import (
  CONTROL_GAP,
  FILLED_GAP,
  FILLED_LIMITATION,
  FIXTURE,
  SCREENING_MODEL,
  VARIANT_HIT,
  VARIANTS,
  _check,
  _FakeVariantLlm,
  _recorded_screener,
  _variant_check,
)

from research_gap_dashboard.dashboard.trust_gate import acknowledge_denominator
import research_gap_dashboard.dashboard as dashboard_pkg
from research_gap_dashboard.dashboard import pipeline_runner, text
from research_gap_dashboard.dashboard.artifacts import (
  load_candidate_gaps,
  load_saturation_checks,
)
from research_gap_dashboard.dashboard.gaps import GapCard, build_gap_cards
from research_gap_dashboard.dashboard.judgments import load_judgments
from research_gap_dashboard.saturate import QUERY_VARIANT_PROMPT_VERSION


@pytest.fixture(name="corpus")
def corpus_fixture(tmp_path: Path) -> Path:
  """Copy the saturation fixture Corpus, alone in its folder, to write into."""
  return Path(shutil.copytree(FIXTURE, tmp_path / "corpora" / "saturation-corpus"))


def _cards(corpus: Path) -> dict[str, GapCard]:
  """Build the Gap Cards read model the page renders, keyed by gap id."""
  data = build_gap_cards(
    load_candidate_gaps(corpus),
    load_judgments(corpus),
    saturation=load_saturation_checks(corpus),
  )
  return {card.gap_id: card for card in data.cards}


def test_an_unchecked_gap_reads_not_checked(corpus: Path):
  """Before any check, a cell gap's badge says "not checked"."""
  card = _cards(corpus)[FILLED_GAP]

  assert card.saturation.checkable
  assert card.saturation.status == "not_checked"
  assert card.saturation.badge == text.GAP_SATURATION_BADGES["not_checked"]


def test_a_possibly_addressed_badge_counts_screened_hits_and_dates_them(corpus: Path):
  """The badge carries the screened-hit count and the check date."""
  check = _check(corpus, FILLED_GAP, now=lambda: datetime(2026, 10, 4, tzinfo=UTC))

  view = _cards(corpus)[FILLED_GAP].saturation

  assert view.status == "possibly_addressed"
  assert view.badge == text.GAP_SATURATION_BADGES["possibly_addressed"].format(
    count=check.screened_hit_count, date="2026-10-04"
  )
  assert view.screened_count == check.screened_hit_count
  assert len(view.hits) == len(check.hits)


def test_no_closing_work_found_lists_sources_and_queries(corpus: Path):
  """The drill-down keeps every verbatim query, per source, with the date."""
  check = _check(corpus, CONTROL_GAP, now=lambda: datetime(2026, 10, 4, tzinfo=UTC))

  view = _cards(corpus)[CONTROL_GAP].saturation

  assert view.badge == text.GAP_SATURATION_BADGES["no_closing_work_found"].format(
    date="2026-10-04"
  )
  assert [(s.source, s.query) for s in view.searches] == [
    (s.source, s.query) for s in check.searches
  ]
  assert view.summary == check.status_reason


def test_history_lists_every_check_newest_first(corpus: Path):
  """A re-check adds to the history; the badge shows the latest snapshot."""
  times = iter([datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 3, 1, tzinfo=UTC)])
  _check(corpus, CONTROL_GAP, now=lambda: next(times))
  _check(corpus, CONTROL_GAP, now=lambda: next(times))

  view = _cards(corpus)[CONTROL_GAP].saturation

  assert len(view.history) == 2
  assert view.history[0].startswith("- 2026-03-01")
  assert "2026-03-01" in view.badge


def test_other_cards_are_unaffected_by_a_check(corpus: Path):
  """A check on one gap changes no other card, and no confidence."""
  before = _cards(corpus)
  _check(corpus, FILLED_GAP)

  after = _cards(corpus)

  assert after[CONTROL_GAP].saturation.status == "not_checked"
  assert {k: c.confidence for k, c in after.items()} == {
    k: c.confidence for k, c in before.items()
  }


def test_a_limitation_card_gets_the_same_badge_and_drill_down(corpus: Path):
  """An Unanswered Limitation is checkable, with the cell gaps' badge and queries."""
  assert _cards(corpus)[FILLED_LIMITATION].saturation.checkable
  check = _check(
    corpus, FILLED_LIMITATION, now=lambda: datetime(2026, 10, 4, tzinfo=UTC)
  )

  view = _cards(corpus)[FILLED_LIMITATION].saturation

  assert view.badge == text.GAP_SATURATION_BADGES["possibly_addressed"].format(
    count=check.screened_hit_count, date="2026-10-04"
  )
  assert [s.query for s in view.searches] == [s.query for s in check.searches]
  assert view.screened_count == check.screened_hit_count
  assert view.screening_method == check.screening_method
  older = [hit for hit in view.hits if hit.reason.startswith("Published in")]
  assert older
  assert not any(hit.screened for hit in older)


def test_a_retrieval_gap_card_is_never_checkable(corpus: Path):
  """Retrieval Gaps are out of the Corpus already: no badge, no button."""
  artifact = load_candidate_gaps(corpus)
  record = artifact.gaps[0].model_copy(update={"gap_type": "retrieval_gap"})
  artifact = artifact.model_copy(update={"gaps": [record]})

  card = build_gap_cards(artifact, load_judgments(corpus)).cards[0]

  assert not card.saturation.checkable


def test_hits_show_their_llm_verdict_and_the_count_drops_ruled_out_hits(
  corpus: Path,
):
  """LLM-screened hits carry the verdict and reason; a "no" stops counting."""
  check = _check(corpus, FILLED_GAP, screener=_recorded_screener())

  view = _cards(corpus)[FILLED_GAP].saturation

  assert view.screened_count == check.screened_hit_count
  assert str(check.screened_hit_count) in view.badge
  screened = [h for h in view.hits if h.llm_verdict]
  assert len(screened) == check.llm_screening.screened_count  # type: ignore[union-attr]
  ruled_out = next(
    h for h in view.hits if h.llm_verdict == text.GAP_SATURATION_LLM_VERDICTS["no"]
  )
  assert not ruled_out.screened
  assert ruled_out.llm_reason
  assert SCREENING_MODEL in view.llm_screening


def test_a_check_without_llm_screening_says_so(corpus: Path):
  """No LLM: the drill-down says screening did not run, and why."""
  _check(corpus, FILLED_GAP)

  view = _cards(corpus)[FILLED_GAP].saturation

  assert "ANTHROPIC_API_KEY" in view.llm_screening
  assert all(not hit.llm_verdict for hit in view.hits)


# --- The page -------------------------------------------------------------------


class _FakeRun:  # pylint: disable=too-few-public-methods
  """Stands in for `subprocess.run`, recording each argv."""

  def __init__(self) -> None:
    self.calls: list[list[str]] = []

  def __call__(self, args: list[str], **_kwargs: Any) -> subprocess.CompletedProcess:
    self.calls.append(list(args))
    return subprocess.CompletedProcess(args, 0, "", "")


def _open_all_gap_cards(corpus: Path, monkeypatch: pytest.MonkeyPatch):
  """Open the Gap Cards page on the fixture with every confidence level shown."""
  from streamlit.testing.v1 import AppTest

  # Past the soft Trust gate (#77): this fixture Corpus is partial.
  acknowledge_denominator(corpus)
  monkeypatch.setenv("RESEARCH_GAP_CORPORA_DIR", str(corpus.parent))
  app_path = Path(dashboard_pkg.__file__).parent / "app.py"
  app = AppTest.from_file(str(app_path), default_timeout=30).run()
  open_page(app, text.PAGE_GAP_CARDS)
  app.multiselect[0].set_value(["high", "medium", "low"]).run()
  return app


def test_the_card_button_runs_saturate_for_its_gap(
  corpus: Path, monkeypatch: pytest.MonkeyPatch
):
  """The card button runs `saturate <corpus> --gap <id>` through the runner."""
  fake = _FakeRun()
  monkeypatch.setattr(pipeline_runner.subprocess, "run", fake)
  app = _open_all_gap_cards(corpus, monkeypatch)
  assert not app.exception

  button = next(b for b in app.button if b.key == f"saturate-{FILLED_GAP}")
  assert button.label == text.GAP_SATURATION_BUTTON
  button.click().run()

  assert not app.exception
  assert fake.calls[0][3:] == ["saturate", str(corpus.resolve()), "--gap", FILLED_GAP]
  assert text.RUN_SUCCEEDED.format(stage="saturate") in [s.value for s in app.success]


def test_a_limitation_card_button_runs_saturate_for_its_gap(
  corpus: Path, monkeypatch: pytest.MonkeyPatch
):
  """A limitation card has the same "Check wider literature" button."""
  fake = _FakeRun()
  monkeypatch.setattr(pipeline_runner.subprocess, "run", fake)
  app = _open_all_gap_cards(corpus, monkeypatch)

  button = next(b for b in app.button if b.key == f"saturate-{FILLED_LIMITATION}")
  button.click().run()

  assert not app.exception
  assert fake.calls[0][3:] == [
    "saturate",
    str(corpus.resolve()),
    "--gap",
    FILLED_LIMITATION,
  ]


def test_the_page_shows_the_badge_and_out_of_corpus_hits(
  corpus: Path, monkeypatch: pytest.MonkeyPatch
):
  """After a check the card shows its badge, labelled hits and the queries."""
  check = _check(corpus, FILLED_GAP)
  app = _open_all_gap_cards(corpus, monkeypatch)

  assert not app.exception
  badge = _cards(corpus)[FILLED_GAP].saturation.badge
  assert badge in [caption.value for caption in app.caption]
  assert text.GAP_SATURATION_OUT_OF_CORPUS in [info.value for info in app.info]
  codes = [block.value for block in app.code]
  assert all(search.query in codes for search in check.searches)
  markdown = "\n".join(block.value for block in app.markdown)
  assert check.hits[0].reason in markdown


def test_the_page_shows_each_hits_llm_verdict(
  corpus: Path, monkeypatch: pytest.MonkeyPatch
):
  """The drill-down lists every LLM verdict and reason, and the screening run."""
  check = _check(corpus, FILLED_GAP, screener=_recorded_screener())
  app = _open_all_gap_cards(corpus, monkeypatch)

  assert not app.exception
  markdown = "\n".join(block.value for block in app.markdown)
  for hit in check.hits:
    if hit.llm_verdict:
      assert hit.llm_reason in markdown
  view = _cards(corpus)[FILLED_GAP].saturation
  assert view.llm_screening in [caption.value for caption in app.caption]


def test_the_second_attempt_is_labelled_with_its_model_and_queries(
  corpus: Path, monkeypatch: pytest.MonkeyPatch
):
  """LLM-rephrased queries and their hits read as the adversarial second attempt."""
  llm = _FakeVariantLlm(VARIANTS)
  check, _ = _variant_check(corpus, llm, {"carcinoid valve disease": [VARIANT_HIT]})

  view = _cards(corpus)[CONTROL_GAP].saturation

  assert check.second_attempt is not None
  assert view.second_attempt_label == check.second_attempt.label
  assert "claude-test" in view.second_attempt_provenance
  assert QUERY_VARIANT_PROMPT_VERSION in view.second_attempt_provenance
  assert {s.attempt for s in view.searches} == {"base", "llm_variant"}
  variant_hit = next(h for h in view.hits if h.screened)
  assert variant_hit.second_attempt

  app = _open_all_gap_cards(corpus, monkeypatch)
  assert not app.exception
  markdown = "\n".join(block.value for block in app.markdown)
  assert check.second_attempt.label in markdown
  codes = [block.value for block in app.code]
  assert all(search.query in codes for search in check.searches)


def test_a_second_attempt_that_did_not_run_says_why(corpus: Path):
  """Without an LLM the card notes the attempt was not run, and why."""
  check = _check(corpus, CONTROL_GAP)

  view = _cards(corpus)[CONTROL_GAP].saturation

  assert check.second_attempt is not None
  assert view.second_attempt_note == check.second_attempt.note
