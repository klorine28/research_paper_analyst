"""
The extract stage: turn each Paper's parsed sections into structured facts.

For every Paper in the manifest, the LLM pulls the seven Extraction fields
(research question, methods, populations/samples, datasets, key findings, stated
limitations, stated future work) out of the parsed text. Every extracted fact
carries Evidence: a verbatim passage and the section it came from.

The stage then mechanically verifies each quoted passage against the parsed
text. Verification is *per fact*: a fact whose Evidence is not a verbatim match
is dropped and recorded as an `UnverifiedFact`, and the Paper keeps its verified
facts (ADR 0004). No unverified quote ever reaches the artifact as Evidence
(CODING_STANDARDS > Research integrity), but one bad quote no longer discards a
Paper's other good facts. A Paper is only a failure when it was never parsed or
when *none* of its facts verify.

The stage writes one inspectable artifact, `artifacts/extractions.json`, holding
every Paper's Extraction, the dropped `unverified` facts, and the failures, and
records the prompt version and model tier behind the run. LLM calls go through
the cached client (ADR 0001), so reruns are free and repeatable.
"""

import logging
import re
import unicodedata
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

# The Unicode replacement character docling emits where a PDF glyph could not be
# decoded (e.g. `\u2264`/`\u2265` collapsing to `\ufffd`). The source character is
# genuinely lost, so a faithful LLM quote reproduces the intended glyph and can
# never match verbatim. During verification each `\ufffd` in the parsed text is
# therefore treated as a wildcard for exactly one character.
_REPLACEMENT_CHAR = "\ufffd"

# Markdown table cell delimiters docling injects between columns. A paper that
# quotes a table row copies the cell text without the `|` separators, so the
# pipes are dropped (to whitespace) on both sides before matching.
_TABLE_PIPE = re.compile(r"\s*\|\s*")


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


class UnverifiedFact(BaseModel):
  """A fact dropped because its Evidence did not verify against the parsed text."""

  citation_key: str
  field: str
  statement: str
  passage: str
  section: SectionLabel
  reason: str


class PaperExtraction(BaseModel):
  """One Paper's verified Extraction plus the facts dropped for bad Evidence."""

  extraction: Extraction
  unverified: list[UnverifiedFact]


class ExtractReport(BaseModel):
  """What the extract stage produced: the Extractions, the failures, and provenance."""

  corpus_root: Path
  prompt_version: str
  tier: ModelTier
  extractions: list[Extraction]
  failures: list[ExtractionFailure]
  unverified: list[UnverifiedFact] = Field(default_factory=list)


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
  unverified: list[UnverifiedFact] = []

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
      result = extract_paper(
        paper.citation_key, parsed, client, prompt_version=prompt_version, tier=tier
      )
    except EvidenceVerificationError as error:
      logger.error("Rejected extraction for %s: %s", paper.citation_key, error)
      failures.append(
        ExtractionFailure(citation_key=paper.citation_key, reason=str(error))
      )
      continue

    extractions.append(result.extraction)
    unverified.extend(result.unverified)
    if result.unverified:
      logger.warning(
        "Dropped %d unverifiable fact(s) from %s.",
        len(result.unverified),
        paper.citation_key,
      )

  report = ExtractReport(
    corpus_root=root,
    prompt_version=prompt_version,
    tier=tier,
    extractions=extractions,
    failures=failures,
    unverified=unverified,
  )
  _write_report(root, report)
  logger.info(
    "Extracted %d of %d Papers (%d failed, %d facts dropped).",
    len(extractions),
    len(manifest.papers),
    len(failures),
    len(unverified),
  )
  return report


def extract_paper(
  citation_key: str,
  parsed: ParsedPaper,
  client: LlmClient,
  *,
  prompt_version: str = PROMPT_VERSION,
  tier: ModelTier = "default",
) -> PaperExtraction:
  """
  Extract one Paper's facts, keeping only those whose Evidence verifies.

  Each fact's quoted passage is checked against the parsed text; a fact that
  does not verify is dropped and returned as an `UnverifiedFact` rather than
  discarding the whole Paper (ADR 0004). A Paper whose facts *all* fail to
  verify raises `EvidenceVerificationError`, since it has nothing to show.
  """
  fields = complete_model(
    client,
    _build_prompt(parsed),
    ExtractionFields,
    prompt_version=prompt_version,
    tier=tier,
    attempts=EXTRACT_ATTEMPTS,
    accept=_has_any_fact,
  )
  verified, unverified = _verify_fields(citation_key, parsed, fields)
  if not _has_any_fact(verified):
    raise EvidenceVerificationError(
      f"no fact's Evidence verified against the paper text "
      f"({len(unverified)} unverifiable)"
    )
  return PaperExtraction(
    extraction=Extraction(citation_key=citation_key, fields=verified),
    unverified=unverified,
  )


def _has_any_fact(fields: ExtractionFields) -> bool:
  """Report whether an extraction found at least one fact in any field."""
  return any(getattr(fields, name) for name in _FIELD_NAMES)


def read_extractions(root: Path) -> ExtractReport:
  """Read the Extractions artifact a previous extract run wrote."""
  path = inspect_corpus_layout(root).layout.artifacts_dir / EXTRACTIONS_NAME
  return ExtractReport.model_validate_json(path.read_text(encoding="utf-8"))


def _verify_fields(
  citation_key: str, parsed: ParsedPaper, fields: ExtractionFields
) -> tuple[ExtractionFields, list[UnverifiedFact]]:
  """Partition a Paper's facts into those whose Evidence verifies and those dropped."""
  haystack = _normalize(parsed.full_text)
  kept: dict[str, list[ExtractedFact]] = {}
  unverified: list[UnverifiedFact] = []
  for name in _FIELD_NAMES:
    keep: list[ExtractedFact] = []
    for fact in getattr(fields, name):
      reason = _evidence_problem(fact.evidence.passage, haystack)
      if reason is None:
        keep.append(fact)
      else:
        unverified.append(
          UnverifiedFact(
            citation_key=citation_key,
            field=name,
            statement=fact.statement,
            passage=fact.evidence.passage,
            section=fact.evidence.section,
            reason=reason,
          )
        )
    kept[name] = keep
  return ExtractionFields(**kept), unverified


def _verify_evidence(parsed: ParsedPaper, fields: ExtractionFields) -> None:
  """
  Raise on the first unverifiable Evidence passage (strict, all-or-nothing).

  The extract stage drops unverifiable facts per-fact (ADR 0004), but promoting
  a human-reviewed Extraction to a Gold regression fixture demands every quote
  be verbatim, so gold promotion (promote_gold) verifies strictly through here.
  """
  haystack = _normalize(parsed.full_text)
  for name in _FIELD_NAMES:
    for fact in getattr(fields, name):
      problem = _evidence_problem(fact.evidence.passage, haystack)
      if problem is not None:
        raise EvidenceVerificationError(f"Evidence in '{name}': {problem}")


def _evidence_problem(passage: str, haystack: str) -> str | None:
  """Return why a quoted passage fails verification, or None when it verifies."""
  stripped = passage.strip()
  if not stripped:
    return "empty Evidence passage"
  if not _passage_found(_normalize(stripped), haystack):
    return f"passage not found in the paper text: {stripped[:80]!r}"
  return None


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
  """
  Canonicalize text so a faithful quote matches across benign parser artifacts.

  This only undoes transformations the *parser* introduced (compatibility
  glyphs, table pipes, wrapped whitespace); it never edits words, so a genuine
  LLM misquote (a typo or stitched span) still fails verification.
  """
  text = unicodedata.normalize("NFKC", text)
  text = _TABLE_PIPE.sub(" ", text)
  return _WHITESPACE.sub(" ", text).strip()


def _passage_found(passage: str, haystack: str) -> bool:
  """
  Report whether a normalized passage occurs in the normalized paper text.

  A plain substring check is tried first. When the paper text carries the
  replacement character (a glyph the parser could not decode), each such
  position is allowed to stand for any one character of the passage, so a quote
  faithful to the paper's intent still verifies. No other character may differ.
  """
  if passage in haystack:
    return True
  if _REPLACEMENT_CHAR not in haystack:
    return False
  span = len(passage)
  for start in range(len(haystack) - span + 1):
    window = haystack[start : start + span]
    if all(h in (c, _REPLACEMENT_CHAR) for h, c in zip(window, passage)):
      return True
  return False
