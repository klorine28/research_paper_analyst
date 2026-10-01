"""
Behavior of the Gap Cards view: CandidateGaps artifact and judgments in, cards out.

The dashboard reads the artifact the detect stage writes, so these tests build a
real artifact with detect and then read it back through the dashboard's own
reader: a schema drift between writer and reader would surface here. The cards
must carry every field, including the verbatim Evidence passages, and reflect
the researcher's saved accept/reject state.
"""

from pathlib import Path

import pytest

from conftest import StubLlmClient
from test_detect import _PLACEMENTS, _write_corpus

import research_gap_dashboard.dashboard as dashboard_pkg

from research_gap_dashboard.analytics import CorpusPaper, GroundingContext
from research_gap_dashboard.dashboard.artifacts import load_candidate_gaps
from research_gap_dashboard.dashboard.gaps import build_gap_cards, scope_context_to_gap
from research_gap_dashboard.dashboard.judgments import load_judgments, record_judgment
from research_gap_dashboard.detect import detect_corpus


@pytest.fixture(name="corpus")
def corpus_fixture(tmp_path: Path) -> Path:
  """
  Write a synthetic Corpus and run detect so a CandidateGaps artifact exists.

  The corpus lives in its own subdirectory so its parent holds only this Corpus,
  keeping the app's corpus picker deterministic under the shared pytest tmp dir.
  """
  root = _write_corpus(tmp_path / "corpus", _PLACEMENTS)
  detect_corpus(root, StubLlmClient({}))
  return root


def _card(corpus: Path, gap_id: str):
  """Build the cards and return the one with this gap id, failing if absent."""
  data = build_gap_cards(load_candidate_gaps(corpus), load_judgments(corpus))
  by_id = {card.gap_id: card for card in data.cards}
  assert gap_id in by_id, f"{gap_id} not in {sorted(by_id)}"
  return by_id[gap_id]


def test_every_candidate_gap_becomes_a_card(corpus: Path):
  """The view lists one card per Candidate Gap the artifact holds."""
  artifact = load_candidate_gaps(corpus)
  data = build_gap_cards(artifact, load_judgments(corpus))

  assert data.gap_count == len(artifact.gaps)
  assert data.gap_count > 0


def test_a_card_renders_every_field(corpus: Path):
  """A cell-gap card carries its type, explanation, confidence, and cell count."""
  card = _card(corpus, "topicxmethod:takotsubo:rct")

  assert card.gap_type_label == "Coverage Gap"
  assert card.confidence_label == "Low"
  assert card.confidence_reason
  assert card.cell_count == 0
  assert card.corpus_paper_count == 6
  assert "4 of 6 Papers" in card.explanation


def test_a_card_carries_verbatim_evidence_passages(corpus: Path):
  """Every card links the exact Evidence passages behind its categories."""
  card = _card(corpus, "topicxmethod:takotsubo:rct")

  passages = {(p.citation_key, p.passage) for p in card.evidence}
  assert ("p01", "p01 passage on takotsubo") in passages
  assert ("p05", "p05 passage on rct") in passages
  assert all(p.section for p in card.evidence)


def test_new_cards_start_undecided(corpus: Path):
  """A gap with no saved judgment reads as undecided, not accepted or rejected."""
  data = build_gap_cards(load_candidate_gaps(corpus), load_judgments(corpus))

  assert all(card.status is None for card in data.cards)
  assert data.undecided_count == data.gap_count
  assert data.accepted_count == 0
  assert data.rejected_count == 0


def test_saved_judgments_are_merged_onto_the_cards(corpus: Path):
  """Accept/reject state persisted to disk is restored on the matching cards."""
  record_judgment(corpus, "topicxmethod:takotsubo:rct", "accepted")
  record_judgment(corpus, "topicxpopulation:takotsubo:aged", "rejected")

  data = build_gap_cards(load_candidate_gaps(corpus), load_judgments(corpus))

  assert _card(corpus, "topicxmethod:takotsubo:rct").status == "accepted"
  assert _card(corpus, "topicxpopulation:takotsubo:aged").status == "rejected"
  assert data.accepted_count == 1
  assert data.rejected_count == 1
  assert data.undecided_count == data.gap_count - 2


def _corpus_context() -> GroundingContext:
  """Build a whole-Corpus grounding context spanning the synthetic Corpus."""
  return GroundingContext(
    papers=[
      CorpusPaper(citation_key=f"p0{n}", title=f"Paper {n}", year=2000 + n)
      for n in range(1, 7)
    ]
  )


def test_scope_context_to_gap_narrows_to_the_gaps_slice(corpus: Path):
  """A scoped context keeps only the gap, its source Papers, and its Evidence."""
  card = _card(corpus, "topicxmethod:takotsubo:rct")

  scoped = scope_context_to_gap(card, _corpus_context())

  assert [gap.title for gap in scoped.gaps] == [card.title]
  evidence_keys = {passage.citation_key for passage in card.evidence}
  scoped_keys = {paper.citation_key for paper in scoped.papers}
  assert scoped_keys == set(card.source_citation_keys) | evidence_keys
  assert scoped_keys < _corpus_context().allowed_keys


def test_scope_context_to_gap_carries_the_evidence_passages(corpus: Path):
  """The scoped context quotes the same Evidence passages the card shows."""
  card = _card(corpus, "topicxmethod:takotsubo:rct")

  scoped = scope_context_to_gap(card, _corpus_context())

  rendered = scoped.render()
  for passage in card.evidence:
    assert passage.passage in rendered


def _run_gap_cards_app(corpus: Path, monkeypatch: pytest.MonkeyPatch):
  """Run the Streamlit shell on the Gap Cards page for a corpus directory."""
  from streamlit.testing.v1 import AppTest

  monkeypatch.setenv("RESEARCH_GAP_CORPORA_DIR", str(corpus.parent))
  app_path = Path(dashboard_pkg.__file__).parent / "app.py"
  app = AppTest.from_file(str(app_path), default_timeout=30).run()
  app.radio[0].set_value("Gap Cards").run()
  return app


def test_gap_cards_page_renders_and_persists_a_judgment(
  corpus: Path, monkeypatch: pytest.MonkeyPatch
):
  """The Gap Cards page shows cards and its Accept button writes a judgment."""
  app = _run_gap_cards_app(corpus, monkeypatch)

  assert not app.exception
  assert "Gap Cards" in [header.value for header in app.header]
  accept = next(button for button in app.button if button.key.startswith("accept-"))
  gap_id = accept.key.removeprefix("accept-")

  accept.click().run()

  assert not app.exception
  assert load_judgments(corpus).by_gap_id()[gap_id].decision == "accepted"


def test_discuss_this_gap_opens_a_persistent_scoped_conversation(
  corpus: Path, monkeypatch: pytest.MonkeyPatch
):
  """Clicking 'Discuss this gap' opens a saved thread scoped to that gap (#48, #50)."""
  import research_gap_dashboard.analytics as analytics_module
  from research_gap_dashboard.dashboard.conversations import (
    conversation_id_for_gap,
    load_conversation,
  )
  from research_gap_dashboard.dashboard.text import (
    ANALYTICS_SCOPED_INTRO,
    GAP_DISCUSS_BUTTON,
    PAGE_ANALYTICS,
  )

  # A keyless stub answers the scoped chat so the smoke test never touches the
  # network (CODING_STANDARDS.md > Test with fixtures, not live services). AppTest
  # re-imports the app each run, so patch the client factory at its source module.
  monkeypatch.setattr(
    analytics_module,
    "build_analytics_client",
    lambda _cache_dir: StubLlmClient({"answer": "Grounded.", "citations": []}),
  )

  app = _run_gap_cards_app(corpus, monkeypatch)
  discuss = next(button for button in app.button if button.label == GAP_DISCUSS_BUTTON)
  gap_id = discuss.key.removeprefix("discuss-")

  discuss.click().run()

  assert not app.exception
  assert app.session_state["nav_page"] == PAGE_ANALYTICS
  # The one-click button is consumed to open the gap's persistent thread.
  assert app.session_state["active_conversation_id"] == conversation_id_for_gap(gap_id)
  intros = [block.value for block in app.info]
  assert ANALYTICS_SCOPED_INTRO in intros

  app.chat_input[0].set_value("Why is this a gap?").run()

  assert not app.exception
  markdown = " ".join(block.value for block in app.markdown)
  assert "Grounded." in markdown
  # The exchange is saved to the gap's thread, not thrown away with the session.
  saved = load_conversation(corpus, conversation_id_for_gap(gap_id))
  assert saved is not None
  assert [turn.content for turn in saved.turns] == ["Why is this a gap?", "Grounded."]
