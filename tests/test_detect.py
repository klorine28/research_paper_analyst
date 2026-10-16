"""Behavior of the detect stage: artifacts in, Candidate Gap cards out."""

import json
import re
from pathlib import Path
from typing import Any

import pytest

from conftest import StubLlmClient

from research_gap_dashboard.aggregate import (
  AggregateReport,
  CategoryAssignment,
  NormalizedFacts,
  TaxonomyProvenance,
)
from research_gap_dashboard import cli
from research_gap_dashboard.cli import EXIT_ERROR, EXIT_OK, main
from research_gap_dashboard.detect import (
  CANDIDATE_GAPS_NAME,
  PREVIOUS_CANDIDATE_GAPS_NAME,
  CandidateGapsReport,
  CellGap,
  L1Margins,
  UnansweredLimitationGap,
  detect_corpus,
  read_candidate_gaps,
  l1_probability,
)
from research_gap_dashboard.extract import (
  Evidence,
  ExtractedFact,
  Extraction,
  ExtractionFields,
  ExtractReport,
)
from research_gap_dashboard.ingest import CorpusManifest, Paper
from research_gap_dashboard.limitations import (
  FOLLOW_UP_PROMPT_VERSION,
  GROUP_PROMPT_VERSION,
)
from research_gap_dashboard.review import REVIEW_PROMPT_VERSION
from research_gap_dashboard.sources import OpenAlexAdapter, PubMedAdapter
from research_gap_dashboard.taxonomy import Axis

Placements = dict[str, list[tuple[Axis, str]]]

# (axis, category id) placements per Paper. Takotsubo is studied by four Papers
# with retrospective designs, heart failure by two with an RCT; nobody combines
# Takotsubo with an RCT, and only one Paper combines heart failure with a
# retrospective design.
_PLACEMENTS: Placements = {
  "p01": [("topic", "takotsubo"), ("method", "retrospective")],
  "p02": [("topic", "takotsubo"), ("method", "retrospective")],
  "p03": [("topic", "takotsubo"), ("method", "retrospective")],
  "p04": [("topic", "takotsubo"), ("method", "retrospective")],
  "p05": [("topic", "heart-failure"), ("method", "rct")],
  "p06": [
    ("topic", "heart-failure"),
    ("method", "retrospective"),
    ("population", "aged"),
  ],
}

_LABELS = {
  "takotsubo": "Takotsubo Cardiomyopathy",
  "heart-failure": "Heart Failure",
  "retrospective": "Retrospective Studies",
  "rct": "Randomized Controlled Trial",
  "aged": "Aged",
  "cardiomyopathies": "Cardiomyopathies",
  "dilated": "Dilated Cardiomyopathy",
  "cohort": "Cohort Studies",
}


def _facts(key: str, placements: list[tuple[Axis, str]]) -> NormalizedFacts:
  """Build one Paper's NormalizedFacts, each placement backed by Evidence."""
  return NormalizedFacts(
    citation_key=key,
    assignments=[
      CategoryAssignment(
        axis=axis,
        original_term=f"{category} term",
        category_id=category,
        category_label=_LABELS[category],
        fact_ref=f"{axis}s[{index}]",
        evidence=Evidence(passage=f"{key} passage on {category}", section="methods"),
      )
      for index, (axis, category) in enumerate(placements)
    ],
    unmapped=[],
  )


def _write_corpus(
  root: Path,
  placements: Placements,
  *,
  manifest_keys: list[str] | None = None,
  extractions: list[Extraction] | None = None,
  years: dict[str, int | None] | None = None,
  parents: dict[Axis, dict[str, str]] | None = None,
) -> Path:
  """Write the manifest, Extractions, and NormalizedFacts for a synthetic Corpus."""
  for name in ("papers", "paper-data", "artifacts", "judgments"):
    (root / name).mkdir(parents=True, exist_ok=True)
  (root / "corpus.bib").write_text("", encoding="utf-8")

  keys = manifest_keys if manifest_keys is not None else list(placements)
  manifest = CorpusManifest(
    corpus_root=root,
    papers=[
      Paper(
        citation_key=key,
        doi=f"10.1/{key}",
        pdf_path=root / f"{key}.pdf",
        year=(years or {}).get(key),
      )
      for key in keys
    ],
    unmatched_entries=[],
    orphan_pdfs=[],
  )
  normalized = AggregateReport(
    corpus_root=root,
    prompt_version="aggregate-v2",
    tier="default",
    taxonomies=[
      TaxonomyProvenance(
        axis=axis,
        domain="test",
        source="test",
        category_count=len(axis_parents),
        parents=axis_parents,
      )
      for axis, axis_parents in (parents or {}).items()
    ],
    normalized=[_facts(key, value) for key, value in placements.items()],
    failures=[],
  )
  extracted = ExtractReport(
    corpus_root=root,
    prompt_version="extract-v1",
    tier="default",
    extractions=extractions or [],
    failures=[],
  )
  artifacts = root / "artifacts"
  (artifacts / "extractions.json").write_text(
    extracted.model_dump_json(indent=2), encoding="utf-8"
  )
  (artifacts / "corpus-manifest.json").write_text(
    manifest.model_dump_json(indent=2), encoding="utf-8"
  )
  (artifacts / "normalized_facts.json").write_text(
    normalized.model_dump_json(indent=2), encoding="utf-8"
  )
  return root


# A review that keeps the candidate at its level: what the fake LLMs answer to
# the L5 review unless a test scripts something else.
KEEP_REVIEW: dict[str, Any] = {
  "verdict": "keep",
  "rejection_class": None,
  "claim": "The combination is unstudied in the Corpus.",
  "grounds": [],
  "warrant": "Both categories are well covered, never together.",
  "audit_basis": None,
  "minimal_study": None,
  "bucket": "more_probable",
}


def _detect(root: Path, client: Any = None) -> CandidateGapsReport:
  """Run detect with a fake LLM whose review keeps every candidate at its level."""
  return detect_corpus(root, client or RoutedLlmClient({}))


def _before_review(trail: list[Any]) -> list[tuple[str, str, str]]:
  """List the deterministic layers' steps: every step but the L5 review's."""
  return [(s.layer, s.effect, s.level_after) for s in trail if s.layer != "l5_review"]


def _gap(report: CandidateGapsReport, gap_id: str) -> CellGap:
  """Return the cell gap card with this id, failing the test if it is absent."""
  by_id = {gap.gap_id: gap for gap in report.gaps}
  assert gap_id in by_id, f"{gap_id} not in {sorted(by_id)}"
  gap = by_id[gap_id]
  assert isinstance(gap, CellGap)
  return gap


def test_matrices_cover_all_three_axis_pairs_and_topic_pairs(tmp_path: Path) -> None:
  """A Topic × Topic and a Topic × Method/Population/Dataset matrix are built."""
  report = _detect(_write_corpus(tmp_path, _PLACEMENTS))

  assert [(m.row_axis, m.column_axis) for m in report.matrices] == [
    ("topic", "topic"),
    ("topic", "method"),
    ("topic", "population"),
    ("topic", "dataset"),
  ]
  by_pair = {(m.row_axis, m.column_axis): m for m in report.matrices}
  method = by_pair[("topic", "method")]
  counts = {(c.row_id, c.column_id): c.paper_count for c in method.cells}
  assert counts == {
    ("heart-failure", "rct"): 1,
    ("heart-failure", "retrospective"): 1,
    ("takotsubo", "rct"): 0,
    ("takotsubo", "retrospective"): 4,
  }
  assert method.corpus_paper_count == 6
  # Only observed categories become rows and columns; no Paper has a dataset.
  assert by_pair[("topic", "dataset")].columns == []


def test_every_empty_cell_is_a_candidate(tmp_path: Path) -> None:
  """An empty cell between two observed categories is a candidate gap."""
  report = _detect(_write_corpus(tmp_path, _PLACEMENTS))

  gap = _gap(report, "topicxmethod:takotsubo:rct")
  assert gap.gap_type == "coverage_gap"
  assert gap.cell_count == 0
  assert gap.corpus_paper_count == 6


def test_single_paper_cell_is_a_candidate_only_when_more_were_expected(
  tmp_path: Path,
) -> None:
  """One Paper where more than one was expected is a candidate; otherwise not."""
  report = _detect(_write_corpus(tmp_path, _PLACEMENTS))
  ids = {g.gap_id for g in report.gaps}

  # 2 heart-failure x 5 retrospective / 6 Papers: about 1.7 expected, 1 observed.
  gap = _gap(report, "topicxmethod:heart-failure:retrospective")
  assert gap.cell_count == 1
  assert gap.cell_citation_keys == ["p06"]
  assert "only 1 Paper" in gap.explanation
  assert {link.role for link in gap.evidence} >= {"cell"}
  # 2 heart-failure x 1 RCT / 6: about 0.3 expected, 1 observed.
  assert "topicxmethod:heart-failure:rct" not in ids
  # 2 heart-failure x 1 aged / 6: about 0.3 expected, 1 observed.
  assert "topicxpopulation:heart-failure:aged" not in ids
  # 4 Takotsubo x 5 retrospective: well covered.
  assert "topicxmethod:takotsubo:retrospective" not in ids


def test_the_candidate_rule_is_the_same_at_every_corpus_size(tmp_path: Path) -> None:
  """A single-Paper cell with about one Paper expected stays out at 25 Papers too."""
  placements: Placements = {
    **{f"a{i:02d}": [("topic", "takotsubo")] for i in range(20)},
    "b00": [("topic", "takotsubo"), ("method", "rct")],
    **{f"c{i:02d}": [("method", "retrospective")] for i in range(4)},
  }

  report = _detect(_write_corpus(tmp_path, placements))

  assert report.corpus_paper_count == 25
  # 21 Takotsubo x 1 RCT / 25 Papers: 0.84 expected, 1 observed.
  assert "topicxmethod:takotsubo:rct" not in {g.gap_id for g in report.gaps}
  assert "empty" in report.candidate_rule
  assert "more than one" in report.candidate_rule


@pytest.mark.parametrize(
  ("observed", "row", "column", "total", "expected"),
  [
    (0, 5, 5, 10, 1 / 252),
    (1, 5, 5, 10, 26 / 252),
    (0, 3, 3, 10, 35 / 120),
    (0, 4, 1, 6, 2 / 6),
    (0, 6, 6, 10, 0.0),
    (2, 2, 3, 6, 1.0),
  ],
)
def test_l1_is_the_hypergeometric_lower_tail(
  observed: int, row: int, column: int, total: int, expected: float
) -> None:
  """P(X <= observed) for a cell, given its two category sizes and the Corpus."""
  assert l1_probability(observed, row, column, total) == pytest.approx(expected)


def _balanced_placements() -> Placements:
  """Ten Papers: five Takotsubo retrospective, five heart-failure RCT."""
  return {
    **{
      f"a{i}": [("topic", "takotsubo"), ("method", "retrospective")] for i in range(5)
    },
    **{f"b{i}": [("topic", "heart-failure"), ("method", "rct")] for i in range(5)},
  }


def test_an_empty_cell_between_two_halves_ranks_high(tmp_path: Path) -> None:
  """N = 10, margins 5 and 5, empty: about 0.004, so the L1 level is High."""
  report = _detect(_write_corpus(tmp_path, _balanced_placements()))

  gap = _gap(report, "topicxmethod:takotsubo:rct")
  assert gap.l1_probability == pytest.approx(0.00397, abs=1e-5)
  assert gap.confidence == "high"
  assert _before_review(gap.trail) == [("l1", "start", "high")]
  assert "2.5 of 10 Papers" in gap.confidence_reason
  assert "1 time in 252" in gap.confidence_reason


def test_l1_levels_follow_the_probability_cut_points(tmp_path: Path) -> None:
  """At most 0.05 is High, at most 0.20 Medium, anything above Low."""
  placements: Placements = {
    **{f"a{i}": [("topic", "takotsubo")] for i in range(4)},
    **{f"b{i}": [("method", "rct")] for i in range(4)},
    **{f"c{i}": [("topic", "heart-failure")] for i in range(2)},
  }
  report = _detect(_write_corpus(tmp_path, placements))

  # 4 x 4 of 10, empty: 15/210, about 0.071.
  medium = _gap(report, "topicxmethod:takotsubo:rct")
  assert medium.l1_probability == pytest.approx(15 / 210)
  assert medium.confidence == "medium"
  # 2 x 4 of 10, empty: 15/45, about 0.33.
  low = _gap(report, "topicxmethod:heart-failure:rct")
  assert low.l1_probability == pytest.approx(15 / 45)
  assert low.confidence == "low"


def test_the_artifact_records_the_smallest_margins_per_level(tmp_path: Path) -> None:
  """For N = 10, equal margins of 5 can reach High and 4 Medium."""
  report = _detect(_write_corpus(tmp_path, _balanced_placements()))

  assert report.l1_smallest_margins == L1Margins(high=5, medium=4)


def test_no_card_text_claims_significance(tmp_path: Path) -> None:
  """The L1 score is never worded as a significance test or a p-value."""
  report = _detect(_write_corpus(tmp_path, _balanced_placements()))

  for gap in report.gaps:
    if isinstance(gap, CellGap):
      texts = [gap.explanation, gap.confidence_reason] + [s.reason for s in gap.trail]
      for line in texts:
        assert "significan" not in line.lower()
        assert re.search(r"\bp\b", line, flags=re.IGNORECASE) is None, line


def test_topic_pairs_never_studied_together_are_knowledge_gaps(tmp_path: Path) -> None:
  """Two observed Topics no Paper combines form one Knowledge Gap, not two."""
  report = _detect(_write_corpus(tmp_path, _PLACEMENTS))

  knowledge = [g for g in report.gaps if g.gap_type == "knowledge_gap"]
  assert [g.gap_id for g in knowledge] == ["topicxtopic:heart-failure:takotsubo"]


def test_every_card_states_count_and_links_evidence(tmp_path: Path) -> None:
  """An empty cell cites the Papers establishing both of its categories."""
  report = _detect(_write_corpus(tmp_path, _PLACEMENTS))

  gap = _gap(report, "topicxmethod:takotsubo:rct")
  cited = {(link.role, link.citation_key) for link in gap.evidence}
  assert cited == {
    ("row", "p01"),
    ("row", "p02"),
    ("row", "p03"),
    ("row", "p04"),
    ("column", "p05"),
  }
  assert gap.evidence[0].evidence.passage == "p01 passage on takotsubo"
  assert "4 of 6 Papers" in gap.explanation
  assert "not a verdict" in gap.explanation
  for card in report.gaps:
    assert card.evidence
    if isinstance(card, CellGap):
      assert card.cell_count <= 1


def test_confidence_grows_with_how_expected_the_combination_was(
  tmp_path: Path,
) -> None:
  """An empty cell between two common categories outranks one between rare ones."""
  report = _detect(_write_corpus(tmp_path, _PLACEMENTS))

  # 4 Takotsubo x 1 RCT / 6 Papers: under one Paper expected.
  assert _gap(report, "topicxmethod:takotsubo:rct").confidence == "low"
  # 4 Takotsubo x 1 aged-population / 6: also under one.
  assert _gap(report, "topicxpopulation:takotsubo:aged").confidence == "low"

  common: Placements = {
    **{
      f"a{i}": [("topic", "takotsubo"), ("method", "retrospective")] for i in range(5)
    },
    **{f"b{i}": [("topic", "heart-failure"), ("method", "rct")] for i in range(5)},
    **{f"c{i}": [("method", "retrospective")] for i in range(5)},
  }
  crowded = _detect(_write_corpus(tmp_path / "crowded", common))
  # 5 Takotsubo x 5 RCT / 15 Papers: about 1.7 expected, none observed.
  gap = _gap(crowded, "topicxmethod:takotsubo:rct")
  assert gap.confidence == "medium"
  assert "1.7" in gap.confidence_reason


def test_papers_outside_the_manifest_are_never_cited(tmp_path: Path) -> None:
  """NormalizedFacts for a Paper not in the Corpus are excluded and listed."""
  placements: Placements = {
    **_PLACEMENTS,
    "stranger": [("topic", "takotsubo"), ("method", "rct")],
  }
  root = _write_corpus(tmp_path, placements, manifest_keys=list(_PLACEMENTS))

  report = _detect(root)

  assert report.excluded_citation_keys == ["stranger"]
  assert report.corpus_paper_count == 6
  cited = {link.citation_key for gap in report.gaps for link in gap.evidence}
  assert cited <= set(_PLACEMENTS)
  # With the stranger excluded, Takotsubo x RCT is still an empty cell.
  assert _gap(report, "topicxmethod:takotsubo:rct").cell_count == 0


def test_same_artifact_in_gives_same_gaps_out(tmp_path: Path) -> None:
  """Detection is deterministic: two runs write byte-identical artifacts."""
  root = _write_corpus(tmp_path, _PLACEMENTS)
  artifact = root / "artifacts" / "candidate_gaps.json"

  _detect(root)
  first = artifact.read_bytes()
  _detect(root)

  assert artifact.read_bytes() == first
  assert read_candidate_gaps(root).gaps == _detect(root).gaps


def test_cli_detect_writes_the_artifact(tmp_path: Path, monkeypatch) -> None:
  """The detect command writes the artifact through the (here faked) LLM client."""
  root = _write_corpus(tmp_path, _PLACEMENTS)
  monkeypatch.setattr(cli, "build_llm_client", lambda **_: StubLlmClient({}))

  assert main(["detect", str(root)]) == EXIT_OK
  assert (root / "artifacts" / "candidate_gaps.json").is_file()


def test_cli_detect_makes_no_literature_search_calls(
  tmp_path: Path, monkeypatch
) -> None:
  """Saturation checks run after detect, never inside it (ADR 0009 §5)."""
  root = _write_corpus(tmp_path, _PLACEMENTS)
  monkeypatch.setattr(cli, "build_llm_client", lambda **_: StubLlmClient({}))

  def _no_search(*_args: Any, **_kwargs: Any) -> Any:
    raise AssertionError("detect must not search the wider literature")

  monkeypatch.setattr(cli, "_build_search_sources", _no_search)
  monkeypatch.setattr(OpenAlexAdapter, "search", _no_search)
  monkeypatch.setattr(PubMedAdapter, "search", _no_search)

  assert main(["detect", str(root)]) == EXIT_OK
  assert not (root / "artifacts" / "saturation_checks.json").exists()


def test_cli_detect_records_the_run_llm_usage(tmp_path: Path, monkeypatch) -> None:
  """The detect command records what it spent on the LLM in the usage log."""
  root = _write_corpus(tmp_path, _PLACEMENTS)

  def _client(**kwargs):
    kwargs["usage"].record("model-x", {"input_tokens": 5, "output_tokens": 7})
    return StubLlmClient({})

  monkeypatch.setattr(cli, "build_llm_client", _client)

  assert main(["detect", str(root)]) == EXIT_OK
  recorded = json.loads((root / "artifacts" / "llm-usage.json").read_text())
  assert recorded["detect"]["by_model"]["model-x"]["output_tokens"] == 7


def test_cli_detect_before_aggregate_fails_clearly(tmp_path: Path, monkeypatch) -> None:
  """Without the NormalizedFacts artifact the command exits with an error."""
  root = _write_corpus(tmp_path, _PLACEMENTS)
  (root / "artifacts" / "normalized_facts.json").unlink()
  monkeypatch.setattr(cli, "build_llm_client", lambda **_: StubLlmClient({}))

  assert main(["detect", str(root)]) == EXIT_ERROR


def test_cli_detect_requires_an_api_key(tmp_path: Path, monkeypatch) -> None:
  """Limitation grouping needs the LLM, so a missing key fails loudly."""
  root = _write_corpus(tmp_path, _PLACEMENTS)
  monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

  assert main(["detect", str(root)]) == EXIT_ERROR


class RoutedLlmClient:  # pylint: disable=too-few-public-methods
  """
  A keyless fake LLM answering each prompt version from its own script.

  An unscripted review is answered with `KEEP_REVIEW`, any other unscripted
  prompt with an empty object.
  """

  name = "routed"

  def __init__(self, scripts: dict[str, list[dict[str, Any]]]):
    self.scripts = {REVIEW_PROMPT_VERSION: [KEEP_REVIEW], **scripts}
    self.calls: dict[str, int] = {}

  def complete(  # pylint: disable=unused-argument
    self,
    prompt: str,
    schema: dict[str, Any],
    *,
    prompt_version: str,
    tier: str = "default",
    refresh: bool = False,
  ) -> dict[str, Any]:
    """Return the next scripted result for this prompt version."""
    script = self.scripts.get(prompt_version, [{}])
    count = self.calls.get(prompt_version, 0)
    self.calls[prompt_version] = count + 1
    return script[min(count, len(script) - 1)]


def _fact(statement: str, passage: str) -> ExtractedFact:
  """Build one extracted fact with its Evidence."""
  return ExtractedFact(
    statement=statement, evidence=Evidence(passage=passage, section="discussion")
  )


def _limitation_corpus() -> list[Extraction]:
  """
  Two 2018 Papers share a small-sample limitation; one also asks about women.

  A 2020 Paper studies a large multicentre cohort; a 2018 peer and a Paper of
  unknown year can never count as later.
  """
  return [
    Extraction(
      citation_key="early1",
      fields=ExtractionFields(
        limitations=[_fact("The sample was small.", "our sample was small")],
        future_work=[_fact("Sex differences need study.", "women warrant study")],
      ),
    ),
    Extraction(
      citation_key="early2",
      fields=ExtractionFields(
        limitations=[_fact("Only 40 patients.", "only 40 patients were enrolled")]
      ),
    ),
    Extraction(
      citation_key="later1",
      fields=ExtractionFields(
        methods=[_fact("A multicentre cohort of 2000 patients.", "2000 patients")]
      ),
    ),
    Extraction(
      citation_key="undated",
      fields=ExtractionFields(key_findings=[_fact("Outcomes varied.", "varied")]),
    ),
  ]


_YEARS: dict[str, int | None] = {
  "early1": 2018,
  "early2": 2018,
  "later1": 2020,
  "undated": None,
}

_GROUPING = {
  "groups": [
    {
      "label": "Small sample size",
      "statement_ids": ["early1:limitations[0]", "early2:limitations[0]"],
    },
    {"label": "Sex differences", "statement_ids": ["early1:future_work[0]"]},
  ]
}

_SAMPLE_ADDRESSED = {
  "follow_ups": [
    {"fact_id": "later1:methods[0]", "reason": "Enrolls a far larger cohort."}
  ]
}
_NONE_ADDRESSED: dict[str, Any] = {"follow_ups": []}


def _limitations_root(tmp_path: Path) -> Path:
  """Write a Corpus whose Extractions carry the limitation fixture."""
  keys = list(_YEARS)
  return _write_corpus(
    tmp_path,
    {key: [] for key in keys},
    extractions=_limitation_corpus(),
    years=_YEARS,
  )


def _limitation_gaps(report: CandidateGapsReport) -> list[UnansweredLimitationGap]:
  """Return only the Unanswered Limitation cards."""
  return [g for g in report.gaps if isinstance(g, UnansweredLimitationGap)]


def test_limitations_are_grouped_with_follow_up_counts(tmp_path: Path) -> None:
  """Every group, addressed or not, is kept with its follow-ups in the artifact."""
  client = RoutedLlmClient(
    {
      GROUP_PROMPT_VERSION: [_GROUPING],
      FOLLOW_UP_PROMPT_VERSION: [_SAMPLE_ADDRESSED, _NONE_ADDRESSED],
    }
  )

  report = detect_corpus(_limitations_root(tmp_path), client)

  groups = {g.label: g for g in read_candidate_gaps(tmp_path).limitation_groups}
  sample = groups["Small sample size"]
  assert sample.source_citation_keys == ["early1", "early2"]
  assert sample.later_citation_keys == ["later1"]
  assert [(f.citation_key, f.evidence.passage) for f in sample.follow_ups] == [
    ("later1", "2000 patients")
  ]
  assert groups["Sex differences"].follow_ups == []
  assert report.extracted_paper_count == 4


def test_only_unaddressed_groups_become_cards_citing_their_passages(
  tmp_path: Path,
) -> None:
  """An addressed group gets no card; an unaddressed one cites its sources."""
  client = RoutedLlmClient(
    {
      GROUP_PROMPT_VERSION: [_GROUPING],
      FOLLOW_UP_PROMPT_VERSION: [_SAMPLE_ADDRESSED, _NONE_ADDRESSED],
    }
  )

  report = detect_corpus(_limitations_root(tmp_path), client)

  cards = _limitation_gaps(report)
  assert [c.title for c in cards] == ["Sex differences"]
  card = cards[0]
  assert card.gap_type == "unanswered_limitation"
  assert [(e.citation_key, e.evidence.passage) for e in card.evidence] == [
    ("early1", "women warrant study")
  ]
  assert card.later_paper_count == 1
  assert card.confidence == "medium"
  assert "not a verdict" in card.explanation


def test_papers_of_the_same_or_unknown_year_are_not_later(tmp_path: Path) -> None:
  """A group whose sources are the latest Papers is checked against nobody."""
  only_later = [
    Extraction(
      citation_key="later1",
      fields=ExtractionFields(
        methods=[_fact("A cohort of 2000 patients.", "2000 patients")],
        limitations=[_fact("Single centre.", "one centre")],
      ),
    ),
    *_limitation_corpus()[:2],
  ]
  grouping = {
    "groups": [
      {"label": "Single centre", "statement_ids": ["later1:limitations[0]"]},
      {
        "label": "Small sample size",
        "statement_ids": [
          "early1:limitations[0]",
          "early2:limitations[0]",
          "early1:future_work[0]",
        ],
      },
    ]
  }
  client = RoutedLlmClient(
    {GROUP_PROMPT_VERSION: [grouping], FOLLOW_UP_PROMPT_VERSION: [_NONE_ADDRESSED]}
  )
  root = _write_corpus(
    tmp_path, {key: [] for key in _YEARS}, extractions=only_later, years=_YEARS
  )

  report = detect_corpus(root, client)

  single = next(c for c in _limitation_gaps(report) if c.title == "Single centre")
  assert single.later_paper_count == 0
  assert single.confidence == "low"
  # Only the 2018 group had a later Paper to check.
  assert client.calls[FOLLOW_UP_PROMPT_VERSION] == 1


def test_model_proposals_are_validated_and_corrections_noted(tmp_path: Path) -> None:
  """Invented ids never enter a group; a forgotten statement is kept alone."""
  sloppy = {
    "groups": [
      {
        "label": "Small sample size",
        "statement_ids": ["early1:limitations[0]", "ghost:limitations[9]"],
      }
    ]
  }
  invented_follow_up = {
    "follow_ups": [{"fact_id": "later1:methods[5]", "reason": "made up"}]
  }
  client = RoutedLlmClient(
    {
      GROUP_PROMPT_VERSION: [sloppy],
      FOLLOW_UP_PROMPT_VERSION: [invented_follow_up],
    }
  )

  report = detect_corpus(_limitations_root(tmp_path), client)

  groups = {g.label: g for g in report.limitation_groups}
  sample = groups["Small sample size"]
  assert [s.statement_id for s in sample.statements] == ["early1:limitations[0]"]
  assert sample.follow_ups == []
  assert any("ghost:limitations[9]" in note for note in sample.notes)
  assert any("later1:methods[5]" in note for note in sample.notes)
  kept_alone = [g for g in report.limitation_groups if len(g.statements) == 1]
  placed = {s.statement_id for g in kept_alone for s in g.statements}
  assert {"early2:limitations[0]", "early1:future_work[0]"} <= placed
  assert all(
    g.group_id.startswith("limitation-group-") for g in report.limitation_groups
  )


def test_limitations_of_papers_outside_the_manifest_are_never_cited(
  tmp_path: Path,
) -> None:
  """An Extraction for a Paper not in the Corpus contributes no statement."""
  extractions = [
    *_limitation_corpus(),
    Extraction(
      citation_key="stranger",
      fields=ExtractionFields(limitations=[_fact("Stranger limit.", "stranger")]),
    ),
  ]
  client = RoutedLlmClient(
    {GROUP_PROMPT_VERSION: [_GROUPING], FOLLOW_UP_PROMPT_VERSION: [_NONE_ADDRESSED]}
  )
  root = _write_corpus(
    tmp_path,
    {key: [] for key in _YEARS},
    extractions=extractions,
    years=_YEARS,
  )

  report = detect_corpus(root, client)

  cited = {e.citation_key for card in _limitation_gaps(report) for e in card.evidence}
  statements = {s.citation_key for g in report.limitation_groups for s in g.statements}
  assert "stranger" not in cited | statements


def test_bracketed_ids_copied_from_the_prompt_still_resolve(tmp_path: Path) -> None:
  """
  Ids returned in their `[id]` prompt form group and follow up as if bare.

  Read literally they matched nothing, so every statement stood alone (#54).
  """
  bracketed = {
    "groups": [
      {
        "label": "Small sample size",
        "statement_ids": ["[early1:limitations[0]]", " [early2:limitations[0]] "],
      },
      {"label": "Sex differences", "statement_ids": ["[early1:future_work[0]]"]},
    ]
  }
  follow_up = {
    "follow_ups": [{"fact_id": "[later1:methods[0]]", "reason": "Larger cohort."}]
  }
  client = RoutedLlmClient(
    {
      GROUP_PROMPT_VERSION: [bracketed],
      FOLLOW_UP_PROMPT_VERSION: [follow_up, _NONE_ADDRESSED],
    }
  )

  report = detect_corpus(_limitations_root(tmp_path), client)

  groups = {g.label: g for g in report.limitation_groups}
  sample = groups["Small sample size"]
  assert sample.source_citation_keys == ["early1", "early2"]
  assert sample.notes == []
  assert [f.fact_id for f in sample.follow_ups] == ["later1:methods[0]"]


def test_every_cell_candidate_carries_a_verdict_trail(tmp_path: Path) -> None:
  """L1 is the trail's start step and the last step's level is final."""
  report = _detect(_write_corpus(tmp_path, _PLACEMENTS))

  cells = [gap for gap in report.gaps if isinstance(gap, CellGap)]
  assert cells
  for gap in cells:
    assert gap.trail, gap.gap_id
    start = gap.trail[0]
    assert start.effect == "start"
    assert start.layer == "l1"
    assert start.reason == gap.confidence_reason
    assert gap.trail[-1].level_after == gap.confidence


def test_an_artifact_written_before_the_trail_still_reads(tmp_path: Path) -> None:
  """An old artifact gains a one-step trail from its recorded confidence."""
  root = _write_corpus(tmp_path, _PLACEMENTS)
  _detect(root)
  artifact = root / "artifacts" / "candidate_gaps.json"
  data = json.loads(artifact.read_text(encoding="utf-8"))
  for gap in data["gaps"]:
    gap.pop("trail", None)
  artifact.write_text(json.dumps(data), encoding="utf-8")

  gap = _gap(read_candidate_gaps(root), "topicxmethod:takotsubo:rct")

  assert [(s.layer, s.effect, s.level_after) for s in gap.trail] == [
    ("heuristic", "start", gap.confidence)
  ]
  assert gap.trail[0].reason == gap.confidence_reason


def test_an_artifact_written_before_l1_still_reads(tmp_path: Path) -> None:
  """A sparse-count artifact reads with its old rule and a recomputed L1 score."""
  root = _write_corpus(tmp_path, _balanced_placements())
  _detect(root)
  artifact = root / "artifacts" / "candidate_gaps.json"
  data = json.loads(artifact.read_text(encoding="utf-8"))
  for key in ("candidate_rule", "l1_smallest_margins"):
    data.pop(key)
  data["sparse_max_count"] = 0
  for gap in data["gaps"]:
    gap.pop("l1_probability", None)
  artifact.write_text(json.dumps(data), encoding="utf-8")

  report = read_candidate_gaps(root)

  assert "at most 0 Papers" in report.candidate_rule
  assert report.l1_smallest_margins is None
  gap = _gap(report, "topicxmethod:takotsubo:rct")
  assert gap.l1_probability == pytest.approx(1 / 252)


# The taxonomy roll-up layer (L2, ADR 0008). Cardiomyopathies is the parent of
# Takotsubo and of dilated cardiomyopathy. c1 is placed only on the parent, with
# an RCT; d1 is placed on the parent and on its child "dilated", with a cohort
# design. Heart failure has no place in the hierarchy.
_ROLL_UP: Placements = {
  "t1": [("topic", "takotsubo"), ("method", "retrospective")],
  "t2": [("topic", "takotsubo"), ("method", "retrospective")],
  "t3": [("topic", "takotsubo"), ("method", "retrospective")],
  "t4": [("topic", "takotsubo"), ("method", "retrospective")],
  "c1": [("topic", "cardiomyopathies"), ("method", "rct")],
  "d1": [("topic", "dilated"), ("topic", "cardiomyopathies"), ("method", "cohort")],
  "h1": [("topic", "heart-failure"), ("method", "rct")],
  "h2": [("topic", "heart-failure"), ("method", "rct")],
}
_ROLL_UP_PARENTS: dict[Axis, dict[str, str]] = {
  "topic": {"takotsubo": "cardiomyopathies", "dilated": "cardiomyopathies"},
}


def _roll_up_report(tmp_path: Path, parents: Any = _ROLL_UP_PARENTS) -> Any:
  """Detect over the roll-up Corpus with the given hierarchy."""
  return _detect(_write_corpus(tmp_path, _ROLL_UP, parents=parents))


def test_an_ancestor_by_descendant_cell_is_rejected_but_kept(tmp_path: Path) -> None:
  """A category paired with its own broader category is a taxonomy artifact."""
  gap = _gap(_roll_up_report(tmp_path), "topicxtopic:cardiomyopathies:takotsubo")

  last = gap.trail[-1]
  assert (last.layer, last.effect) == ("taxonomy_roll_up", "reject")
  assert "Cardiomyopathies" in last.reason
  assert "Takotsubo Cardiomyopathy" in last.reason
  assert "taxonomy artifact" in last.reason
  assert gap.rejected
  assert last.level_after == gap.trail[0].level_after


def test_a_parent_only_fill_lowers_one_level_and_names_the_paper(
  tmp_path: Path,
) -> None:
  """A Paper placed only on the broader category may cover the narrower cell."""
  gap = _gap(_roll_up_report(tmp_path), "topicxmethod:takotsubo:rct")

  assert [(s.layer, s.effect, s.level_after) for s in gap.trail] == [
    ("l1", "start", "medium"),
    ("taxonomy_roll_up", "lower", "low"),
  ]
  assert gap.confidence == "low"
  assert "c1" in gap.trail[-1].reason
  assert "Cardiomyopathies" in gap.trail[-1].reason
  assert not gap.rejected


def test_a_sibling_fill_changes_no_level(tmp_path: Path) -> None:
  """A parent cell filled only by Papers on a narrower sibling is no cover."""
  gap = _gap(_roll_up_report(tmp_path), "topicxmethod:takotsubo:cohort")

  assert {step.level_after for step in gap.trail} == {gap.trail[0].level_after}
  assert gap.trail[-1].effect == "none"
  assert "d1" in gap.trail[-1].reason
  assert not gap.rejected


def test_a_cell_outside_the_hierarchy_gets_no_roll_up_step(tmp_path: Path) -> None:
  """Categories with no parent and no children are left to the other layers."""
  gap = _gap(_roll_up_report(tmp_path), "topicxmethod:heart-failure:retrospective")

  assert [step.layer for step in gap.trail] == ["l1"]


def test_without_a_hierarchy_the_roll_up_is_a_no_op(tmp_path: Path) -> None:
  """A taxonomy without parents or tree numbers leaves every trail at its start."""
  report = _roll_up_report(tmp_path, parents=None)

  cells = [gap for gap in report.gaps if isinstance(gap, CellGap)]
  assert all(len(_before_review(gap.trail)) == 1 for gap in cells)
  assert not any(gap.rejected for gap in cells)
  assert _gap(report, "topicxmethod:takotsubo:rct").confidence == "medium"


def test_a_re_run_keeps_the_previous_artifact_for_the_change_summary(
  tmp_path: Path,
) -> None:
  """Re-running detect keeps the artifact it replaces, written time and all."""
  root = _write_corpus(tmp_path, _PLACEMENTS)
  artifacts = root / "artifacts"
  _detect(root)
  first = (artifacts / CANDIDATE_GAPS_NAME).read_bytes()
  first_mtime = (artifacts / CANDIDATE_GAPS_NAME).stat().st_mtime_ns

  _detect(root)

  previous = artifacts / PREVIOUS_CANDIDATE_GAPS_NAME
  assert previous.read_bytes() == first
  assert previous.stat().st_mtime_ns == first_mtime


def test_a_first_run_leaves_no_previous_artifact(tmp_path: Path) -> None:
  """With nothing to replace, there is no previous run to compare against."""
  root = _write_corpus(tmp_path, _PLACEMENTS)

  _detect(root)

  assert not (root / "artifacts" / PREVIOUS_CANDIDATE_GAPS_NAME).exists()


def test_the_artifact_lists_the_corpus_papers_it_ran_over(tmp_path: Path) -> None:
  """The Corpus's Paper keys are recorded, so a later run can name new Papers."""
  root = _write_corpus(tmp_path, _PLACEMENTS)

  report = _detect(root)

  assert report.paper_citation_keys == sorted(_PLACEMENTS)
