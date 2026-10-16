"""Behavior of the L5 review (`gap-review-v1`) inside the detect stage."""

import json
import shutil
from pathlib import Path
from typing import Any

from conftest import StubLlmClient
from test_detect import (
  _GROUPING,
  _NONE_ADDRESSED,
  _ROLL_UP,
  _ROLL_UP_PARENTS,
  KEEP_REVIEW,
  Placements,
  RoutedLlmClient,
  _balanced_placements,
  _gap,
  _limitations_root,
  _write_corpus,
)

from research_gap_dashboard.detect import (
  CandidateGapsReport,
  CellGap,
  UnansweredLimitationGap,
  detect_corpus,
  read_candidate_gaps,
)
from research_gap_dashboard.limitations import (
  FOLLOW_UP_PROMPT_VERSION,
  GROUP_PROMPT_VERSION,
)
from research_gap_dashboard.llm import CachingLlmClient, DiskCache, RecordedLlmClient
from research_gap_dashboard.review import REVIEW_PROMPT_VERSION

FIXTURE = Path(__file__).parent / "fixtures" / "gap-review"


def _review_steps(gap: Any) -> list[Any]:
  """List the candidate's L5 steps (at most one)."""
  return [step for step in gap.trail if step.layer == "l5_review"]


def _fixture_report(tmp_path: Path, client: Any = None) -> CandidateGapsReport:
  """Detect over a scratch copy of the fixture Corpus with the recorded answers."""
  root = tmp_path / "corpus"
  if not root.exists():
    shutil.copytree(FIXTURE / "corpus", root)
  recorded = RecordedLlmClient.from_file(FIXTURE / "llm-responses.json")
  return detect_corpus(root, client or recorded)


class _CountingClient:  # pylint: disable=too-few-public-methods
  """Passes calls through to an inner client, counting the ones that reach it."""

  name = "counting"

  def __init__(self, inner: Any):
    self.inner = inner
    self.calls = 0

  def complete(self, prompt: str, schema: dict[str, Any], **kwargs: Any) -> Any:
    """Count the call and delegate it."""
    self.calls += 1
    return self.inner.complete(prompt, schema, **kwargs)


def test_low_candidates_make_no_review_call(tmp_path: Path) -> None:
  """Only live Medium+ cells are reviewed; Low and rejected ones are not."""
  client = RoutedLlmClient({})
  report = detect_corpus(
    _write_corpus(tmp_path, _ROLL_UP, parents=_ROLL_UP_PARENTS), client
  )

  cells = [gap for gap in report.gaps if isinstance(gap, CellGap)]
  reviewed = [gap for gap in cells if gap.review is not None]
  assert client.calls.get(REVIEW_PROMPT_VERSION, 0) == len(reviewed)
  for gap in cells:
    before = [s for s in gap.trail if s.layer != "l5_review"]
    due = before[-1].level_after != "low" and not any(
      s.effect == "reject" for s in before
    )
    assert (gap.review is not None) == due, gap.gap_id
    assert len(_review_steps(gap)) == (1 if due else 0), gap.gap_id
  lowered = _gap(report, "topicxmethod:takotsubo:rct")
  assert lowered.confidence == "low"
  assert lowered.review is None
  assert _gap(report, "topicxtopic:cardiomyopathies:takotsubo").review is None


def test_the_recorded_review_keeps_a_gap_with_its_card_fields(tmp_path: Path) -> None:
  """A kept gap carries claim, grounds, warrant, Audit Basis and Minimal Study."""
  gap = _gap(_fixture_report(tmp_path), "topicxmethod:takotsubo:rct")

  review = gap.review
  assert review is not None
  assert review.verdict == "keep"
  assert review.claim
  assert review.grounds == ["tako1:research_question[0]", "tako2:research_question[0]"]
  assert review.warrant
  assert review.audit_basis is not None
  assert review.audit_basis.evidence_ids == [
    "tako1:research_question[0]",
    "hf1:study_design[0]",
  ]
  assert review.minimal_study
  assert review.notes == []
  assert [(s.effect, s.level_after) for s in _review_steps(gap)] == [("none", "high")]
  assert gap.confidence == "high"
  assert not gap.rejected


def test_a_rejected_candidate_stays_with_its_layer_and_class(tmp_path: Path) -> None:
  """A pseudo-firstness reject is kept in the artifact, named and explained."""
  _fixture_report(tmp_path)

  gap = _gap(
    read_candidate_gaps(tmp_path / "corpus"), "topicxtopic:heart-failure:takotsubo"
  )

  assert gap.rejected
  last = gap.trail[-1]
  assert (last.layer, last.effect) == ("l5_review", "reject")
  assert "pseudo-firstness" in last.reason
  assert gap.review is not None
  assert gap.review.rejection_class == "pseudo_firstness"
  assert gap.review.audit_basis is None
  assert gap.review.minimal_study is None


def test_an_unknown_ground_id_is_dropped_and_noted(tmp_path: Path) -> None:
  """A ground outside the supplied Evidence never reaches the card."""
  gap = _gap(_fixture_report(tmp_path), "topicxmethod:heart-failure:retrospective")

  review = gap.review
  assert review is not None
  assert review.grounds == ["hf1:research_question[0]"]
  assert any("zz9999:methods[0]" in note for note in review.notes)
  # least_probable: one level down from High.
  assert [(s.effect, s.level_after) for s in _review_steps(gap)] == [
    ("lower", "medium")
  ]
  assert gap.confidence == "medium"


def test_the_artifact_records_the_review_prompt_version(tmp_path: Path) -> None:
  """The review's prompt version is provenance, like the limitation prompts'."""
  report = _fixture_report(tmp_path)

  assert report.review_prompt_version == REVIEW_PROMPT_VERSION
  assert all(
    gap.review is None or gap.review.prompt_version == REVIEW_PROMPT_VERSION
    for gap in report.gaps
  )


def test_a_rerun_on_the_same_inputs_makes_no_paid_call(tmp_path: Path) -> None:
  """Reviews are cached by their inputs, so a second run costs nothing."""
  inner = _CountingClient(RecordedLlmClient.from_file(FIXTURE / "llm-responses.json"))
  client = CachingLlmClient(inner, DiskCache(tmp_path / "cache"))

  first = _fixture_report(tmp_path, client)
  assert inner.calls == 3
  second = _fixture_report(tmp_path, client)

  assert inner.calls == 3
  assert second.gaps == first.gaps


def _cut_point_placements() -> Placements:
  """Takotsubo x RCT is empty with 4 x 4 of 10 Papers: Medium (about 0.071)."""
  return {
    **{f"a{i}": [("topic", "takotsubo")] for i in range(4)},
    **{f"b{i}": [("method", "rct")] for i in range(4)},
    **{f"c{i}": [("topic", "heart-failure")] for i in range(2)},
  }


def test_a_confident_review_never_raises_a_level(tmp_path: Path) -> None:
  """A more_probable keep leaves a Medium candidate at Medium."""
  report = detect_corpus(
    _write_corpus(tmp_path, _cut_point_placements()), RoutedLlmClient({})
  )

  gap = _gap(report, "topicxmethod:takotsubo:rct")
  assert gap.trail[0].level_after == "medium"
  assert [(s.effect, s.level_after) for s in _review_steps(gap)] == [("none", "medium")]
  assert gap.confidence == "medium"


def test_a_review_with_no_usable_answer_changes_nothing(tmp_path: Path) -> None:
  """A failed review is recorded on the trail; the level stands."""
  report = detect_corpus(
    _write_corpus(tmp_path, _balanced_placements()), StubLlmClient({})
  )

  gap = _gap(report, "topicxmethod:takotsubo:rct")
  assert gap.review is None
  steps = _review_steps(gap)
  assert [(s.effect, s.level_after) for s in steps] == [("none", "high")]
  assert "Not reviewed" in steps[0].reason
  assert gap.confidence == "high"


def test_a_keep_without_a_bucket_is_read_as_least_probable(tmp_path: Path) -> None:
  """A keep that does not rate itself is never left above a hedged one."""
  unrated = {**KEEP_REVIEW, "bucket": None}
  client = RoutedLlmClient({REVIEW_PROMPT_VERSION: [unrated]})
  report = detect_corpus(_write_corpus(tmp_path, _cut_point_placements()), client)

  gap = _gap(report, "topicxmethod:takotsubo:rct")
  assert gap.review is not None
  assert gap.review.bucket == "least_probable"
  assert gap.review.notes
  assert gap.confidence == "low"


def test_unanswered_limitations_are_all_reviewed(tmp_path: Path) -> None:
  """Every limitation card is reviewed; a reject stays, a hedge lowers one level."""
  weak = {
    **KEEP_REVIEW,
    "bucket": "least_probable",
    "grounds": ["early1:future_work[0]"],
  }
  generic = {
    "verdict": "reject",
    "rejection_class": "generic_upgrade",
    "warrant": "A bigger sample alone names no new question.",
  }
  client = RoutedLlmClient(
    {
      GROUP_PROMPT_VERSION: [_GROUPING],
      FOLLOW_UP_PROMPT_VERSION: [_NONE_ADDRESSED],
      REVIEW_PROMPT_VERSION: [weak, generic],
    }
  )

  detect_corpus(_limitations_root(tmp_path), client)

  cards = {
    gap.title: gap
    for gap in read_candidate_gaps(tmp_path).gaps
    if isinstance(gap, UnansweredLimitationGap)
  }
  assert client.calls[REVIEW_PROMPT_VERSION] == len(cards) == 2
  sex = cards["Sex differences"]
  assert [(s.layer, s.effect) for s in sex.trail] == [
    ("limitation_follow_up", "start"),
    ("l5_review", "lower"),
  ]
  assert (sex.trail[0].level_after, sex.confidence) == ("medium", "low")
  assert sex.review is not None
  assert sex.review.grounds == ["early1:future_work[0]"]
  sample = cards["Small sample size"]
  assert sample.rejected
  assert sample.review is not None
  assert sample.review.rejection_class == "generic_upgrade"
  assert "generic upgrade" in sample.trail[-1].reason


def test_a_limitation_card_written_before_trails_still_reads(tmp_path: Path) -> None:
  """An old limitation card gains its follow-up start step from its confidence."""
  client = RoutedLlmClient(
    {GROUP_PROMPT_VERSION: [_GROUPING], FOLLOW_UP_PROMPT_VERSION: [_NONE_ADDRESSED]}
  )
  root = _limitations_root(tmp_path)
  detect_corpus(root, client)
  artifact = root / "artifacts" / "candidate_gaps.json"
  data = json.loads(artifact.read_text(encoding="utf-8"))
  for gap in data["gaps"]:
    gap.pop("trail", None)
    gap.pop("review", None)
  data.pop("review_prompt_version")
  artifact.write_text(json.dumps(data), encoding="utf-8")

  report = read_candidate_gaps(root)

  assert report.review_prompt_version is None
  card = next(g for g in report.gaps if isinstance(g, UnansweredLimitationGap))
  assert [(s.layer, s.effect, s.level_after) for s in card.trail] == [
    ("limitation_follow_up", "start", card.confidence)
  ]
  assert card.review is None
