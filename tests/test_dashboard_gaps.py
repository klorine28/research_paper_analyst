"""
Behavior of the Gap Cards view: CandidateGaps artifact and judgments in, cards out.

The dashboard reads the artifact the detect stage writes, so these tests build a
real artifact with detect and then read it back through the dashboard's own
reader: a schema drift between writer and reader would surface here. The cards
must carry every field, including the verbatim Evidence passages, and reflect
the researcher's saved accept/reject state.
"""

import json
import re
import shutil
from pathlib import Path

import pytest

from conftest import StubLlmClient, open_page
from test_detect import (
  _BRIDGES,
  _PLACEMENTS,
  _ROLL_UP,
  _ROLL_UP_PARENTS,
  _WITNESS_EXTRACTIONS,
  _WITNESS_GROUPING,
  RoutedLlmClient,
  _tags,
  _write_corpus,
)

from research_gap_dashboard.dashboard.trust_gate import acknowledge_denominator
import research_gap_dashboard.dashboard as dashboard_pkg

from research_gap_dashboard.analytics import CorpusPaper, GroundingContext
from research_gap_dashboard.dashboard import text
from research_gap_dashboard.dashboard.artifacts import (
  CandidateGapsArtifact,
  load_candidate_gaps,
)
from research_gap_dashboard.dashboard.comparison import (
  MAX_SELECTION,
  comparison_preselection,
)
from research_gap_dashboard.dashboard.gaps import (
  DEFAULT_CONFIDENCE_FILTER,
  build_gap_cards,
  filter_gap_cards,
  filter_by_confidence,
  narrative_scope,
  next_undecided_gap_id,
  scope_context_to_gap,
  surfaced_gap_context,
)
from research_gap_dashboard.dashboard.judgments import load_judgments, record_judgment
from research_gap_dashboard.detect import detect_corpus
from research_gap_dashboard.limitations import GROUP_PROMPT_VERSION, TAG_PROMPT_VERSION
from research_gap_dashboard.llm import RecordedLlmClient


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


def test_narrative_scope_keeps_only_accepted_gaps(corpus: Path):
  """Rejected and undecided gaps never reach the narrative's grounding context."""
  data = build_gap_cards(load_candidate_gaps(corpus), load_judgments(corpus))
  accepted, rejected = data.cards[0], data.cards[1]
  record_judgment(corpus, accepted.gap_id, "accepted")
  record_judgment(corpus, rejected.gap_id, "rejected")
  data = build_gap_cards(load_candidate_gaps(corpus), load_judgments(corpus))

  scope = narrative_scope(data, _corpus_context(), ("high", "medium", "low"))

  assert [gap.title for gap in scope.context.gaps] == [accepted.title]
  assert scope.accepted_count == 1
  assert scope.rejected_count == 1
  assert scope.undecided_count == data.gap_count - 2
  assert scope.paper_count == data.corpus_paper_count
  # Papers stay the full Corpus: they are the citation allow-list.
  assert scope.context.allowed_keys == _corpus_context().allowed_keys


def test_narrative_scope_uses_only_the_gaps_the_confidence_filter_surfaces(
  corpus: Path,
):
  """An accepted Low gap stays out of the narrative by default, and is counted (#98)."""
  data = build_gap_cards(load_candidate_gaps(corpus), load_judgments(corpus))
  medium = next(card for card in data.cards if card.confidence == "medium")
  low = next(card for card in data.cards if card.confidence == "low")
  record_judgment(corpus, medium.gap_id, "accepted")
  record_judgment(corpus, low.gap_id, "accepted")
  data = build_gap_cards(load_candidate_gaps(corpus), load_judgments(corpus))

  scope = narrative_scope(data, _corpus_context())

  assert [gap.title for gap in scope.context.gaps] == [medium.title]
  assert scope.confidence_levels == list(DEFAULT_CONFIDENCE_FILTER)
  assert scope.accepted_count == 1
  assert scope.undecided_count == 0
  assert scope.hidden_count == data.gap_count - 1
  assert scope.hidden_accepted_count == 1
  # The narrative's own context says which gaps it was given.
  assert "High, Medium" in scope.context.render()


def test_narrative_scope_follows_a_widened_confidence_filter(corpus: Path):
  """With Low shown too, every accepted gap reaches the narrative."""
  data = build_gap_cards(load_candidate_gaps(corpus), load_judgments(corpus))
  low = next(card for card in data.cards if card.confidence == "low")
  record_judgment(corpus, low.gap_id, "accepted")
  data = build_gap_cards(load_candidate_gaps(corpus), load_judgments(corpus))

  scope = narrative_scope(data, _corpus_context(), ("high", "medium", "low"))

  assert [gap.title for gap in scope.context.gaps] == [low.title]
  assert scope.hidden_count == 0
  assert scope.hidden_accepted_count == 0


def test_surfaced_gap_context_holds_only_the_default_filter_s_gaps(corpus: Path):
  """The general chat sees High and Medium gaps only, and is told what is hidden."""
  data = build_gap_cards(load_candidate_gaps(corpus), load_judgments(corpus))

  context = surfaced_gap_context(data, _corpus_context())

  surfaced = [card for card in data.cards if card.confidence in ("high", "medium")]
  assert [gap.title for gap in context.gaps] == [card.title for card in surfaced]
  rendered = context.render()
  assert "High, Medium" in rendered
  assert f"{data.gap_count - len(surfaced)} gap(s) at Low confidence" in rendered
  # Papers stay the full Corpus: they are the citation allow-list.
  assert context.allowed_keys == _corpus_context().allowed_keys


def test_narrative_scope_with_no_accepted_gaps_is_empty(corpus: Path):
  """With no verdicts at all, the narrative has nothing to ground itself in."""
  data = build_gap_cards(load_candidate_gaps(corpus), load_judgments(corpus))

  scope = narrative_scope(data, _corpus_context(), ("high", "medium", "low"))

  assert scope.context.gaps == []
  assert scope.accepted_count == 0
  assert scope.undecided_count == data.gap_count


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

  # Past the soft Trust gate (#77): this fixture Corpus is partial.
  acknowledge_denominator(corpus)
  monkeypatch.setenv("RESEARCH_GAP_CORPORA_DIR", str(corpus.parent))
  app_path = Path(dashboard_pkg.__file__).parent / "app.py"
  app = AppTest.from_file(str(app_path), default_timeout=30).run()
  open_page(app, "Gap Cards")
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


def test_filtering_by_status_states_how_many_gaps_are_hidden(corpus: Path):
  """A status filter keeps only matching cards and counts the rest as hidden."""
  record_judgment(corpus, "topicxmethod:takotsubo:rct", "accepted")
  data = build_gap_cards(load_candidate_gaps(corpus), load_judgments(corpus))

  shown = filter_gap_cards(data, statuses={"undecided"})

  assert [card.gap_id for card in shown.cards] == [
    "topicxtopic:heart-failure:takotsubo",
    "topicxmethod:heart-failure:retrospective",
    "topicxpopulation:takotsubo:aged",
  ]
  assert shown.hidden_count == 1


def test_status_and_gap_type_filters_combine(corpus: Path):
  """Both filters must match: undecided Coverage Gaps leave out the Knowledge Gap."""
  record_judgment(corpus, "topicxmethod:takotsubo:rct", "accepted")
  data = build_gap_cards(load_candidate_gaps(corpus), load_judgments(corpus))

  shown = filter_gap_cards(data, statuses={"undecided"}, gap_types={"coverage_gap"})

  assert [card.gap_id for card in shown.cards] == [
    "topicxmethod:heart-failure:retrospective",
    "topicxpopulation:takotsubo:aged",
  ]
  assert shown.hidden_count == 2


def test_next_undecided_skips_decided_cards_and_wraps(corpus: Path):
  """'Next undecided' walks the undecided cards in order and wraps to the top."""
  record_judgment(corpus, "topicxmethod:takotsubo:rct", "rejected")
  cards = build_gap_cards(load_candidate_gaps(corpus), load_judgments(corpus)).cards

  first = next_undecided_gap_id(cards, None)
  second = next_undecided_gap_id(cards, first)
  third = next_undecided_gap_id(cards, second)
  fourth = next_undecided_gap_id(cards, third)

  assert first == "topicxtopic:heart-failure:takotsubo"
  assert second == "topicxmethod:heart-failure:retrospective"
  assert third == "topicxpopulation:takotsubo:aged"
  assert fourth == "topicxtopic:heart-failure:takotsubo"


def test_next_undecided_is_none_when_every_gap_is_decided(corpus: Path):
  """With no undecided card left there is nowhere to move to."""
  for gap_id in (
    "topicxtopic:heart-failure:takotsubo",
    "topicxmethod:heart-failure:retrospective",
    "topicxmethod:takotsubo:rct",
    "topicxpopulation:takotsubo:aged",
  ):
    record_judgment(corpus, gap_id, "accepted")
  cards = build_gap_cards(load_candidate_gaps(corpus), load_judgments(corpus)).cards

  assert next_undecided_gap_id(cards, "topicxmethod:takotsubo:rct") is None


def test_gap_cards_page_filters_and_states_the_hidden_count(
  corpus: Path, monkeypatch: pytest.MonkeyPatch
):
  """Filtering the page to accepted gaps hides the rest and says how many."""
  record_judgment(corpus, "topicxmethod:takotsubo:rct", "accepted")
  app = _run_gap_cards_app(corpus, monkeypatch)
  # Show every confidence level: the default filter (#108) hides Low cards.
  app.multiselect[0].set_value(["high", "medium", "low"]).run()

  app.multiselect(key="gap_filter_status").set_value(["accepted"]).run()

  assert not app.exception
  accept_keys = [b.key for b in app.button if b.key.startswith("accept-")]
  assert accept_keys == ["accept-topicxmethod:takotsubo:rct"]
  captions = " ".join(caption.value for caption in app.caption)
  assert "Showing 1 of 4 candidate gaps; 3 hidden by the filters." in captions


def test_next_undecided_moves_to_the_next_undecided_card(
  corpus: Path, monkeypatch: pytest.MonkeyPatch
):
  """Each press of 'Next undecided' brings the next undecided card into focus."""
  record_judgment(corpus, "topicxtopic:heart-failure:takotsubo", "accepted")
  app = _run_gap_cards_app(corpus, monkeypatch)
  # Show every confidence level: the default filter (#108) hides Low cards.
  app.multiselect[0].set_value(["high", "medium", "low"]).run()

  app.button(key="gap-next-undecided").click().run()
  assert app.session_state["gap_focus_id"] == "topicxmethod:heart-failure:retrospective"
  first_accept = next(b for b in app.button if b.key.startswith("accept-"))
  assert first_accept.key == "accept-topicxmethod:heart-failure:retrospective"

  app.button(key="gap-next-undecided").click().run()

  assert not app.exception
  assert app.session_state["gap_focus_id"] == "topicxmethod:takotsubo:rct"
  first_accept = next(b for b in app.button if b.key.startswith("accept-"))
  assert first_accept.key == "accept-topicxmethod:takotsubo:rct"


def test_confidence_filter_defaults_to_high_and_medium(corpus: Path):
  """By default Low-confidence gaps are hidden, and the hidden count says so."""
  data = build_gap_cards(load_candidate_gaps(corpus), load_judgments(corpus))

  filtered = filter_by_confidence(data, DEFAULT_CONFIDENCE_FILTER)

  assert [card.confidence for card in filtered.cards] == ["medium"]
  assert filtered.shown_count == 1
  assert filtered.hidden_count == 3
  assert filtered.total_count == data.gap_count


@pytest.mark.parametrize(
  ("levels", "shown", "hidden"),
  [
    (("high", "medium", "low"), 4, 0),
    (("low",), 3, 1),
    (("high",), 0, 4),
    ((), 0, 4),
  ],
)
def test_confidence_filter_keeps_only_the_chosen_levels(
  corpus: Path, levels: tuple[str, ...], shown: int, hidden: int
):
  """Only cards at a chosen level are shown; every other card counts as hidden."""
  data = build_gap_cards(load_candidate_gaps(corpus), load_judgments(corpus))

  filtered = filter_by_confidence(data, levels)

  assert all(card.confidence in levels for card in filtered.cards)
  assert filtered.shown_count == shown
  assert filtered.hidden_count == hidden


def test_confidence_filter_keeps_the_artifact_order(corpus: Path):
  """Filtering never reorders the cards it keeps."""
  data = build_gap_cards(load_candidate_gaps(corpus), load_judgments(corpus))

  filtered = filter_by_confidence(data, ("high", "medium", "low"))

  assert [c.gap_id for c in filtered.cards] == [c.gap_id for c in data.cards]


def test_gap_cards_page_shows_the_hidden_count_and_keeps_the_filter(
  corpus: Path, monkeypatch: pytest.MonkeyPatch
):
  """The page states how many gaps are hidden; the filter survives navigation."""
  from research_gap_dashboard.dashboard.text import (
    GAP_FILTER_SUMMARY,
    PAGE_OVERVIEW,
  )

  app = _run_gap_cards_app(corpus, monkeypatch)

  def captions() -> list[str]:
    return [block.value for block in app.caption]

  assert not app.exception
  assert GAP_FILTER_SUMMARY.format(shown=1, total=4, hidden=3) in captions()
  assert len([b for b in app.button if b.key.startswith("accept-")]) == 1

  app.multiselect[0].set_value(["high", "medium", "low"]).run()

  assert GAP_FILTER_SUMMARY.format(shown=4, total=4, hidden=0) in captions()

  open_page(app, PAGE_OVERVIEW)
  open_page(app, "Gap Cards")

  assert not app.exception
  assert app.multiselect[0].value == ["high", "medium", "low"]
  assert GAP_FILTER_SUMMARY.format(shown=4, total=4, hidden=0) in captions()


def test_tier_one_headline_states_why_and_the_cell_count(corpus: Path):
  """Tier 1 carries a one-line why built from the cell's own counts (ADR 0008)."""
  card = _card(corpus, "topicxmethod:takotsubo:rct")

  assert card.title
  assert card.why == (
    "4 of 6 Papers cover Takotsubo Cardiomyopathy and 1 of 6 cover "
    "Randomized Controlled Trial; 0 combine them."
  )
  assert card.cell_count == 0


def test_tier_three_lists_the_papers_in_each_category(corpus: Path):
  """'Show the data' names the Papers placed on each of the cell's categories."""
  card = _card(corpus, "topicxmethod:takotsubo:rct")

  papers = {group.label: group.citation_keys for group in card.data.category_papers}
  assert papers == {
    "Takotsubo Cardiomyopathy": ["p01", "p02", "p03", "p04"],
    "Randomized Controlled Trial": ["p05"],
  }


def test_tier_three_lists_the_neighbouring_cells(corpus: Path):
  """Neighbouring cells share a category with the gap, fullest first, never itself."""
  card = _card(corpus, "topicxmethod:takotsubo:rct")

  neighbours = [
    (cell.row_label, cell.column_label, cell.paper_count)
    for cell in card.data.neighbours
  ]
  assert neighbours == [
    ("Takotsubo Cardiomyopathy", "Retrospective Studies", 4),
    ("Heart Failure", "Randomized Controlled Trial", 1),
  ]


def test_tier_three_phrases_the_score_as_expected_versus_observed(corpus: Path):
  """The score reads as expected vs observed Papers: 4 x 1 / 6 expected, 0 seen."""
  card = _card(corpus, "topicxmethod:takotsubo:rct")

  assert card.data.expected_count == pytest.approx(4 / 6)
  assert card.data.observed_count == 0
  assert card.confidence_reason in card.data.reasons


def test_a_limitation_card_has_tiers_too(tmp_path: Path):
  """An Unanswered Limitation's headline and data come from its source Papers."""
  artifact = CandidateGapsArtifact.model_validate(
    {
      "corpus_root": str(tmp_path),
      "corpus_paper_count": 6,
      "gaps": [
        {
          "gap_id": "limitation:g1",
          "gap_type": "unanswered_limitation",
          "title": "Small samples",
          "explanation": "Candidate Unanswered Limitation.",
          "confidence": "medium",
          "confidence_reason": "3 later Paper(s) in the Corpus were checked.",
          "corpus_paper_count": 6,
          "source_citation_keys": ["p01", "p02"],
          "later_paper_count": 3,
          "evidence": [
            {
              "citation_key": "p01",
              "statement": "The sample was small.",
              "evidence": {"passage": "Our sample was small.", "section": "discussion"},
            }
          ],
        }
      ],
    }
  )

  card = build_gap_cards(artifact, load_judgments(tmp_path)).cards[0]

  assert card.why == "2 Paper(s) state this; none of 3 later Paper(s) addressed it."
  assert [(g.label, g.citation_keys) for g in card.data.category_papers] == [
    ("Source Papers", ["p01", "p02"])
  ]
  assert card.data.neighbours == []
  assert card.data.expected_count is None


def test_every_card_expands_into_justification_and_data_tiers(
  corpus: Path, monkeypatch: pytest.MonkeyPatch
):
  """Each card's Tier 2 holds its Evidence and Tier 3 its data; Discuss stays."""
  app = _run_gap_cards_app(corpus, monkeypatch)
  # Show every confidence level: the default filter (#108) hides Low cards.
  app.multiselect[0].set_value(["high", "medium", "low"]).run()
  card_count = len(load_candidate_gaps(corpus).gaps)

  assert not app.exception
  justification = [
    e for e in app.expander if e.label == text.GAP_TIER_JUSTIFICATION_LABEL
  ]
  data = [e for e in app.expander if e.label == text.GAP_TIER_DATA_LABEL]
  assert len(justification) == len(data) == card_count
  discuss = [b for b in app.button if b.label == text.GAP_DISCUSS_BUTTON]
  assert len(discuss) == card_count

  justification_text = " ".join(
    block.value for tier in justification for block in tier.markdown
  )
  assert "p01 passage on takotsubo" in justification_text
  data_text = " ".join(block.value for tier in data for block in tier.markdown)
  assert "Takotsubo Cardiomyopathy × Retrospective Studies: 4" in data_text
  assert "p01, p02, p03, p04" in data_text


def test_a_card_lists_its_papers_once_sources_first(corpus: Path):
  """A card's Papers are its source Papers, then any Evidence Paper, each once."""
  data = build_gap_cards(load_candidate_gaps(corpus), load_judgments(corpus))

  for card in data.cards:
    expected = list(
      dict.fromkeys(
        [*card.source_citation_keys, *(p.citation_key for p in card.evidence)]
      )
    )
    assert card.paper_keys == expected
    assert card.paper_keys


def test_comparison_preselection_keeps_corpus_papers_up_to_the_limit():
  """A gap's Papers preselect the Comparison: Corpus Papers only, capped."""
  corpus_keys = [f"p{i:02d}" for i in range(MAX_SELECTION + 5)]
  wanted = ["ghost", *corpus_keys, "p00"]

  chosen = comparison_preselection(wanted, corpus_keys)

  assert chosen == corpus_keys[:MAX_SELECTION]


def _first_explain_picker(app) -> tuple[str, list[str]]:
  """Return the first shown card's gap id and its Papers, from its Explain picker."""
  picker = next(box for box in app.selectbox if box.key.startswith("explain-pick-"))
  return picker.key.removeprefix("explain-pick-"), list(picker.options)


def test_open_in_explainer_preselects_the_chosen_paper(
  corpus: Path, monkeypatch: pytest.MonkeyPatch
):
  """A card's Explainer button opens the Explainer with the chosen Paper (#80)."""
  app = _run_gap_cards_app(corpus, monkeypatch)
  gap_id, papers = _first_explain_picker(app)
  chosen = papers[-1]

  app.selectbox(key=f"explain-pick-{gap_id}").set_value(chosen).run()
  app.button(key=f"explain-{gap_id}").click().run()

  assert not app.exception
  assert app.session_state["nav_page"] == text.PAGE_EXPLAINER
  assert text.EXPLAINER_HEADING in [header.value for header in app.header]
  assert app.selectbox[0].value == chosen


def test_compare_opens_the_comparison_with_the_gaps_papers(
  corpus: Path, monkeypatch: pytest.MonkeyPatch
):
  """A card's Compare button opens the Comparison with its Papers selected (#80)."""
  app = _run_gap_cards_app(corpus, monkeypatch)
  gap_id = next(
    button.key.removeprefix("compare-")
    for button in app.button
    if button.key.startswith("compare-")
  )
  card = _card(corpus, gap_id)

  app.button(key=f"compare-{gap_id}").click().run()

  assert not app.exception
  assert app.session_state["nav_page"] == text.PAGE_COMPARISON
  assert text.COMPARISON_HEADING in [header.value for header in app.header]
  assert app.multiselect[0].value == card.paper_keys


def test_a_preselected_comparison_keeps_later_edits(
  corpus: Path, monkeypatch: pytest.MonkeyPatch
):
  """After opening from a card, changing the selection is not undone (#80)."""
  app = _run_gap_cards_app(corpus, monkeypatch)
  compare = next(button for button in app.button if button.key.startswith("compare-"))
  compare.click().run()
  kept = app.multiselect[0].value[:-1]
  if len(kept) < 2:
    kept = app.multiselect[0].value

  app.multiselect[0].set_value(kept).run()
  app.run()

  assert not app.exception
  assert app.multiselect[0].value == kept


def test_an_explainer_opened_from_a_card_keeps_a_later_choice(
  corpus: Path, monkeypatch: pytest.MonkeyPatch
):
  """After opening from a card, picking another Paper is not undone (#80)."""
  app = _run_gap_cards_app(corpus, monkeypatch)
  gap_id, papers = _first_explain_picker(app)
  app.selectbox(key=f"explain-pick-{gap_id}").set_value(papers[-1]).run()
  app.button(key=f"explain-{gap_id}").click().run()
  assert app.selectbox[0].value == papers[-1]
  opened = app.selectbox[0].index
  # Neither the opened Paper nor the picker's default first entry, so a reset to
  # either would show.
  other = next(i for i in range(len(app.selectbox[0].options)) if i not in {0, opened})

  app.selectbox[0].select_index(other).run()
  picked = app.selectbox[0].value
  app.run()

  assert not app.exception
  assert app.selectbox[0].index == other
  assert app.selectbox[0].value == picked


def test_the_read_model_mirrors_the_verdict_trail(corpus: Path):
  """Each cell gap's layer steps reach the card's Tier 3 reasons, in order."""
  artifact = load_candidate_gaps(corpus)
  record = next(g for g in artifact.gaps if g.gap_id == "topicxmethod:takotsubo:rct")

  assert [(s.layer, s.effect, s.level_after) for s in record.trail] == [
    ("l1", "start", record.confidence)
  ]
  card = _card(corpus, "topicxmethod:takotsubo:rct")
  assert card.data.reasons == [step.reason for step in record.trail]


def test_the_read_model_carries_the_l1_score_and_margins(corpus: Path):
  """Each cell gap's L1 probability and the Corpus's smallest margins are read."""
  artifact = load_candidate_gaps(corpus)
  record = next(g for g in artifact.gaps if g.gap_id == "topicxmethod:takotsubo:rct")

  # 4 Takotsubo x 1 RCT of 6 Papers, empty: 2/6.
  assert record.l1_probability == pytest.approx(2 / 6)
  data = build_gap_cards(artifact, load_judgments(corpus))
  # N = 6: equal margins of 3 reach both High and Medium (1/20).
  assert data.l1_smallest_margins == (3, 3)


def test_the_gap_list_states_the_smallest_margins_that_rank_high(
  corpus: Path, monkeypatch: pytest.MonkeyPatch
):
  """The researcher sees what L1 can detect at this Corpus size, in Papers."""
  app = _run_gap_cards_app(corpus, monkeypatch)

  assert not app.exception
  captions = [caption.value for caption in app.caption]
  expected = text.GAP_L1_MARGINS.format(total=6, high=3, medium=3)
  assert expected in captions


def test_no_gap_card_text_claims_significance():
  """No user-facing gap text words the L1 score as a significance test or p."""
  for name in dir(text):
    value = getattr(text, name)
    if name.startswith("GAP_") and isinstance(value, str):
      assert "significan" not in value.lower(), name
      assert re.search(r"\bp\b", value, flags=re.IGNORECASE) is None, name


def test_an_artifact_without_trails_still_builds_cards(corpus: Path):
  """A CandidateGaps artifact written before the trail existed reads cleanly."""
  path = corpus / "artifacts" / "candidate_gaps.json"
  data = json.loads(path.read_text(encoding="utf-8"))
  for gap in data["gaps"]:
    gap.pop("trail", None)
  path.write_text(json.dumps(data), encoding="utf-8")

  card = _card(corpus, "topicxmethod:takotsubo:rct")

  assert card.data.reasons == [card.confidence_reason]


def test_tier_three_lists_the_bridges_strongest_first(tmp_path: Path):
  """A cell's bridging categories reach its Tier 3, Adamic-Adar order kept."""
  root = _write_corpus(tmp_path / "corpus", _BRIDGES)
  detect_corpus(root, StubLlmClient({}))

  card = _card(root, "topicxmethod:takotsubo:rct")

  assert [bridge.label for bridge in card.data.bridges] == ["Female", "Aged"]
  assert card.data.bridges[0].text == text.GAP_DATA_BRIDGE.format(
    label="Female", axis="population", row_count=2, column_count=2
  )


def test_the_card_shows_its_bridges(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
  """The rendered Tier 3 names each bridge under its own heading."""
  root = _write_corpus(tmp_path / "corpus", _BRIDGES)
  detect_corpus(root, StubLlmClient({}))
  app = _run_gap_cards_app(root, monkeypatch)
  app.multiselect[0].set_value(["high", "medium", "low"]).run()

  assert not app.exception
  data = [e for e in app.expander if e.label == text.GAP_TIER_DATA_LABEL]
  texts = [" ".join(block.value for block in tier.markdown) for tier in data]
  bridged = [t for t in texts if "- Female (population" in t]
  assert len(bridged) == 1
  assert text.GAP_DATA_BRIDGES_HEADING in bridged[0]
  assert bridged[0].index("- Female (population") < bridged[0].index(
    "- Aged (population"
  )


def test_a_card_without_bridges_lists_none(corpus: Path):
  """No bridge, no list: older artifacts and unbridged cells read the same."""
  card = _card(corpus, "topicxmethod:takotsubo:rct")

  assert card.data.bridges == []


@pytest.fixture(name="witnessed")
def witnessed_fixture(tmp_path: Path) -> Path:
  """
  Write a Corpus whose limitation group 01 corroborates Takotsubo x RCT.

  Group 01 is a study-design limitation about Takotsubo; group 02 a generic
  sample-size one. Both stay unanswered (no Paper has a year).
  """
  client = RoutedLlmClient(
    {
      GROUP_PROMPT_VERSION: [_WITNESS_GROUPING],
      TAG_PROMPT_VERSION: [
        _tags(
          ("01", "study_design", ["topic:takotsubo"]),
          ("02", "sample_size", []),
        )
      ],
    }
  )
  root = _write_corpus(
    tmp_path / "corpus", _PLACEMENTS, extractions=_WITNESS_EXTRACTIONS
  )
  detect_corpus(root, client)
  return root


def test_a_corroborated_card_lists_the_groups_that_flag_it(witnessed: Path):
  """A two-witness card names its limitation groups; an unflagged one none."""
  flagged = _card(witnessed, "topicxmethod:takotsubo:rct")
  plain = _card(witnessed, "topicxtopic:heart-failure:takotsubo")

  assert flagged.flagged_by_authors == ["No randomized trial"]
  assert plain.flagged_by_authors == []


def test_an_unanswered_limitation_card_shows_its_bucket(witnessed: Path):
  """A limitation card carries its group's type; an untagged one shows none."""
  data = build_gap_cards(load_candidate_gaps(witnessed), load_judgments(witnessed))
  buckets = {
    card.title: card.bucket_label
    for card in data.cards
    if card.gap_type == "unanswered_limitation"
  }

  assert buckets == {
    "No randomized trial": "Study design",
    "Small sample": "Sample size",
    "Older patients": "",
  }


def test_the_page_shows_the_authors_flag_and_the_bucket(
  witnessed: Path, monkeypatch: pytest.MonkeyPatch
):
  """Tier 1 says "also flagged by authors" and a limitation card's type."""
  app = _run_gap_cards_app(witnessed, monkeypatch)
  app.multiselect[0].set_value(["high", "medium", "low"]).run()

  captions = " ".join(caption.value for caption in app.caption)
  assert not app.exception
  assert "Also flagged by authors: No randomized trial" in captions
  assert "Limitation type: Sample size" in captions


REVIEW_FIXTURE = Path(__file__).parent / "fixtures" / "gap-review"


@pytest.fixture(name="reviewed_corpus")
def reviewed_corpus_fixture(tmp_path: Path) -> Path:
  """Copy the gap-review fixture Corpus and detect it with its recorded answers."""
  # Its own parent directory, so the app's corpus picker sees only this Corpus.
  root = tmp_path / "reviewed" / "corpus"
  shutil.copytree(REVIEW_FIXTURE / "corpus", root)
  detect_corpus(
    root, RecordedLlmClient.from_file(REVIEW_FIXTURE / "llm-responses.json")
  )
  return root


def test_a_kept_gap_card_carries_audit_basis_and_minimal_study(reviewed_corpus: Path):
  """Tier 2 fields come from the review that kept the gap, with its Evidence ids."""
  card = _card(reviewed_corpus, "topicxmethod:takotsubo:rct")

  assert card.audit_basis.startswith("Takotsubo is studied only")
  assert card.audit_basis_evidence_ids == [
    "tako1:research_question[0]",
    "hf1:study_design[0]",
  ]
  assert "randomized trial" in card.minimal_study
  assert card.data.reasons[-1].startswith("Kept by review")


def test_a_rejected_or_unreviewed_card_has_no_review_fields(
  reviewed_corpus: Path, corpus: Path
):
  """A reject carries no Audit Basis or Minimal Study, nor does an unreviewed card."""
  rejected = _card(reviewed_corpus, "topicxtopic:heart-failure:takotsubo")
  unreviewed = _card(corpus, "topicxmethod:takotsubo:rct")

  for card in (rejected, unreviewed):
    assert card.audit_basis == ""
    assert card.minimal_study == ""
  assert "pseudo-firstness" in rejected.data.reasons[-1]


def test_tier_two_shows_the_audit_basis_and_a_labelled_minimal_study(
  reviewed_corpus: Path, monkeypatch: pytest.MonkeyPatch
):
  """The researcher reads the Audit Basis, and the Minimal Study as a suggestion."""
  app = _run_gap_cards_app(reviewed_corpus, monkeypatch)

  assert not app.exception
  justification = [
    e for e in app.expander if e.label == text.GAP_TIER_JUSTIFICATION_LABEL
  ]
  markdown = " ".join(block.value for tier in justification for block in tier.markdown)
  assert text.GAP_AUDIT_BASIS_HEADING in markdown
  assert text.GAP_MINIMAL_STUDY_HEADING in markdown
  assert "suggestion" in text.GAP_MINIMAL_STUDY_HEADING
  captions = " ".join(c.value for tier in justification for c in tier.caption)
  assert text.GAP_MINIMAL_STUDY_CAVEAT in captions


# "Rejected by review" (ADR 0008 §5, issue #109). The roll-up Corpus's hierarchy
# makes the taxonomy roll-up reject one cell and lower another, so the artifact
# detect writes carries real review verdicts.
_REJECTED_ID = "topicxtopic:cardiomyopathies:takotsubo"
_LOWERED_ID = "topicxmethod:takotsubo:rct"
_UNREVIEWED_ID = "topicxmethod:heart-failure:retrospective"
_ALL_LEVELS = ["high", "medium", "low"]


@pytest.fixture(name="reviewed")
def reviewed_fixture(tmp_path: Path) -> Path:
  """Write a Corpus whose CandidateGaps artifact rejects one cell and lowers one."""
  root = _write_corpus(tmp_path / "corpus", _ROLL_UP, parents=_ROLL_UP_PARENTS)
  detect_corpus(root, StubLlmClient({}))
  return root


def _reviewed_cards(corpus: Path):
  """Build the Gap Cards data for a corpus, judgments included."""
  return build_gap_cards(load_candidate_gaps(corpus), load_judgments(corpus))


def test_each_card_carries_its_review_verdicts(reviewed: Path):
  """A rejected or lowered card names the layer and its reason; others none."""
  by_id = {card.gap_id: card for card in _reviewed_cards(reviewed).cards}

  rejected = by_id[_REJECTED_ID]
  assert [(s.layer, s.effect) for s in rejected.review] == [
    ("taxonomy_roll_up", "reject")
  ]
  assert rejected.rejected_by_review
  assert "taxonomy artifact" in rejected.review[0].label
  assert "taxonomy roll-up" in rejected.review[0].label

  lowered = by_id[_LOWERED_ID]
  assert [(s.layer, s.effect, s.level_after) for s in lowered.review] == [
    ("taxonomy_roll_up", "lower", "low")
  ]
  assert not lowered.rejected_by_review
  assert "c1" in lowered.review[0].label

  assert by_id[_UNREVIEWED_ID].review == []


def test_rejected_candidates_leave_the_list_but_are_never_deleted(reviewed: Path):
  """A rejected card is kept and listed under review, counted apart from filters."""
  data = _reviewed_cards(reviewed)
  shown = filter_gap_cards(data, confidence_levels=_ALL_LEVELS)

  assert _REJECTED_ID in {card.gap_id for card in data.cards}
  assert _REJECTED_ID not in {card.gap_id for card in shown.cards}
  assert _LOWERED_ID in {card.gap_id for card in shown.cards}
  assert shown.review_hidden_count == 1
  assert shown.hidden_count == 0
  reviewed_ids = [card.gap_id for card in data.reviewed_cards]
  assert reviewed_ids[0] == _REJECTED_ID
  assert _LOWERED_ID in reviewed_ids
  assert _UNREVIEWED_ID not in reviewed_ids


def test_a_rejected_candidate_stays_out_of_the_chat_context(reviewed: Path):
  """The chat grounds in surfaced gaps only, so a rejected one is left out."""
  data = _reviewed_cards(reviewed)
  rejected = next(card for card in data.cards if card.gap_id == _REJECTED_ID)

  context = surfaced_gap_context(data, _corpus_context(), _ALL_LEVELS)

  assert rejected.title not in {gap.title for gap in context.gaps}


def test_accepting_a_rejected_gap_surfaces_it_and_records_the_override(
  reviewed: Path,
):
  """The researcher's acceptance outranks the review and is logged with its layer."""
  record_judgment(
    reviewed, _REJECTED_ID, "accepted", overrode_review="taxonomy_roll_up"
  )

  data = _reviewed_cards(reviewed)
  shown = filter_gap_cards(data, confidence_levels=_ALL_LEVELS)

  assert _REJECTED_ID in {card.gap_id for card in shown.cards}
  assert shown.review_hidden_count == 0
  assert _REJECTED_ID in {card.gap_id for card in data.reviewed_cards}
  judgment = load_judgments(reviewed).by_gap_id()[_REJECTED_ID]
  assert (judgment.decision, judgment.overrode_review) == (
    "accepted",
    "taxonomy_roll_up",
  )


def test_the_page_lists_rejected_gaps_collapsed_and_accepts_one(
  reviewed: Path, monkeypatch: pytest.MonkeyPatch
):
  """'Rejected by review (N)' lists every verdict; Accept there is recorded."""
  app = _run_gap_cards_app(reviewed, monkeypatch)
  app.multiselect[0].set_value(_ALL_LEVELS).run()
  count = len(_reviewed_cards(reviewed).reviewed_cards)

  assert not app.exception
  label = text.GAP_REVIEW_SECTION_LABEL.format(count=count)
  section = next(e for e in app.expander if e.label == label)
  assert not section.proto.expanded
  section_text = " ".join(block.value for block in section.markdown)
  assert "taxonomy artifact" in section_text
  assert f"accept-{_REJECTED_ID}" not in {button.key for button in app.button}
  assert any(
    text.GAP_REVIEW_HIDDEN_NOTE.format(count=1) == c.value for c in app.caption
  )

  app.button(key=f"review-accept-{_REJECTED_ID}").click().run()

  assert not app.exception
  judgment = load_judgments(reviewed).by_gap_id()[_REJECTED_ID]
  assert (judgment.decision, judgment.overrode_review) == (
    "accepted",
    "taxonomy_roll_up",
  )
  assert f"accept-{_REJECTED_ID}" in {button.key for button in app.button}
