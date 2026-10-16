"""
Reading the on-disk artifacts the dashboard depends on.

The dashboard owns its own read models of the artifact schema so it never
imports a pipeline stage (ADR 0002). The models keep only the fields the
dashboard reads and ignore the rest, so a pipeline change that adds fields
does not break the front end.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, ConfigDict

ARTIFACTS_DIR = "artifacts"
PAPER_DATA_DIR = "paper-data"
PAPERS_DIR = "papers"
EXPLANATION_SUFFIX = ".explanation.json"
MANIFEST_NAME = "corpus-manifest.json"
CANDIDATE_GAPS_NAME = "candidate_gaps.json"
# Mirrors detect.PREVIOUS_CANDIDATE_GAPS_NAME: the artifact the last run replaced.
PREVIOUS_CANDIDATE_GAPS_NAME = "candidate_gaps.previous.json"
NORMALIZED_FACTS_NAME = "normalized_facts.json"
RETRIEVAL_GAPS_NAME = "retrieval_gaps.json"
SATURATION_CHECKS_NAME = "saturation_checks.json"
EXTRACTIONS_NAME = "extractions.json"
PARSE_REPORT_NAME = "parse-report.json"
# The Corpus's scope statement: an input at its root, written by `corpus_scope`.
SCOPE_NAME = "scope.json"
# How and when each Paper entered the Corpus: an input next to the scope,
# written by `entry_log` (ADR 0010).
ENTRY_LOG_NAME = "entry_log.json"


class ArtifactNotFoundError(FileNotFoundError):
  """Raised when a corpus directory has no CorpusManifest artifact to read."""


class _ReadModel(BaseModel):
  """A read model that tolerates artifact fields the dashboard does not use."""

  model_config = ConfigDict(extra="ignore")


class PaperRecord(_ReadModel):
  """One Paper, as the dashboard reads it from the CorpusManifest artifact."""

  citation_key: str
  doi: str = ""
  title: str = ""
  year: int | None = None
  journal: str = ""
  pdf_path: Path | None = None
  authors: list[str] = []
  openalex_id: str = ""
  referenced_works: list[str] = []


class UnmatchedEntryRecord(_ReadModel):
  """A paper-list entry that never became a Paper, and why it was left out."""

  citation_key: str = ""
  title: str = ""
  reason: str = ""


class ManifestArtifact(_ReadModel):
  """The CorpusManifest artifact, projected onto what the dashboard shows."""

  corpus_root: Path
  papers: list[PaperRecord] = []
  unmatched_entries: list[UnmatchedEntryRecord] = []
  orphan_pdfs: list[Path] = []


class EvidenceRecord(_ReadModel):
  """A verbatim Evidence passage, as the dashboard reads it from a gap card."""

  passage: str = ""
  section: str = ""


class GapEvidenceRecord(_ReadModel):
  """
  One Evidence link on a Candidate Gap card, over both gap-card shapes.

  Cell gaps carry a role and the Paper's original term; Unanswered Limitation
  gaps carry the source statement. Both carry a citation key and a verbatim
  passage, and the read model keeps every field either shape may set.
  """

  citation_key: str
  evidence: EvidenceRecord
  role: str = ""
  axis: str = ""
  category_id: str = ""
  original_term: str = ""
  statement: str = ""


class LayerStepRecord(_ReadModel):
  """One detection layer's verdict on a cell gap, as the dashboard reads it."""

  layer: str
  effect: str
  level_after: str
  reason: str = ""


class CandidateGapRecord(_ReadModel):
  """One Candidate Gap card, projected onto what the Gap Cards view shows."""

  gap_id: str
  gap_type: str
  title: str
  explanation: str
  confidence: str
  confidence_reason: str
  corpus_paper_count: int
  # Cell gaps: each layer's verdict in order; empty in pre-trail artifacts.
  trail: list[LayerStepRecord] = []
  # Cell gaps: the L1 ranking score; None in pre-L1 artifacts.
  l1_probability: float | None = None
  cell_count: int | None = None
  source_citation_keys: list[str] = []
  evidence: list[GapEvidenceRecord] = []
  # Cell gaps (Knowledge and Coverage) only: the cell and its two categories.
  row_axis: str = ""
  row_id: str = ""
  row_label: str = ""
  column_axis: str = ""
  column_id: str = ""
  column_label: str = ""
  row_paper_count: int = 0
  column_paper_count: int = 0
  # Cell gaps: the limitation groups that corroborate the cell (two witnesses);
  # empty when none does, and in artifacts written before the layer.
  corroborating_group_ids: list[str] = []
  # Unanswered Limitations only: how many later Papers were checked.
  later_paper_count: int = 0
  # Unanswered Limitations only: the group's limitation type, if tagged.
  bucket: str | None = None


class MatrixCategoryRecord(_ReadModel):
  """One row or column of a Coverage Matrix: its category and Paper count."""

  category_id: str
  label: str = ""
  paper_count: int = 0


class MatrixCellRecord(_ReadModel):
  """One Coverage Matrix cell: how many Papers cover both its categories."""

  row_id: str
  column_id: str
  paper_count: int = 0
  citation_keys: list[str] = []


class CoverageMatrixRecord(_ReadModel):
  """One Coverage Matrix, as the dashboard reads it from the CandidateGaps artifact."""

  row_axis: str
  column_axis: str
  corpus_paper_count: int = 0
  rows: list[MatrixCategoryRecord] = []
  columns: list[MatrixCategoryRecord] = []
  cells: list[MatrixCellRecord] = []


class LimitationStatementRecord(_ReadModel):
  """One source statement in a limitation group, as the dashboard reads it."""

  statement_id: str = ""
  citation_key: str
  kind: str = ""
  statement: str = ""
  evidence: EvidenceRecord = EvidenceRecord()


class FollowUpRecord(_ReadModel):
  """One later Paper's fact that addressed a limitation group, as read."""

  citation_key: str
  statement: str = ""
  evidence: EvidenceRecord = EvidenceRecord()
  reason: str = ""


class LimitationGroupRecord(_ReadModel):
  """One limitation group, projected onto what the Limitations view shows."""

  group_id: str
  label: str = ""
  statements: list[LimitationStatementRecord] = []
  earliest_year: int | None = None
  later_citation_keys: list[str] = []
  follow_ups: list[FollowUpRecord] = []
  notes: list[str] = []


class L1MarginsRecord(_ReadModel):
  """The smallest equal category sizes at which an empty cell ranks each level."""

  high: int | None = None
  medium: int | None = None


class CandidateGapsArtifact(_ReadModel):
  """The CandidateGaps artifact, projected onto what the Gap Cards view shows."""

  corpus_root: Path
  corpus_paper_count: int = 0
  # Every Paper in the manifest; empty in artifacts written before it was kept.
  paper_citation_keys: list[str] = []
  extracted_paper_count: int = 0
  # Which cells became candidates, and what L1 can rank High at this size;
  # empty / None in artifacts written before L1.
  candidate_rule: str = ""
  l1_smallest_margins: L1MarginsRecord | None = None
  matrices: list[CoverageMatrixRecord] = []
  limitation_groups: list[LimitationGroupRecord] = []
  gaps: list[CandidateGapRecord] = []


class AssignmentRecord(_ReadModel):
  """One Paper phrase placed onto a category of one axis, as the dashboard reads it."""

  axis: str
  category_id: str
  category_label: str = ""
  original_term: str = ""
  fact_ref: str = ""


class NormalizedFactsRecord(_ReadModel):
  """One Paper's category placements, projected onto what the Trends view needs."""

  citation_key: str
  assignments: list[AssignmentRecord] = []


class NormalizedFactsArtifact(_ReadModel):
  """The NormalizedFacts artifact, projected onto what the Trends view shows."""

  corpus_root: Path
  # Provenance (UI audit I7); defaulted so artifacts that predate a field load.
  prompt_version: str = ""
  tier: str = ""
  model: str = ""
  normalized: list[NormalizedFactsRecord] = []


class CorpusChoice(BaseModel):
  """A corpus the dashboard can open: its directory and a label to show."""

  name: str
  root: Path


def _manifest_path(corpus_root: Path) -> Path:
  """Return where a corpus directory keeps its CorpusManifest artifact."""
  return corpus_root / ARTIFACTS_DIR / MANIFEST_NAME


def has_manifest(corpus_root: Path) -> bool:
  """Report whether a corpus directory has a CorpusManifest artifact to read."""
  return _manifest_path(corpus_root).is_file()


def load_manifest(corpus_root: Path) -> ManifestArtifact:
  """Read the CorpusManifest artifact for a corpus directory."""
  path = _manifest_path(corpus_root)
  if not path.is_file():
    raise ArtifactNotFoundError(
      f"No CorpusManifest at '{path}'. Run `ingest` on '{corpus_root}' first."
    )
  raw = json.loads(path.read_text(encoding="utf-8"))
  return ManifestArtifact.model_validate(raw)


def _candidate_gaps_path(corpus_root: Path) -> Path:
  """Return where a corpus directory keeps its CandidateGaps artifact."""
  return corpus_root / ARTIFACTS_DIR / CANDIDATE_GAPS_NAME


def has_candidate_gaps(corpus_root: Path) -> bool:
  """Report whether a corpus directory has a CandidateGaps artifact to read."""
  return _candidate_gaps_path(corpus_root).is_file()


def load_candidate_gaps(corpus_root: Path) -> CandidateGapsArtifact:
  """Read the CandidateGaps artifact the detect stage left on disk."""
  path = _candidate_gaps_path(corpus_root)
  if not path.is_file():
    raise ArtifactNotFoundError(
      f"No CandidateGaps at '{path}'. Run `detect` on '{corpus_root}' first."
    )
  raw = json.loads(path.read_text(encoding="utf-8"))
  return CandidateGapsArtifact.model_validate(raw)


def load_previous_candidate_gaps(corpus_root: Path) -> CandidateGapsArtifact | None:
  """Read the CandidateGaps artifact the last detect run replaced, if any."""
  path = corpus_root / ARTIFACTS_DIR / PREVIOUS_CANDIDATE_GAPS_NAME
  if not path.is_file():
    return None
  return CandidateGapsArtifact.model_validate_json(path.read_text(encoding="utf-8"))


def candidate_gaps_versions(
  corpus_root: Path,
) -> list[tuple[datetime, CandidateGapsArtifact]]:
  """
  List the CandidateGaps artifacts on disk, each with when it was written.

  The current artifact and the one the last run replaced; detect keeps the
  replaced file's modification time, so both dates are when detect wrote them.
  """
  versions: list[tuple[datetime, CandidateGapsArtifact]] = []
  for name in (CANDIDATE_GAPS_NAME, PREVIOUS_CANDIDATE_GAPS_NAME):
    path = corpus_root / ARTIFACTS_DIR / name
    if path.is_file():
      written_at = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
      artifact = CandidateGapsArtifact.model_validate_json(
        path.read_text(encoding="utf-8")
      )
      versions.append((written_at, artifact))
  return versions


class RetrievalCandidateRecord(_ReadModel):
  """One out-of-corpus candidate, as the dashboard reads it from the artifact."""

  openalex_id: str
  doi: str = ""
  title: str = ""
  year: int | None = None
  venue: str = ""
  authors: list[str] = []
  cited_by_count: int = 0
  citation_overlap: int = 0
  citing_citation_keys: list[str] = []


class RetrievalGapsArtifact(_ReadModel):
  """The RetrievalGaps artifact, projected onto what the Retrieval view shows."""

  corpus_root: Path
  source: str = ""
  label: str = ""
  corpus_paper_count: int = 0
  coupled_paper_count: int = 0
  no_doi_paper_count: int = 0
  min_overlap: int = 0
  limit: int = 0
  candidates: list[RetrievalCandidateRecord] = []


def _retrieval_gaps_path(corpus_root: Path) -> Path:
  """Return where a corpus directory keeps its RetrievalGaps artifact."""
  return corpus_root / ARTIFACTS_DIR / RETRIEVAL_GAPS_NAME


def has_retrieval_gaps(corpus_root: Path) -> bool:
  """Report whether a corpus directory has a RetrievalGaps artifact to read."""
  return _retrieval_gaps_path(corpus_root).is_file()


def load_retrieval_gaps(corpus_root: Path) -> RetrievalGapsArtifact:
  """Read the RetrievalGaps artifact the retrieve stage left on disk."""
  path = _retrieval_gaps_path(corpus_root)
  if not path.is_file():
    raise ArtifactNotFoundError(
      f"No RetrievalGaps at '{path}'. Run `retrieve` on '{corpus_root}' first."
    )
  raw = json.loads(path.read_text(encoding="utf-8"))
  return RetrievalGapsArtifact.model_validate(raw)


class SaturationConceptRecord(_ReadModel):
  """One concept of a checked cell gap and the terms it was searched by."""

  label: str = ""
  terms: list[str] = []


class SaturationSearchRecord(_ReadModel):
  """One search a saturation check ran: source, verbatim query, any error."""

  source: str
  query: str = ""
  attempt: str = "base"
  returned_work_ids: list[str] = []
  in_corpus_work_ids: list[str] = []
  error: str = ""


class SaturationHitRecord(_ReadModel):
  """One out-of-Corpus work a saturation check returned, with its verdict."""

  work_id: str
  sources: list[str] = []
  title: str = ""
  year: int | None = None
  venue: str = ""
  doi: str = ""
  pmid: str = ""
  openalex_id: str = ""
  verdict: str = ""
  reason: str = ""
  attempt: str = "base"
  llm_verdict: str | None = None
  llm_reason: str = ""

  @property
  def counts(self) -> bool:
    """Whether it passed every screening step that ran on it (LLM "no" fails)."""
    return self.verdict == "mentions_both" and self.llm_verdict != "no"


class SaturationSecondAttemptRecord(_ReadModel):
  """The LLM-rephrased second attempt of a check, with its provenance."""

  label: str = ""
  model: str = ""
  prompt_version: str = ""
  concepts: list[SaturationConceptRecord] = []
  note: str = ""


class LlmScreeningRecord(_ReadModel):
  """How a check's LLM screening ran: status, reason, and its provenance."""

  status: str = ""
  reason: str = ""
  prompt_version: str = ""
  model: str = ""
  screened_count: int = 0
  errors: list[str] = []


class SaturationCheckRecord(_ReadModel):
  """One dated saturation-check snapshot, as the Gap Cards view reads it."""

  check_id: str
  gap_id: str
  checked_at: datetime
  status: str
  status_reason: str = ""
  screening_method: str = ""
  concepts: list[SaturationConceptRecord] = []
  searches: list[SaturationSearchRecord] = []
  hits: list[SaturationHitRecord] = []
  llm_screening: LlmScreeningRecord | None = None
  second_attempt: SaturationSecondAttemptRecord | None = None


class SaturationChecksArtifact(_ReadModel):
  """The append-only saturation-check log of one Corpus."""

  label: str = ""
  checks: list[SaturationCheckRecord] = []


def load_saturation_checks(corpus_root: Path) -> SaturationChecksArtifact:
  """Read the saturation-check log, or an empty one when no check has run."""
  path = corpus_root / ARTIFACTS_DIR / SATURATION_CHECKS_NAME
  if not path.is_file():
    return SaturationChecksArtifact()
  raw = json.loads(path.read_text(encoding="utf-8"))
  return SaturationChecksArtifact.model_validate(raw)


def _normalized_facts_path(corpus_root: Path) -> Path:
  """Return where a corpus directory keeps its NormalizedFacts artifact."""
  return corpus_root / ARTIFACTS_DIR / NORMALIZED_FACTS_NAME


def has_normalized_facts(corpus_root: Path) -> bool:
  """Report whether a corpus directory has a NormalizedFacts artifact to read."""
  return _normalized_facts_path(corpus_root).is_file()


def load_normalized_facts(corpus_root: Path) -> NormalizedFactsArtifact:
  """Read the NormalizedFacts artifact the aggregate stage left on disk."""
  path = _normalized_facts_path(corpus_root)
  if not path.is_file():
    raise ArtifactNotFoundError(
      f"No NormalizedFacts at '{path}'. Run `aggregate` on '{corpus_root}' first."
    )
  raw = json.loads(path.read_text(encoding="utf-8"))
  return NormalizedFactsArtifact.model_validate(raw)


class ExtractedFactRecord(_ReadModel):
  """One extracted fact and its Evidence, as the dashboard reads it."""

  statement: str = ""
  evidence: EvidenceRecord = EvidenceRecord()


class ExtractionFieldsRecord(_ReadModel):
  """One Paper's eight Extraction fields, projected onto what a view reads."""

  research_question: list[ExtractedFactRecord] = []
  study_design: list[ExtractedFactRecord] = []
  methods: list[ExtractedFactRecord] = []
  populations: list[ExtractedFactRecord] = []
  datasets: list[ExtractedFactRecord] = []
  key_findings: list[ExtractedFactRecord] = []
  limitations: list[ExtractedFactRecord] = []
  future_work: list[ExtractedFactRecord] = []


class ExtractionRecord(_ReadModel):
  """One Paper's structured facts, keyed to the Paper in the manifest."""

  citation_key: str
  fields: ExtractionFieldsRecord = ExtractionFieldsRecord()


class ExtractionFailureRecord(_ReadModel):
  """A Paper whose extraction was rejected outright, and why."""

  citation_key: str = ""
  reason: str = ""


class UnverifiedFactRecord(_ReadModel):
  """A fact dropped because its Evidence did not verify (ADR 0004)."""

  citation_key: str = ""
  field: str = ""
  statement: str = ""
  passage: str = ""
  reason: str = ""


class ExtractionsArtifact(_ReadModel):
  """The Extractions artifact, projected onto what the dashboard shows."""

  corpus_root: Path
  # Provenance (UI audit I7); defaulted so artifacts that predate a field load.
  prompt_version: str = ""
  tier: str = ""
  model: str = ""
  extractions: list[ExtractionRecord] = []
  failures: list[ExtractionFailureRecord] = []
  unverified: list[UnverifiedFactRecord] = []


def _extractions_path(corpus_root: Path) -> Path:
  """Return where a corpus directory keeps its Extractions artifact."""
  return corpus_root / ARTIFACTS_DIR / EXTRACTIONS_NAME


def has_extractions(corpus_root: Path) -> bool:
  """Report whether a corpus directory has an Extractions artifact to read."""
  return _extractions_path(corpus_root).is_file()


def load_extractions(corpus_root: Path) -> ExtractionsArtifact:
  """Read the Extractions artifact the extract stage left on disk."""
  path = _extractions_path(corpus_root)
  if not path.is_file():
    raise ArtifactNotFoundError(
      f"No Extractions at '{path}'. Run `extract` on '{corpus_root}' first."
    )
  raw = json.loads(path.read_text(encoding="utf-8"))
  return ExtractionsArtifact.model_validate(raw)


class ParseFailureRecord(_ReadModel):
  """A Paper whose PDF could not be parsed by any parser in the chain, and why."""

  citation_key: str = ""
  pdf_path: Path | None = None
  error: str = ""


class ParseReportArtifact(_ReadModel):
  """The parse stage's summary, projected onto the failures the dashboard surfaces."""

  corpus_root: Path
  parsed_paths: list[Path] = []
  failures: list[ParseFailureRecord] = []


def _parse_report_path(corpus_root: Path) -> Path:
  """Return where a corpus directory keeps its parse-stage summary."""
  return corpus_root / ARTIFACTS_DIR / PARSE_REPORT_NAME


def has_parse_report(corpus_root: Path) -> bool:
  """Report whether the parse stage left a summary to read."""
  return _parse_report_path(corpus_root).is_file()


def load_parse_report(corpus_root: Path) -> ParseReportArtifact:
  """Read the parse stage's summary, or raise when the parse stage never ran."""
  path = _parse_report_path(corpus_root)
  if not path.is_file():
    raise ArtifactNotFoundError(
      f"No parse report at '{path}'. Run `parse` on '{corpus_root}' first."
    )
  return ParseReportArtifact.model_validate(
    json.loads(path.read_text(encoding="utf-8"))
  )


class PaperExplanationRegistersRecord(_ReadModel):
  """One Paper's two register explanations, as the dashboard reads them."""

  domain_explanation: str = ""
  lay_explanation: str = ""


class PaperExplanationArtifact(_ReadModel):
  """One Paper's explanation file, projected onto what the Explainer view shows."""

  citation_key: str
  # Provenance (UI audit I7); defaulted so artifacts that predate a field load.
  prompt_version: str = ""
  tier: str = ""
  model: str = ""
  cached: bool | None = None
  registers: PaperExplanationRegistersRecord = PaperExplanationRegistersRecord()


def _paper_explanation_path(corpus_root: Path, citation_key: str) -> Path:
  """Return where a corpus directory keeps a Paper's explanation file."""
  return corpus_root / PAPER_DATA_DIR / f"{citation_key}{EXPLANATION_SUFFIX}"


def has_paper_explanation(corpus_root: Path, citation_key: str) -> bool:
  """Report whether a Paper has an explanation file the explain stage wrote."""
  return _paper_explanation_path(corpus_root, citation_key).is_file()


def load_paper_explanation(
  corpus_root: Path, citation_key: str
) -> PaperExplanationArtifact:
  """Read one Paper's two-register explanation the explain stage left on disk."""
  path = _paper_explanation_path(corpus_root, citation_key)
  if not path.is_file():
    raise ArtifactNotFoundError(
      f"No explanation at '{path}'. Run `explain` on '{corpus_root}' first."
    )
  raw = json.loads(path.read_text(encoding="utf-8"))
  return PaperExplanationArtifact.model_validate(raw)


def explained_citation_keys(corpus_root: Path) -> set[str]:
  """Return the citation keys of Papers that have an explanation file."""
  paper_data = corpus_root / PAPER_DATA_DIR
  if not paper_data.is_dir():
    return set()
  return {
    path.name[: -len(EXPLANATION_SUFFIX)]
    for path in paper_data.glob(f"*{EXPLANATION_SUFFIX}")
  }


def discover_corpora(corpora_root: Path) -> list[CorpusChoice]:
  """
  List the corpora under a directory that have a manifest to read.

  A corpus is any immediate subdirectory whose artifacts hold a CorpusManifest,
  or that has a scope statement (a Corpus created in the app before its Papers
  were ingested), plus the directory itself when it is a corpus. The list is
  sorted by name so the picker order is stable.
  """
  choices: list[CorpusChoice] = []
  if not corpora_root.is_dir():
    return choices
  if _is_corpus(corpora_root):
    choices.append(CorpusChoice(name=corpora_root.name, root=corpora_root))
  for child in sorted(corpora_root.iterdir()):
    if child.is_dir() and _is_corpus(child):
      choices.append(CorpusChoice(name=child.name, root=child))
  return choices


def discover_uningested_corpora(corpora_root: Path) -> list[CorpusChoice]:
  """
  List the corpus folders under a directory that have Papers but no manifest yet.

  These are the folders a researcher can ingest from the dashboard; any immediate
  subdirectory holding a papers/ folder counts, sorted by name.
  """
  if not corpora_root.is_dir():
    return []
  return [
    CorpusChoice(name=child.name, root=child)
    for child in sorted(corpora_root.iterdir())
    if child.is_dir() and (child / PAPERS_DIR).is_dir() and not has_manifest(child)
  ]


def _is_corpus(directory: Path) -> bool:
  """Whether a directory is a Corpus: it has a manifest or a scope statement."""
  return has_manifest(directory) or (directory / SCOPE_NAME).is_file()
