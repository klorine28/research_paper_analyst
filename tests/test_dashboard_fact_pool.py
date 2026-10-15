"""
Behavior of the chat's fact pool: Extractions and Normalized Facts mapped for retrieval.

The pool is every extracted fact of a Corpus, each carrying its verbatim Evidence
and the taxonomy categories its phrases were normalized onto (issue #97).
"""

from pathlib import Path

from research_gap_dashboard.dashboard.artifacts import (
  AssignmentRecord,
  EvidenceRecord,
  ExtractedFactRecord,
  ExtractionFieldsRecord,
  ExtractionRecord,
  ExtractionsArtifact,
  NormalizedFactsArtifact,
  NormalizedFactsRecord,
)
from research_gap_dashboard.dashboard.fact_pool import build_fact_pool, load_fact_pool

GOLD_REVIEW_DIR = Path(__file__).parent / "fixtures" / "gold-review"


def _extractions() -> ExtractionsArtifact:
  """One Paper with two methods facts and a finding."""
  methods = [
    ExtractedFactRecord(
      statement="Chart review",
      evidence=EvidenceRecord(passage="We reviewed charts.", section="methods"),
    ),
    ExtractedFactRecord(
      statement="Cohort follow-up",
      evidence=EvidenceRecord(passage="Patients were followed.", section="methods"),
    ),
  ]
  findings = [ExtractedFactRecord(statement="Mortality was low")]
  return ExtractionsArtifact(
    corpus_root=Path("."),
    extractions=[
      ExtractionRecord(
        citation_key="hanna2019",
        fields=ExtractionFieldsRecord(methods=methods, key_findings=findings),
      )
    ],
  )


def test_every_fact_enters_the_pool_with_its_evidence():
  """Each Extraction fact becomes one pool entry, in Extraction field order."""
  pool = build_fact_pool(_extractions(), None)

  assert [(fact.field, fact.statement) for fact in pool] == [
    ("methods", "Chart review"),
    ("methods", "Cohort follow-up"),
    ("key_findings", "Mortality was low"),
  ]
  assert pool[0].passage == "We reviewed charts."
  assert pool[0].section == "methods"


def test_normalized_categories_attach_to_the_fact_they_came_from():
  """A fact_ref ('methods[1]' or '[methods[1]]') attaches its category label."""
  normalized = NormalizedFactsArtifact(
    corpus_root=Path("."),
    normalized=[
      NormalizedFactsRecord(
        citation_key="hanna2019",
        assignments=[
          AssignmentRecord(
            axis="method",
            category_id="prospective-studies",
            category_label="Prospective Studies",
            original_term="cohort follow-up",
            fact_ref="[methods[1]]",
          )
        ],
      )
    ],
  )

  pool = build_fact_pool(_extractions(), normalized)

  assert pool[0].terms == []
  assert pool[1].terms == ["Prospective Studies", "cohort follow-up"]


def test_a_corpus_without_extractions_has_an_empty_pool(tmp_path: Path):
  """No Extractions artifact on disk is an empty pool, not an error."""
  assert not load_fact_pool(tmp_path)


def test_a_committed_extractions_artifact_loads_into_the_pool():
  """A committed Extractions artifact loads into a non-empty pool."""
  pool = load_fact_pool(GOLD_REVIEW_DIR)

  assert pool
  assert all(fact.citation_key and fact.statement for fact in pool)
