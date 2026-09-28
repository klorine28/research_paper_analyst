"""
The explain stage: per-Paper plain-language explanations of the experiment.

For every Paper in the manifest, the LLM writes two explanations of what the
Paper did and found, in two registers: a domain-language explanation for a
researcher in the field, and a lay-language explanation for a non-specialist.
Both are grounded in the Paper's own parsed text - the prompt sees only that
text and is told to describe only what the Paper says, never to invent or
generalize beyond it (CODING_STANDARDS > Research integrity).

Unlike the extract stage, an explanation is prose the model writes in its own
words, so there is no verbatim Evidence to string-match; grounding is enforced
by prompting on the Paper's text alone. The stage stores one file per Paper
under `paper-data/` (alongside the parsed text), records the prompt version and
model tier behind each one, and surfaces a per-Paper failure without aborting
the run. LLM calls go through the cached client (ADR 0001), so reruns are free.
"""

import logging
from pathlib import Path

from pydantic import BaseModel, Field

from research_gap_dashboard.corpus_layout import inspect_corpus_layout
from research_gap_dashboard.ingest import read_manifest
from research_gap_dashboard.llm import LlmClient, ModelTier, complete_model
from research_gap_dashboard.parsing import ParsedPaper, read_parsed_paper

logger = logging.getLogger(__name__)

EXPLANATION_SUFFIX = ".explanation.json"

# Bump when the explain prompt or output shape changes: it keys the LLM cache
# (ADR 0001), so a bump reruns explanation instead of serving stale answers.
PROMPT_VERSION = "explain-v1"

# Forced structured-output calls occasionally misfire (an empty object, or the
# schema echoed back), yielding a blank explanation for a Paper that plainly has
# text. Retry a few times, bypassing the cache, before accepting emptiness.
EXPLAIN_ATTEMPTS = 3


class ExplanationRegisters(BaseModel):
  """One Paper's experiment explained in two registers, as the LLM returns it."""

  domain_explanation: str = Field(
    default="",
    description=(
      "The experiment explained for a researcher in the paper's own field: what "
      "was studied, how, and what was found, using the field's terminology."
    ),
  )
  lay_explanation: str = Field(
    default="",
    description=(
      "The same experiment explained for a non-specialist: what was studied, how, "
      "and what was found, in plain language with no unexplained jargon."
    ),
  )


class PaperExplanation(BaseModel):
  """One Paper's two-register explanation, keyed to the Paper in the manifest."""

  citation_key: str
  prompt_version: str
  tier: ModelTier
  registers: ExplanationRegisters


class ExplanationFailure(BaseModel):
  """A Paper whose explanation could not be produced, and why."""

  citation_key: str
  reason: str


class ExplainReport(BaseModel):
  """What the explain stage produced: the files it wrote, failures, and provenance."""

  corpus_root: Path
  prompt_version: str
  tier: ModelTier
  explanation_paths: list[Path]
  failures: list[ExplanationFailure]


def explain_corpus(
  root: Path,
  client: LlmClient,
  *,
  prompt_version: str = PROMPT_VERSION,
  tier: ModelTier = "default",
) -> ExplainReport:
  """
  Explain every Paper's experiment and store one file per Paper under paper-data/.

  Each Paper is explained independently: a Paper that was never parsed is
  recorded in the report's failures and the run carries on with the rest of the
  Corpus.
  """
  manifest = read_manifest(root)
  paper_data_dir = inspect_corpus_layout(root).layout.paper_data_dir
  paper_data_dir.mkdir(parents=True, exist_ok=True)

  explanation_paths: list[Path] = []
  failures: list[ExplanationFailure] = []

  for paper in manifest.papers:
    try:
      parsed = read_parsed_paper(root, paper.citation_key)
    except FileNotFoundError:
      logger.warning("No parsed text for %s; run `parse` first.", paper.citation_key)
      failures.append(
        ExplanationFailure(
          citation_key=paper.citation_key,
          reason="no parsed text; run `parse` first",
        )
      )
      continue

    explanation = explain_paper(
      paper.citation_key, parsed, client, prompt_version=prompt_version, tier=tier
    )
    out_path = paper_data_dir / f"{paper.citation_key}{EXPLANATION_SUFFIX}"
    out_path.write_text(explanation.model_dump_json(indent=2), encoding="utf-8")
    explanation_paths.append(out_path)

  report = ExplainReport(
    corpus_root=root,
    prompt_version=prompt_version,
    tier=tier,
    explanation_paths=explanation_paths,
    failures=failures,
  )
  logger.info(
    "Explained %d of %d Papers into %s (%d failed).",
    len(explanation_paths),
    len(manifest.papers),
    paper_data_dir,
    len(failures),
  )
  return report


def explain_paper(
  citation_key: str,
  parsed: ParsedPaper,
  client: LlmClient,
  *,
  prompt_version: str = PROMPT_VERSION,
  tier: ModelTier = "default",
) -> PaperExplanation:
  """Explain one Paper's experiment in both registers, grounded in its text."""
  registers = complete_model(
    client,
    _build_prompt(parsed),
    ExplanationRegisters,
    prompt_version=prompt_version,
    tier=tier,
    attempts=EXPLAIN_ATTEMPTS,
    accept=_has_both_registers,
  )
  return PaperExplanation(
    citation_key=citation_key,
    prompt_version=prompt_version,
    tier=tier,
    registers=registers,
  )


def read_paper_explanation(root: Path, citation_key: str) -> PaperExplanation:
  """Read the two-register explanation a previous explain run wrote for a Paper."""
  path = (
    inspect_corpus_layout(root).layout.paper_data_dir
    / f"{citation_key}{EXPLANATION_SUFFIX}"
  )
  return PaperExplanation.model_validate_json(path.read_text(encoding="utf-8"))


def _has_both_registers(registers: ExplanationRegisters) -> bool:
  """Report whether both register explanations came back non-empty."""
  return bool(registers.domain_explanation.strip()) and bool(
    registers.lay_explanation.strip()
  )


def _build_prompt(parsed: ParsedPaper) -> str:
  """Build the explanation prompt from a Paper's parsed sections."""
  return (
    "You explain one research paper's experiment to two audiences. Describe only "
    "what this paper reports - its question, what was done, and what was found. "
    "Never invent, infer, or generalize beyond the text, and never bring in other "
    "work.\n"
    "Write two explanations of the same experiment:\n"
    "- a DOMAIN explanation for a researcher in the paper's field, using the "
    "field's terminology;\n"
    "- a LAY explanation for a non-specialist, in plain language with no "
    "unexplained jargon.\n\n"
    "PAPER TEXT:\n"
    f"{parsed.full_text}"
  )
