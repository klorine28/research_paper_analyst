"""Behavior of the detect stage: artifacts in, Candidate Gap cards out."""

from pathlib import Path
from typing import Any

import pytest

from conftest import StubLlmClient

from research_gap_dashboard.aggregate import (
  AggregateReport,
  CategoryAssignment,
  NormalizedFacts,
)
from research_gap_dashboard import cli
from research_gap_dashboard.cli import EXIT_ERROR, EXIT_OK, main
from research_gap_dashboard.detect import (
  CandidateGapsReport,
  CellGap,
  UnansweredLimitationGap,
  detect_corpus,
  read_candidate_gaps,
  sparse_max_count,
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
    taxonomies=[],
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


def _detect(root: Path, client: Any = None) -> CandidateGapsReport:
  """Run detect with a fake LLM (unused when no Paper states a limitation)."""
  return detect_corpus(root, client or StubLlmClient({}))


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


def test_small_corpus_counts_only_empty_cells_as_gaps(tmp_path: Path) -> None:
  """Under 25 Papers, an empty cell is a gap and a single-Paper cell is not."""
  report = _detect(_write_corpus(tmp_path, _PLACEMENTS))

  assert report.sparse_max_count == 0
  gap = _gap(report, "topicxmethod:takotsubo:rct")
  assert gap.gap_type == "coverage_gap"
  assert gap.cell_count == 0
  assert gap.corpus_paper_count == 6
  assert "topicxmethod:heart-failure:retrospective" not in {
    g.gap_id for g in report.gaps
  }
  assert all(g.cell_count == 0 for g in report.gaps if isinstance(g, CellGap))


def test_topic_pairs_never_studied_together_are_knowledge_gaps(tmp_path: Path) -> None:
  """Two observed Topics no Paper combines form one Knowledge Gap, not two."""
  report = _detect(_write_corpus(tmp_path, _PLACEMENTS))

  knowledge = [g for g in report.gaps if g.gap_type == "knowledge_gap"]
  assert [g.gap_id for g in knowledge] == ["topicxtopic:heart-failure:takotsubo"]


def test_large_corpus_also_counts_single_paper_cells(tmp_path: Path) -> None:
  """At 25 Papers and above, a single-Paper cell is a gap and says so."""
  placements = dict(_PLACEMENTS)
  for index in range(7, 26):
    placements[f"p{index:02d}"] = [("topic", "takotsubo"), ("method", "retrospective")]

  report = _detect(_write_corpus(tmp_path, placements))

  assert report.corpus_paper_count == 25
  assert report.sparse_max_count == 1
  gap = _gap(report, "topicxmethod:heart-failure:retrospective")
  assert gap.cell_count == 1
  assert gap.cell_citation_keys == ["p06"]
  assert "only 1 Paper" in gap.explanation
  assert {link.role for link in gap.evidence} >= {"cell"}


@pytest.mark.parametrize(("size", "expected"), [(10, 0), (24, 0), (25, 1), (75, 1)])
def test_sparse_threshold_follows_corpus_size(size: int, expected: int) -> None:
  """The provisional threshold switches from empty-only to single-Paper at 25."""
  assert sparse_max_count(size) == expected


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
      assert card.cell_count <= report.sparse_max_count


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
  """A keyless fake LLM answering each prompt version from its own script."""

  name = "routed"

  def __init__(self, scripts: dict[str, list[dict[str, Any]]]):
    self.scripts = scripts
    self.calls: dict[str, int] = {version: 0 for version in scripts}

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
    script = self.scripts[prompt_version]
    result = script[min(self.calls[prompt_version], len(script) - 1)]
    self.calls[prompt_version] += 1
    return result


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
