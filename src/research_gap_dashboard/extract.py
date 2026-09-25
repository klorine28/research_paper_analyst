"""
The extract stage: turn each Paper's parsed sections into structured facts.

For every Paper in the manifest, the LLM pulls the seven Extraction fields
(research question, methods, populations/samples, datasets, key findings, stated
limitations, stated future work) out of the parsed text. Every extracted fact
carries Evidence: a verbatim passage and the section it came from.

The stage then mechanically verifies each quoted passage against the parsed
text and rejects the whole Paper's extraction loudly on any mismatch, so no
invented quote ever reaches the artifact (CODING_STANDARDS > Research
integrity). Verification is a per-Paper failure: one bad extraction is recorded
and the run carries on with the rest of the Corpus.

The stage writes one inspectable artifact, `artifacts/extractions.json`, holding
every Paper's Extraction plus the failures, and records the prompt version and
model tier behind the run. LLM calls go through the cached client (ADR 0001), so
reruns are free and repeatable.
"""

import logging
import re
from pathlib import Path

from pydantic import BaseModel, Field

from research_gap_dashboard.corpus_layout import inspect_corpus_layout
from research_gap_dashboard.ingest import read_manifest
from research_gap_dashboard.llm import LlmClient, ModelTier, complete_model
from research_gap_dashboard.parsing import ParsedPaper, SectionLabel, read_parsed_paper

logger = logging.getLogger(__name__)

EXTRACTIONS_NAME = "extractions.json"

# Bump when the extraction prompt or output shape changes: it keys the LLM cache
# (ADR 0001), so a bump reruns extraction instead of serving stale answers.
PROMPT_VERSION = "extract-v1"

# Forced structured-output calls occasionally misfire (an empty object, or the
# schema echoed back), yielding an all-empty Extraction for a Paper that plainly
# has facts. Retry a few times, bypassing the cache, before accepting emptiness.
EXTRACT_ATTEMPTS = 3

# The seven Extraction fields, in a fixed order for verification and display.
_FIELD_NAMES: tuple[str, ...] = (
  "research_question",
  "methods",
  "populations",
  "datasets",
  "key_findings",
  "limitations",
  "future_work",
)

_WHITESPACE = re.compile(r"\s+")


class EvidenceVerificationError(Exception):
  """Raised when a quoted Evidence passage is not found in the parsed text."""


class Evidence(BaseModel):
  """A verbatim passage from a Paper, and the section it was quoted from."""

  passage: str = Field(description="A verbatim quote copied from the paper's text.")
  section: SectionLabel = Field(description="The section the passage was quoted from.")


class ExtractedFact(BaseModel):
  """One extracted fact stated in the model's words, grounded in Evidence."""

  statement: str = Field(description="The fact, stated plainly.")
  evidence: Evidence


class ExtractionFields(BaseModel):
  """The seven Extraction fields the LLM returns for one Paper."""

  research_question: list[ExtractedFact] = Field(
    default_factory=list, description="The research question(s) the paper asks."
  )
  methods: list[ExtractedFact] = Field(
    default_factory=list, description="The study designs and methods used."
  )
  populations: list[ExtractedFact] = Field(
    default_factory=list, description="The populations or samples studied."
  )
  datasets: list[ExtractedFact] = Field(
    default_factory=list, description="The datasets or data sources used."
  )
  key_findings: list[ExtractedFact] = Field(
    default_factory=list, description="The paper's key findings or results."
  )
  limitations: list[ExtractedFact] = Field(
    default_factory=list, description="Limitations the paper states about itself."
  )
  future_work: list[ExtractedFact] = Field(
    default_factory=list, description="Future work or open questions the paper states."
  )


class Extraction(BaseModel):
  """One Paper's structured facts, keyed to the Paper in the manifest."""

  citation_key: str
  fields: ExtractionFields


class ExtractionFailure(BaseModel):
  """A Paper whose extraction was rejected, and why."""

  citation_key: str
  reason: str


class ExtractReport(BaseModel):
  """What the extract stage produced: the Extractions, the failures, and provenance."""

  corpus_root: Path
  prompt_version: str
  tier: ModelTier
  extractions: list[Extraction]
  failures: list[ExtractionFailure]


def extract_corpus(
  root: Path,
  client: LlmClient,
  *,
  prompt_version: str = PROMPT_VERSION,
  tier: ModelTier = "default",
) -> ExtractReport:
  """
  Extract structured facts for every Paper and write the Extractions artifact.

  Each Paper is extracted independently: a Paper that was never parsed, or whose
  Evidence fails verification, is recorded in the report's failures and the run
  carries on with the rest of the Corpus.
  """
  manifest = read_manifest(root)
  extractions: list[Extraction] = []
  failures: list[ExtractionFailure] = []

  for paper in manifest.papers:
    try:
      parsed = read_parsed_paper(root, paper.citation_key)
    except FileNotFoundError:
      logger.warning("No parsed text for %s; run `parse` first.", paper.citation_key)
      failures.append(
        ExtractionFailure(
          citation_key=paper.citation_key,
          reason="no parsed text; run `parse` first",
        )
      )
      continue

    try:
      extraction = extract_paper(
        paper.citation_key, parsed, client, prompt_version=prompt_version, tier=tier
      )
    except EvidenceVerificationError as error:
      logger.error("Rejected extraction for %s: %s", paper.citation_key, error)
      failures.append(
        ExtractionFailure(citation_key=paper.citation_key, reason=str(error))
      )
      continue

    extractions.append(extraction)

  report = ExtractReport(
    corpus_root=root,
    prompt_version=prompt_version,
    tier=tier,
    extractions=extractions,
    failures=failures,
  )
  _write_report(root, report)
  logger.info(
    "Extracted %d of %d Papers (%d failed).",
    len(extractions),
    len(manifest.papers),
    len(failures),
  )
  return report


def extract_paper(
  citation_key: str,
  parsed: ParsedPaper,
  client: LlmClient,
  *,
  prompt_version: str = PROMPT_VERSION,
  tier: ModelTier = "default",
) -> Extraction:
  """Extract one Paper's facts and verify every Evidence passage against its text."""
  fields = complete_model(
    client,
    _build_prompt(parsed),
    ExtractionFields,
    prompt_version=prompt_version,
    tier=tier,
    attempts=EXTRACT_ATTEMPTS,
    accept=_has_any_fact,
  )
  _verify_evidence(parsed, fields)
  return Extraction(citation_key=citation_key, fields=fields)


def _has_any_fact(fields: ExtractionFields) -> bool:
  """Report whether an extraction found at least one fact in any field."""
  return any(getattr(fields, name) for name in _FIELD_NAMES)


def read_extractions(root: Path) -> ExtractReport:
  """Read the Extractions artifact a previous extract run wrote."""
  path = inspect_corpus_layout(root).layout.artifacts_dir / EXTRACTIONS_NAME
  return ExtractReport.model_validate_json(path.read_text(encoding="utf-8"))


def _verify_evidence(parsed: ParsedPaper, fields: ExtractionFields) -> None:
  """Fail loudly if any quoted passage is absent from the parsed text."""
  haystack = _normalize(parsed.full_text)
  for name in _FIELD_NAMES:
    for fact in getattr(fields, name):
      passage = fact.evidence.passage.strip()
      if not passage:
        raise EvidenceVerificationError(f"empty Evidence passage in '{name}'")
      if _normalize(passage) not in haystack:
        raise EvidenceVerificationError(
          f"Evidence passage in '{name}' not found in the paper text: {passage[:80]!r}"
        )


def _build_prompt(parsed: ParsedPaper) -> str:
  """Build the extraction prompt from a Paper's parsed sections."""
  return (
    "You extract structured facts from one research paper. Return only facts "
    "stated in the paper; never invent, infer, or generalize beyond the text.\n"
    "For every fact, copy a short verbatim passage from the paper as Evidence, "
    "exactly as written, and name the section it came from. If a field has no "
    "support in the text, return an empty list for it.\n\n"
    "PAPER TEXT:\n"
    f"{parsed.full_text}"
  )


def _write_report(root: Path, report: ExtractReport) -> None:
  """Write the Extractions artifact under artifacts/."""
  artifacts_dir = inspect_corpus_layout(root).layout.artifacts_dir
  artifacts_dir.mkdir(parents=True, exist_ok=True)
  path = artifacts_dir / EXTRACTIONS_NAME
  path.write_text(report.model_dump_json(indent=2), encoding="utf-8")


def _normalize(text: str) -> str:
  """Collapse whitespace so quotes match across a PDF's line wrapping."""
  return _WHITESPACE.sub(" ", text).strip()
