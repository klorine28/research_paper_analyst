"""Behavior of the detect stage: NormalizedFacts in, Knowledge/Coverage Gaps out."""

from pathlib import Path

import pytest

from research_gap_dashboard.aggregate import (
  AggregateReport,
  CategoryAssignment,
  NormalizedFacts,
)
from research_gap_dashboard.cli import EXIT_ERROR, EXIT_OK, main
from research_gap_dashboard.detect import (
  CandidateGap,
  CandidateGapsReport,
  detect_corpus,
  read_candidate_gaps,
  sparse_max_count,
)
from research_gap_dashboard.extract import Evidence
from research_gap_dashboard.ingest import CorpusManifest, Paper
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
) -> Path:
  """Write a manifest and a NormalizedFacts artifact for a synthetic Corpus."""
  for name in ("papers", "paper-data", "artifacts", "judgments"):
    (root / name).mkdir(parents=True, exist_ok=True)
  (root / "corpus.bib").write_text("", encoding="utf-8")

  keys = manifest_keys if manifest_keys is not None else list(placements)
  manifest = CorpusManifest(
    corpus_root=root,
    papers=[
      Paper(citation_key=key, doi=f"10.1/{key}", pdf_path=root / f"{key}.pdf")
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
  artifacts = root / "artifacts"
  (artifacts / "corpus-manifest.json").write_text(
    manifest.model_dump_json(indent=2), encoding="utf-8"
  )
  (artifacts / "normalized_facts.json").write_text(
    normalized.model_dump_json(indent=2), encoding="utf-8"
  )
  return root


def _gap(report: CandidateGapsReport, gap_id: str) -> CandidateGap:
  """Return the gap card with this id, failing the test if it is absent."""
  by_id = {gap.gap_id: gap for gap in report.gaps}
  assert gap_id in by_id, f"{gap_id} not in {sorted(by_id)}"
  return by_id[gap_id]


def test_matrices_cover_all_three_axis_pairs_and_topic_pairs(tmp_path: Path) -> None:
  """A Topic × Topic and a Topic × Method/Population/Dataset matrix are built."""
  report = detect_corpus(_write_corpus(tmp_path, _PLACEMENTS))

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
  report = detect_corpus(_write_corpus(tmp_path, _PLACEMENTS))

  assert report.sparse_max_count == 0
  gap = _gap(report, "topicxmethod:takotsubo:rct")
  assert gap.gap_type == "coverage_gap"
  assert gap.cell_count == 0
  assert gap.corpus_paper_count == 6
  assert "topicxmethod:heart-failure:retrospective" not in {
    g.gap_id for g in report.gaps
  }
  assert all(g.cell_count == 0 for g in report.gaps)


def test_topic_pairs_never_studied_together_are_knowledge_gaps(tmp_path: Path) -> None:
  """Two observed Topics no Paper combines form one Knowledge Gap, not two."""
  report = detect_corpus(_write_corpus(tmp_path, _PLACEMENTS))

  knowledge = [g for g in report.gaps if g.gap_type == "knowledge_gap"]
  assert [g.gap_id for g in knowledge] == ["topicxtopic:heart-failure:takotsubo"]


def test_large_corpus_also_counts_single_paper_cells(tmp_path: Path) -> None:
  """At 25 Papers and above, a single-Paper cell is a gap and says so."""
  placements = dict(_PLACEMENTS)
  for index in range(7, 26):
    placements[f"p{index:02d}"] = [("topic", "takotsubo"), ("method", "retrospective")]

  report = detect_corpus(_write_corpus(tmp_path, placements))

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
  report = detect_corpus(_write_corpus(tmp_path, _PLACEMENTS))

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
    assert card.cell_count <= report.sparse_max_count


def test_confidence_grows_with_how_expected_the_combination_was(
  tmp_path: Path,
) -> None:
  """An empty cell between two common categories outranks one between rare ones."""
  report = detect_corpus(_write_corpus(tmp_path, _PLACEMENTS))

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
  crowded = detect_corpus(_write_corpus(tmp_path / "crowded", common))
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

  report = detect_corpus(root)

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

  detect_corpus(root)
  first = artifact.read_bytes()
  detect_corpus(root)

  assert artifact.read_bytes() == first
  assert read_candidate_gaps(root).gaps == detect_corpus(root).gaps


def test_cli_detect_writes_the_artifact(tmp_path: Path) -> None:
  """The detect command runs offline over the aggregate artifact."""
  root = _write_corpus(tmp_path, _PLACEMENTS)

  assert main(["detect", str(root)]) == EXIT_OK
  assert (root / "artifacts" / "candidate_gaps.json").is_file()


def test_cli_detect_before_aggregate_fails_clearly(tmp_path: Path) -> None:
  """Without the NormalizedFacts artifact the command exits with an error."""
  root = _write_corpus(tmp_path, _PLACEMENTS)
  (root / "artifacts" / "normalized_facts.json").unlink()

  assert main(["detect", str(root)]) == EXIT_ERROR
