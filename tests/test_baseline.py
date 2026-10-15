"""Behavior of the corpus baseline report (#126): what each stage kept and lost."""

import shutil
from pathlib import Path

import pytest

from research_gap_dashboard.aggregate import (
  NORMALIZED_FACTS_NAME,
  AggregateFailure,
  AggregateReport,
  CategoryAssignment,
  NormalizedFacts,
  NotStudyDesignTerm,
  UnmappedTerm,
)
from research_gap_dashboard.baseline import (
  AxisBaseline,
  DetectBaseline,
  FactBaseline,
  GapTypeCount,
  LostPaper,
  baseline_corpus,
  render_baseline,
)
from research_gap_dashboard.cli import main
from research_gap_dashboard.detect import (
  CANDIDATE_GAPS_NAME,
  CandidateGapsReport,
  CellGap,
  Confidence,
  EvidenceLink,
  StatementEvidence,
  UnansweredLimitationGap,
)
from research_gap_dashboard.extract import (
  EXTRACTIONS_NAME,
  Evidence,
  ExtractedFact,
  Extraction,
  ExtractionFailure,
  ExtractionFields,
  ExtractReport,
  UnverifiedFact,
)
from research_gap_dashboard.ingest import ingest_corpus
from research_gap_dashboard.limitations import (
  FollowUp,
  LimitationGroup,
  LimitationStatement,
)
from research_gap_dashboard.parsing import DoclingParser, ParsedPaper, parse_corpus

_GOOD_MARKDOWN = """\
Heart Failure in the Elderly

## Abstract

We studied outcomes in older patients across several hospitals over a decade,
following survival, readmission, and quality-of-life endpoints in detail.

## Materials and Methods

A retrospective cohort of 200 patients was assembled from the registry, and
baseline characteristics were compared between the treated and control arms.
"""

_EVIDENCE = Evidence(
  passage="A retrospective cohort of 200 patients", section="methods"
)

# The fixture Corpus's ten Papers, split by how far each gets.
_PARSE_LOST = "zhou2020"
_EXTRACT_LOST = "seid2019"
_AGGREGATE_LOST = "puhl2019"
_KEPT = [
  "dubrock2018",
  "gogiraju2019",
  "hanna2019",
  "hill2019",
  "italia2021",
  "lee2018",
  "maejima2020",
]


class _ScriptedParser:  # pylint: disable=too-few-public-methods
  """Parse every PDF into good text except the one meant to come out empty."""

  name = "scripted"

  def parse(self, pdf: Path) -> ParsedPaper:
    """Return near-empty text for the parse-lost Paper, good text otherwise."""
    markdown = "Scan\n" if pdf.name.startswith(_PARSE_LOST) else _GOOD_MARKDOWN
    return DoclingParser(convert=lambda _: markdown).parse(pdf)


def _fact(statement: str) -> ExtractedFact:
  return ExtractedFact(statement=statement, evidence=_EVIDENCE)


def _extraction(key: str, *, with_design: bool) -> Extraction:
  return Extraction(
    citation_key=key,
    fields=ExtractionFields(
      research_question=[_fact("Does age change outcomes?")],
      study_design=[_fact("retrospective cohort study")] if with_design else [],
      methods=[_fact("echocardiography")],
    ),
  )


def _unverified(key: str, field: str) -> UnverifiedFact:
  return UnverifiedFact(
    citation_key=key,
    field=field,
    statement="A misquoted fact",
    passage="not in the paper",
    section="results",
    reason="passage not found in the parsed text",
  )


def _assignment(axis: str, category_id: str) -> CategoryAssignment:
  return CategoryAssignment.model_validate(
    {
      "axis": axis,
      "original_term": category_id,
      "category_id": category_id,
      "category_label": category_id,
      "fact_ref": "methods[0]",
      "evidence": _EVIDENCE.model_dump(),
    }
  )


def _normalized(key: str) -> NormalizedFacts:
  assignments = [_assignment("topic", "takotsubo"), _assignment("topic", "echo")]
  if key == "dubrock2018":
    assignments.append(_assignment("method", "cohort-studies"))
  return NormalizedFacts(
    citation_key=key,
    assignments=assignments,
    unmapped=[
      UnmappedTerm(axis="population", original_term="elderly", reason="no category")
    ],
    not_study_design=[
      NotStudyDesignTerm(original_term="troponin assay", reason="a lab assay")
    ],
  )


def _cell_gap(gap_id: str, gap_type: str, confidence: str) -> CellGap:
  return CellGap.model_validate(
    {
      "gap_id": gap_id,
      "gap_type": gap_type,
      "title": gap_id,
      "explanation": "No Paper studies both.",
      "confidence": confidence,
      "confidence_reason": "both categories are common",
      "row_axis": "topic",
      "row_id": "takotsubo",
      "row_label": "Takotsubo",
      "column_axis": "topic",
      "column_id": "echo",
      "column_label": "Echo",
      "cell_count": 0,
      "cell_citation_keys": [],
      "row_paper_count": 3,
      "column_paper_count": 3,
      "corpus_paper_count": 7,
      "evidence": [
        EvidenceLink(
          citation_key="hill2019",
          role="row",
          axis="topic",
          category_id="takotsubo",
          original_term="takotsubo",
          fact_ref="methods[0]",
          evidence=_EVIDENCE,
        ).model_dump()
      ],
    }
  )


def _statement(key: str) -> LimitationStatement:
  return LimitationStatement(
    statement_id=f"{key}:limitations[0]",
    citation_key=key,
    kind="limitation",
    statement="Small sample.",
    evidence=_EVIDENCE,
  )


def _group(group_id: str, *, addressed: bool) -> LimitationGroup:
  follow_ups = (
    [
      FollowUp(
        citation_key="italia2021",
        fact_id="italia2021:study_design[0]",
        statement="A larger cohort.",
        evidence=_EVIDENCE,
        reason="enrolled more patients",
      )
    ]
    if addressed
    else []
  )
  return LimitationGroup(
    group_id=group_id,
    label="Small samples",
    statements=[_statement("hill2019")],
    earliest_year=2019,
    later_citation_keys=["italia2021"],
    follow_ups=follow_ups,
  )


def _limitation_gap(group_id: str, confidence: Confidence) -> UnansweredLimitationGap:
  return UnansweredLimitationGap(
    gap_id=f"limitation:{group_id}",
    gap_type="unanswered_limitation",
    title="Small samples",
    explanation="No later Paper addressed it.",
    confidence=confidence,
    confidence_reason="one later Paper checked",
    group_id=group_id,
    source_citation_keys=["hill2019"],
    later_paper_count=1,
    corpus_paper_count=8,
    evidence=[
      StatementEvidence(
        citation_key="hill2019",
        statement_id="hill2019:limitations[0]",
        statement="Small sample.",
        evidence=_EVIDENCE,
      )
    ],
  )


@pytest.fixture(name="corpus")
def corpus_fixture(cardiology_corpus: Path, tmp_path: Path) -> Path:
  """Run a Corpus through every stage, losing a Paper at parse, extract, aggregate."""
  root = tmp_path / "corpus"
  shutil.copytree(cardiology_corpus, root)
  ingest_corpus(root)
  parse_corpus(root, parser=_ScriptedParser())
  artifacts = root / "artifacts"

  extracted = [*_KEPT, _AGGREGATE_LOST]
  (artifacts / EXTRACTIONS_NAME).write_text(
    ExtractReport(
      corpus_root=root,
      prompt_version="extract-v2",
      tier="default",
      extractions=[
        _extraction(key, with_design=key != "hanna2019") for key in extracted
      ],
      failures=[
        ExtractionFailure(
          citation_key=_EXTRACT_LOST,
          reason="the model returned no facts for this paper in 3 attempts",
        )
      ],
      unverified=[
        _unverified("hill2019", "methods"),
        _unverified("lee2018", "methods"),
      ],
    ).model_dump_json(),
    encoding="utf-8",
  )
  (artifacts / NORMALIZED_FACTS_NAME).write_text(
    AggregateReport(
      corpus_root=root,
      prompt_version="aggregate-v3",
      tier="default",
      taxonomies=[],
      normalized=[_normalized(key) for key in _KEPT],
      failures=[
        AggregateFailure(
          citation_key=_AGGREGATE_LOST, axis="method", reason="unknown category"
        )
      ],
    ).model_dump_json(),
    encoding="utf-8",
  )
  (artifacts / CANDIDATE_GAPS_NAME).write_text(
    CandidateGapsReport(
      corpus_root=root,
      corpus_paper_count=7,
      extracted_paper_count=8,
      sparse_max_count=0,
      normalized_prompt_version="aggregate-v3",
      limitation_prompt_versions=["group-v1", "follow-up-v1"],
      tier="default",
      excluded_citation_keys=[],
      matrices=[],
      limitation_groups=[_group("g1", addressed=True), _group("g2", addressed=False)],
      gaps=[
        _cell_gap("a", "knowledge_gap", "high"),
        _cell_gap("b", "knowledge_gap", "low"),
        _cell_gap("c", "coverage_gap", "medium"),
        _limitation_gap("g2", "high"),
      ],
    ).model_dump_json(),
    encoding="utf-8",
  )
  return root


def test_baseline_reports_papers_kept_and_each_lost_paper_with_its_stage(
  corpus: Path,
):
  """A Paper lost at parse, extract, or aggregate is listed with why."""
  baseline = baseline_corpus(corpus)

  assert baseline.total_papers == 10
  assert baseline.kept_citation_keys == _KEPT
  assert [(lost.citation_key, lost.stage) for lost in baseline.lost] == [
    (_AGGREGATE_LOST, "aggregate"),
    (_EXTRACT_LOST, "extract"),
    (_PARSE_LOST, "parse"),
  ]
  reasons = {lost.citation_key: lost.reason for lost in baseline.lost}
  assert reasons[_EXTRACT_LOST] == (
    "the model returned no facts for this paper in 3 attempts"
  )
  assert reasons[_AGGREGATE_LOST] == "method axis: unknown category"
  assert "near-empty" in reasons[_PARSE_LOST]


def test_baseline_counts_verified_and_unverified_facts(corpus: Path):
  """Facts kept in Extractions are verified; dropped ones are unverified."""
  baseline = baseline_corpus(corpus)

  # Eight extracted Papers, each with a question and a method; seven with a design.
  assert baseline.facts == FactBaseline(
    extracted_paper_count=8,
    verified=8 * 2 + 7,
    unverified=2,
    unverified_by_field={"methods": 2},
    study_design_paper_count=7,
  )


def test_baseline_counts_mapped_unmapped_and_set_aside_terms_per_axis(corpus: Path):
  """Each axis reports its mappings, the Papers they cover, and the misses."""
  baseline = baseline_corpus(corpus)

  assert baseline.axes == [
    AxisBaseline(
      axis="topic", mapped=14, papers_mapped=7, unmapped=0, not_study_design=0
    ),
    AxisBaseline(
      axis="method", mapped=1, papers_mapped=1, unmapped=0, not_study_design=7
    ),
    AxisBaseline(
      axis="population", mapped=0, papers_mapped=0, unmapped=7, not_study_design=0
    ),
    AxisBaseline(
      axis="dataset", mapped=0, papers_mapped=0, unmapped=0, not_study_design=0
    ),
  ]


def test_baseline_counts_candidates_by_gap_type_and_confidence(corpus: Path):
  """Every Gap Type gets a row, and limitation groups report how many were addressed."""
  baseline = baseline_corpus(corpus)

  assert baseline.detect == DetectBaseline(
    gaps=[
      GapTypeCount(gap_type="knowledge_gap", low=1, medium=0, high=1),
      GapTypeCount(gap_type="coverage_gap", low=0, medium=1, high=0),
      GapTypeCount(gap_type="unanswered_limitation", low=0, medium=0, high=1),
    ],
    limitation_group_count=2,
    addressed_group_count=1,
  )


def test_render_baseline_is_a_markdown_report_with_every_section(corpus: Path):
  """The rendered baseline names the denominator and every lost Paper."""
  text = render_baseline(baseline_corpus(corpus))

  assert "Papers kept: 7 of 10" in text
  assert f"| {_EXTRACT_LOST} | extract |" in text
  assert "| method | 1 | 1 | 0 | 7 |" in text
  assert "| knowledge_gap | 1 | 0 | 1 | 2 |" in text
  assert "Limitation groups: 2 (1 addressed by a later Paper)" in text


def test_baseline_before_detect_reports_stages_that_have_run(
  corpus: Path,
):
  """Missing later artifacts leave their sections empty instead of failing."""
  (corpus / "artifacts" / CANDIDATE_GAPS_NAME).unlink()
  (corpus / "artifacts" / NORMALIZED_FACTS_NAME).unlink()

  baseline = baseline_corpus(corpus)

  assert baseline.detect is None
  assert baseline.axes is None
  assert "detect has not run" in render_baseline(baseline)


def test_cli_baseline_logs_the_report(corpus: Path, caplog: pytest.LogCaptureFixture):
  """`baseline <corpus>` logs the Markdown report and exits cleanly."""
  with caplog.at_level("INFO"):
    assert main(["baseline", str(corpus)]) == 0

  assert "Papers kept: 7 of 10" in caplog.text


def test_lost_paper_stage_is_constrained():
  """A lost Paper's stage is one of the pipeline's stages."""
  with pytest.raises(ValueError):
    LostPaper.model_validate({"citation_key": "x", "stage": "nowhere", "reason": "?"})
