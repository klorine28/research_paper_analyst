"""
Command line entry point driving the pipeline stages.

The CLI only parses arguments, configures logging, and turns stage errors
into exit codes; all behavior lives in the stage modules.
"""

# pylint: disable=too-many-lines

import argparse
import logging
import sys
from collections import Counter
from collections.abc import Sequence
from pathlib import Path

import httpx

from research_gap_dashboard.aggregate import (
  aggregate_corpus,
  load_pipeline_taxonomies,
  read_normalized_facts,
)
from research_gap_dashboard.dashboard.extraction_review import (
  FIELD_NAMES as _GOLD_FIELD_NAMES,
)
from research_gap_dashboard.apply_corrections import apply_parse_corrections
from research_gap_dashboard.baseline import baseline_corpus, render_baseline
from research_gap_dashboard.detect import detect_corpus
from research_gap_dashboard.diagnose import diagnose_corpus, render_funnel
from research_gap_dashboard.embeddings import (
  EmbeddingConfig,
  EmbeddingConfigError,
  build_embedder,
)
from research_gap_dashboard.env import load_env
from research_gap_dashboard.explain import explain_corpus
from research_gap_dashboard.extract import extract_corpus, read_extractions
from research_gap_dashboard.grow import (
  GrowError,
  WorkRef,
  add_suggested_paper,
  mark_unavailable,
)
from research_gap_dashboard.ingest import (
  CorpusLayoutError,
  CorpusSizeError,
  check_corpus,
  ingest_corpus,
)
from research_gap_dashboard.llm import (
  LlmClient,
  LlmConfig,
  LlmConfigError,
  LlmUsage,
  ModelTier,
  build_llm_client,
  write_usage,
)
from research_gap_dashboard.parsing import parse_corpus
from research_gap_dashboard.promote_gold import (
  GoldPromotionRefusedError,
  default_gold_path,
  promote_gold,
  write_gold,
)
from research_gap_dashboard.retrieval import (
  DEFAULT_LIMIT,
  DEFAULT_MIN_OVERLAP,
  detect_retrieval_gaps,
)
from research_gap_dashboard.saturate import (
  HitScreener,
  QueryVariantModel,
  SaturationError,
  saturate_gap,
)
from research_gap_dashboard.sources import (
  DEFAULT_SEARCH_LIMIT,
  OpenAlexAdapter,
  PubMedAdapter,
  SourceIdentity,
  TopicSearchSource,
  httpx_fetch_bytes,
)
from research_gap_dashboard.taxonomy import (
  AXES,
  CARDIOLOGY_SEED_PATH,
  SEED_PATHS,
  Taxonomy,
  TaxonomyError,
  load_taxonomy,
)
from research_gap_dashboard.term_promotion import (
  UnmappedTermScreen,
  find_candidate,
  promote_unmapped_term,
  screen_unmapped_terms,
)
from research_gap_dashboard.vocabulary import (
  AliasOverlay,
  AliasOverlayError,
  MeshVocabulary,
  approve_alias,
  promote_alias,
  propose_aliases,
  read_alias_overlay,
  reject_alias,
  suggest_mesh_aliases,
  write_alias_overlay,
)

LLM_CACHE_DIR = ".llm-cache"
# Each LLM stage's last run's calls and tokens, read by the dashboard's Run
# details to report what a run actually cost (issue #70).
LLM_USAGE_PATH = Path("artifacts") / "llm-usage.json"
# The commands whose LLM usage is recorded.
_LLM_COMMANDS = frozenset({"extract", "explain", "aggregate", "detect", "saturate"})

# Works fetched per source per saturation-check query.
SATURATE_SEARCH_LIMIT = DEFAULT_SEARCH_LIMIT

logger = logging.getLogger("research_gap_dashboard")

EXIT_OK = 0
EXIT_ERROR = 1
# The run completed but some Papers did not make it through (a bad PDF, a
# rejected extraction): the pipeline never hard-blocks on one Paper, but it
# signals the incompleteness so a caller or CI can notice (issue #46 Step 3).
EXIT_INCOMPLETE = 2


def build_parser() -> argparse.ArgumentParser:
  """Build the argument parser for the pipeline CLI."""
  parser = argparse.ArgumentParser(
    prog="research-gap-dashboard",
    description="Detect research gaps in a corpus of papers.",
  )
  subcommands = parser.add_subparsers(dest="command", required=True)
  ingest = subcommands.add_parser(
    "ingest", help="Pair the paper list with the PDFs and write the CorpusManifest."
  )
  ingest.add_argument("corpus", type=Path, help="Path to the corpus directory.")
  ingest.add_argument(
    "--check",
    action="store_true",
    help=(
      "Only check how the paper list and PDFs pair up and whether the Corpus is "
      "within the 10-75 Paper envelope; print that as JSON and write nothing."
    ),
  )
  ingest.add_argument(
    "--resolve",
    action="store_true",
    help="Resolve each DOI against a scholarly source for metadata (needs network).",
  )
  ingest.add_argument(
    "--source",
    choices=["openalex", "pubmed"],
    default="openalex",
    help="Scholarly source to resolve DOIs against (default: OpenAlex).",
  )
  ingest.add_argument(
    "--mailto",
    default=None,
    help=(
      "Contact email to join OpenAlex's polite pool when resolving "
      "(default: OPENALEX_MAILTO)."
    ),
  )
  parse = subcommands.add_parser(
    "parse",
    help="Parse each Paper's PDF into sectioned text under paper-data/.",
  )
  parse.add_argument("corpus", type=Path, help="Path to the corpus directory.")
  diagnose = subcommands.add_parser(
    "diagnose",
    help="Report the parse/extract funnel and where Papers fall out of it.",
  )
  diagnose.add_argument("corpus", type=Path, help="Path to the corpus directory.")
  diagnose.add_argument(
    "--min-rate",
    type=float,
    default=None,
    help=(
      "Exit non-zero when the end-to-end rate falls below this fraction "
      "(e.g. 0.9 to gate on the >=90%% target); default: report only."
    ),
  )
  baseline = subcommands.add_parser(
    "baseline",
    help=(
      "Report Papers kept and lost, facts, terms per axis, and Candidate Gaps "
      "as Markdown."
    ),
  )
  baseline.add_argument("corpus", type=Path, help="Path to the corpus directory.")
  apply_corrections = subcommands.add_parser(
    "apply-parse-corrections",
    help="Write the dashboard's manual parse corrections into paper-data/.",
  )
  apply_corrections.add_argument(
    "corpus", type=Path, help="Path to the corpus directory."
  )
  extract = subcommands.add_parser(
    "extract",
    help="LLM-extract structured facts with verified Evidence into the artifact.",
  )
  extract.add_argument("corpus", type=Path, help="Path to the corpus directory.")
  extract.add_argument(
    "--tier",
    choices=["default", "cheap"],
    default="default",
    help="Model tier to extract with (default: the capable model).",
  )
  explain = subcommands.add_parser(
    "explain",
    help="LLM-explain each Paper's experiment in domain and lay language.",
  )
  explain.add_argument("corpus", type=Path, help="Path to the corpus directory.")
  explain.add_argument(
    "--tier",
    choices=["default", "cheap"],
    default="default",
    help="Model tier to explain with (default: the capable model).",
  )
  aggregate = subcommands.add_parser(
    "aggregate",
    help="Normalize each Paper's extracted facts onto the axis taxonomies.",
  )
  aggregate.add_argument("corpus", type=Path, help="Path to the corpus directory.")
  aggregate.add_argument(
    "--taxonomy",
    type=Path,
    action="append",
    default=None,
    help=(
      "Taxonomy file to normalize onto; repeat once per axis, each file naming "
      "its axis in [meta] (default: the shipped Topic, Method, Population, and "
      "Dataset seeds)."
    ),
  )
  aggregate.add_argument(
    "--tier",
    choices=["default", "cheap"],
    default="default",
    help="Model tier to normalize with (default: the capable model).",
  )
  detect = subcommands.add_parser(
    "detect",
    help="Find Knowledge, Coverage, and Unanswered Limitation gaps.",
  )
  detect.add_argument("corpus", type=Path, help="Path to the corpus directory.")
  detect.add_argument(
    "--tier",
    choices=["default", "cheap"],
    default="default",
    help="Model tier for limitation grouping (default: the capable model).",
  )
  retrieve = subcommands.add_parser(
    "retrieve",
    help="Find Retrieval Gaps: out-of-corpus papers by citation overlap.",
  )
  retrieve.add_argument("corpus", type=Path, help="Path to the corpus directory.")
  retrieve.add_argument(
    "--limit",
    type=int,
    default=DEFAULT_LIMIT,
    help=f"Cap on candidates written (default: {DEFAULT_LIMIT}).",
  )
  retrieve.add_argument(
    "--min-overlap",
    type=int,
    default=DEFAULT_MIN_OVERLAP,
    help=(
      "Least citation overlap (Corpus Papers citing a work) a candidate needs "
      f"(default: {DEFAULT_MIN_OVERLAP})."
    ),
  )
  retrieve.add_argument(
    "--mailto",
    default=None,
    help=(
      "Contact email to join OpenAlex's polite pool when resolving "
      "(default: OPENALEX_MAILTO)."
    ),
  )
  saturate = subcommands.add_parser(
    "saturate",
    help=(
      "Search OpenAlex and PubMed for work that would close a gap "
      "(saturation check, needs network)."
    ),
  )
  saturate.add_argument("corpus", type=Path, help="Path to the corpus directory.")
  saturate.add_argument(
    "--gap",
    required=True,
    help=(
      "Gap id to check, e.g. topicxtopic:heart-failure:myocardial-ischemia or "
      "limitation:limitation-group-01."
    ),
  )
  saturate.add_argument(
    "--tier",
    choices=["default", "cheap"],
    default="cheap",
    help=(
      "Model tier to screen the top hits with (default: the cheap model). "
      "Without ANTHROPIC_API_KEY, hits are screened by title/abstract only."
    ),
  )
  _add_taxonomy_option(saturate)
  _add_grow_parsers(subcommands)
  promote = subcommands.add_parser(
    "promote-gold",
    help="Turn a reviewed Extraction plus its overlay into a Gold Extraction fixture.",
  )
  promote.add_argument("corpus", type=Path, help="Path to the corpus directory.")
  promote.add_argument(
    "--out",
    type=Path,
    default=None,
    help=(
      "Where to write the Gold Extraction fixture "
      "(default: the corpus's artifacts/gold_extractions.json)."
    ),
  )
  taxonomy = subcommands.add_parser(
    "taxonomy",
    help="Validate a taxonomy file (any axis) and report any problems.",
  )
  taxonomy.add_argument(
    "file",
    type=Path,
    nargs="?",
    default=CARDIOLOGY_SEED_PATH,
    help="Taxonomy file to validate (default: the shipped cardiology seed).",
  )
  _add_aliases_parser(subcommands)
  return parser


def main(argv: Sequence[str] | None = None) -> int:
  """Run one pipeline command; return the process exit code."""
  load_env()
  logging.basicConfig(level=logging.INFO, format="%(message)s")
  arguments = build_parser().parse_args(argv)

  runners = {
    "ingest": _run_ingest,
    "parse": _run_parse,
    "diagnose": _run_diagnose,
    "baseline": _run_baseline,
    "apply-parse-corrections": _run_apply_parse_corrections,
    "extract": _run_extract,
    "explain": _run_explain,
    "aggregate": _run_aggregate,
    "detect": _run_detect,
    "retrieve": _run_retrieve,
    "saturate": _run_saturate,
    "add-suggested": _run_add_suggested,
    "mark-unavailable": _run_mark_unavailable,
    "promote-gold": _run_promote_gold,
    "taxonomy": _run_taxonomy,
    "aliases": _run_aliases,
  }
  if arguments.command not in _LLM_COMMANDS:
    return runners[arguments.command](arguments)
  arguments.llm_usage = LlmUsage()
  try:
    return runners[arguments.command](arguments)
  finally:
    usage: LlmUsage = arguments.llm_usage
    # A run that never reached the LLM (bad config, missing input) leaves the
    # previous run's record in place rather than overwriting it with nothing.
    if usage.calls or usage.cached_calls:
      write_usage(arguments.corpus / LLM_USAGE_PATH, arguments.command, usage)


def _llm_client(arguments: argparse.Namespace) -> LlmClient:
  """Build the stage's cached LLM client, tallying its usage for the run."""
  return build_llm_client(
    cache_dir=arguments.corpus / LLM_CACHE_DIR, usage=arguments.llm_usage
  )


def _run_ingest(arguments: argparse.Namespace) -> int:
  """Pair the paper list with the PDFs and write the CorpusManifest."""
  if arguments.check:
    return _run_ingest_check(arguments)
  adapter = _build_source(arguments) if arguments.resolve else None
  try:
    manifest = ingest_corpus(arguments.corpus, adapter=adapter)
  except (CorpusSizeError, CorpusLayoutError) as error:
    logger.error("%s", error)
    return EXIT_ERROR

  unresolved = sum(1 for paper in manifest.papers if paper.resolution_error)
  no_doi = sum(1 for paper in manifest.papers if not paper.doi)
  logger.info(
    "Corpus: %d Papers (%d without a DOI), %d unmatched entries, %d orphan PDFs, "
    "%d DOIs unresolved.",
    len(manifest.papers),
    no_doi,
    len(manifest.unmatched_entries),
    len(manifest.orphan_pdfs),
    unresolved,
  )
  return EXIT_OK


def _run_ingest_check(arguments: argparse.Namespace) -> int:
  """
  Print the pairing check as JSON on stdout; fail when outside the envelope.

  stdout carries only the JSON (logging goes to stderr), so the dashboard can
  read it back through its subprocess runner without importing ingest.
  """
  try:
    check = check_corpus(arguments.corpus)
  except CorpusLayoutError as error:
    logger.error("%s", error)
    return EXIT_ERROR
  sys.stdout.write(check.model_dump_json(indent=2) + "\n")
  if not check.within_envelope:
    logger.error("%s", check.refusal)
    return EXIT_ERROR
  return EXIT_OK


def _build_source(arguments: argparse.Namespace):
  """Build the source adapter the ingest run resolves DOIs against."""
  if arguments.source == "pubmed":
    return PubMedAdapter(api_key=SourceIdentity.from_env().pubmed_api_key)
  return OpenAlexAdapter(mailto=_openalex_mailto(arguments))


def _openalex_mailto(arguments: argparse.Namespace) -> str | None:
  """Pick the polite-pool mailto: `--mailto` wins over `OPENALEX_MAILTO`."""
  return arguments.mailto or SourceIdentity.from_env().openalex_mailto


def _run_retrieve(arguments: argparse.Namespace) -> int:
  """Find out-of-corpus candidates by citation overlap and write the artifact."""
  adapter = OpenAlexAdapter(mailto=_openalex_mailto(arguments))
  try:
    report = detect_retrieval_gaps(
      arguments.corpus,
      adapter,
      limit=arguments.limit,
      min_overlap=arguments.min_overlap,
    )
  except FileNotFoundError as error:
    logger.error("Run `ingest --resolve` first: %s", error)
    return EXIT_ERROR

  logger.info(
    "Found %d out-of-corpus candidates over %d Papers (overlap at least %d).",
    len(report.candidates),
    report.corpus_paper_count,
    report.min_overlap,
  )
  return EXIT_OK


def _build_search_sources() -> list[TopicSearchSource]:
  """Build the sources a saturation check searches, identified to the APIs."""
  identity = SourceIdentity.from_env()
  return [
    OpenAlexAdapter(mailto=identity.openalex_mailto),
    PubMedAdapter(api_key=identity.pubmed_api_key),
  ]


def _query_variant_model(arguments: argparse.Namespace) -> QueryVariantModel:
  """
  Build the LLM the second attempt rephrases queries with, on the cheap tier.

  A missing API key is not a failure here: the check runs without the second
  attempt, which records why (ADR 0009: surface, don't block).
  """
  tier: ModelTier = "cheap"
  try:
    return QueryVariantModel(
      client=_llm_client(arguments),
      model=LlmConfig.from_env().model_for(tier),
      tier=tier,
    )
  except LlmConfigError as error:
    logger.warning("No LLM second attempt: %s", error)
    return QueryVariantModel(client=None, tier=tier, unavailable_reason=str(error))


def _run_saturate(arguments: argparse.Namespace) -> int:
  """Check one gap against the wider literature and append the dated snapshot."""
  paths = arguments.taxonomy or list(SEED_PATHS.values())
  try:
    taxonomies = load_pipeline_taxonomies(paths, corpus=arguments.corpus)
  except TaxonomyError as error:
    logger.error("Taxonomies are invalid:")
    for problem in error.problems:
      logger.error("  - %s", problem)
    return EXIT_ERROR

  try:
    check = saturate_gap(
      arguments.corpus,
      arguments.gap,
      _build_search_sources(),
      taxonomies,
      limit=SATURATE_SEARCH_LIMIT,
      screener=_hit_screener(arguments),
      query_variants=_query_variant_model(arguments),
    )
  except SaturationError as error:
    logger.error("%s", error)
    return EXIT_ERROR
  except FileNotFoundError as error:
    logger.error("Run `ingest` and `detect` first: %s", error)
    return EXIT_ERROR

  for search in check.searches:
    logger.info("  %s (%s): %s", search.source, search.attempt, search.query)
    if search.error:
      logger.warning("    not searched: %s", search.error)
  if check.llm_screening and check.llm_screening.status == "not_run":
    logger.warning("LLM screening: %s", check.llm_screening.reason)
  if check.second_attempt is not None and check.second_attempt.note:
    logger.info("Second attempt: %s", check.second_attempt.note)
  logger.info("%s", check.status_reason)
  # Offline or rate-limited: the run completes, but the check did not happen
  # (ADR 0009: surface, don't block).
  return EXIT_INCOMPLETE if check.status == "not_run" else EXIT_OK


def _hit_screener(arguments: argparse.Namespace) -> HitScreener | None:
  """
  Build the LLM screener for a saturation check, or None without a key.

  A missing key degrades the check to title/abstract screening, recorded as
  such on the snapshot, rather than failing it: the searches are worth keeping
  (ADR 0006, surface, don't block).
  """
  try:
    client = _llm_client(arguments)
  except LlmConfigError as error:
    logger.warning("%s", error)
    return None
  model = LlmConfig.from_env().model_for(arguments.tier)
  return HitScreener(client, model=model, tier=arguments.tier)


def _add_grow_parsers(subcommands) -> None:
  """Add the Grow path's commands: add a suggested Paper, mark it unavailable."""
  add = subcommands.add_parser(
    "add-suggested",
    help=(
      "Add a Retrieval Gap or saturation hit to the Corpus: fetch its open-access "
      "PDF (needs network) or take an uploaded one; prints the outcome as JSON."
    ),
  )
  unavailable = subcommands.add_parser(
    "mark-unavailable",
    help="Record that a suggested paper cannot be obtained, with the reason.",
  )
  for parser in (add, unavailable):
    parser.add_argument("corpus", type=Path, help="Path to the corpus directory.")
    parser.add_argument("--doi", default="", help="The suggested work's DOI.")
    parser.add_argument("--pmid", default="", help="The suggested work's PMID.")
    parser.add_argument(
      "--openalex-id", default="", help="The suggested work's OpenAlex id."
    )
    parser.add_argument(
      "--route",
      choices=["retrieval", "saturation"],
      required=True,
      help="Where the suggestion came from: a Retrieval Gap or a saturation hit.",
    )
    parser.add_argument(
      "--title", default="", help="Title to record if OpenAlex cannot be reached."
    )
    parser.add_argument(
      "--year", type=int, default=None, help="Year to record likewise."
    )
  add.add_argument(
    "--upload",
    type=Path,
    default=None,
    help="A PDF of the paper to add instead of fetching an open-access copy.",
  )
  add.add_argument(
    "--mailto",
    default=None,
    help="Contact email for OpenAlex's polite pool (default: OPENALEX_MAILTO).",
  )
  unavailable.add_argument(
    "--reason", required=True, help="Why the paper cannot be obtained."
  )


def _work_ref(arguments: argparse.Namespace) -> WorkRef:
  return WorkRef(
    doi=arguments.doi, pmid=arguments.pmid, openalex_id=arguments.openalex_id
  )


def _run_add_suggested(arguments: argparse.Namespace) -> int:
  """
  Add one suggested Paper; print the outcome as JSON on stdout.

  Added exits 0. Pending (no PDF yet) exits 2: the run completed, but the
  researcher still has to upload the PDF or mark the paper unavailable.
  """
  upload = None
  if arguments.upload is not None:
    try:
      upload = arguments.upload.read_bytes()
    except OSError as error:
      logger.error("Could not read the uploaded PDF: %s", error)
      return EXIT_ERROR
  try:
    outcome = add_suggested_paper(
      arguments.corpus,
      _work_ref(arguments),
      arguments.route,
      openalex=OpenAlexAdapter(mailto=_openalex_mailto(arguments)),
      download=httpx_fetch_bytes,
      upload=upload,
      title=arguments.title,
      year=arguments.year,
    )
  except GrowError as error:
    logger.error("%s", error)
    return EXIT_ERROR
  sys.stdout.write(outcome.model_dump_json(indent=2) + "\n")
  if outcome.status == "already_in_corpus":
    logger.error("%s", outcome.message)
    return EXIT_ERROR
  logger.info("%s", outcome.message)
  return EXIT_INCOMPLETE if outcome.status == "pending" else EXIT_OK


def _run_mark_unavailable(arguments: argparse.Namespace) -> int:
  """Record a suggested paper as unavailable, with its reason and date."""
  try:
    record = mark_unavailable(
      arguments.corpus,
      _work_ref(arguments),
      arguments.route,
      arguments.reason,
      title=arguments.title,
      year=arguments.year,
    )
  except GrowError as error:
    logger.error("%s", error)
    return EXIT_ERROR
  logger.info("Marked unavailable: %s (%s)", record.title or record.doi, record.reason)
  return EXIT_OK


def _run_parse(arguments: argparse.Namespace) -> int:
  """Parse each Paper's PDF into sectioned text under paper-data/."""
  try:
    report = parse_corpus(arguments.corpus)
  except FileNotFoundError as error:
    logger.error("Run `ingest` first: %s", error)
    return EXIT_ERROR

  logger.info(
    "Parsed %d Papers into paper-data/ (%d failed).",
    len(report.parsed_paths),
    len(report.failures),
  )
  for failure in report.failures:
    logger.warning("  %s: %s", failure.citation_key, failure.error)
  if report.failures:
    logger.warning(
      "%d Paper(s) did not parse; see the dashboard's needs-attention queue.",
      len(report.failures),
    )
    return EXIT_INCOMPLETE
  return EXIT_OK


def _run_diagnose(arguments: argparse.Namespace) -> int:
  """Report the parse/extract funnel and where Papers fall out of it."""
  try:
    report = diagnose_corpus(arguments.corpus)
  except FileNotFoundError as error:
    logger.error("Run `ingest` first: %s", error)
    return EXIT_ERROR

  logger.info("%s", render_funnel(report))

  if arguments.min_rate is not None and report.end_to_end_rate < arguments.min_rate:
    logger.error(
      "End-to-end rate %.0f%% is below the %.0f%% target.",
      report.end_to_end_rate * 100,
      arguments.min_rate * 100,
    )
    return EXIT_ERROR
  return EXIT_OK


def _run_baseline(arguments: argparse.Namespace) -> int:
  """Report what every stage kept and lost over the Corpus, as Markdown."""
  try:
    report = baseline_corpus(arguments.corpus)
  except FileNotFoundError as error:
    logger.error("Run `ingest` first: %s", error)
    return EXIT_ERROR

  logger.info("%s", render_baseline(report))
  return EXIT_OK


def _run_apply_parse_corrections(arguments: argparse.Namespace) -> int:
  """Write the dashboard's manual parse corrections into paper-data/."""
  try:
    applied = apply_parse_corrections(arguments.corpus)
  except FileNotFoundError as error:
    logger.error("Run `ingest` first: %s", error)
    return EXIT_ERROR

  if not applied:
    logger.info("No manual parse corrections to apply.")
    return EXIT_OK
  logger.info(
    "Applied %d manual parse correction(s); rerun `extract` to pick them up.",
    len(applied),
  )
  for key in applied:
    logger.info("  %s", key)
  return EXIT_OK


def _run_extract(arguments: argparse.Namespace) -> int:
  """LLM-extract structured facts with verified Evidence into the artifact."""
  try:
    client = _llm_client(arguments)
  except LlmConfigError as error:
    logger.error("%s", error)
    return EXIT_ERROR

  try:
    report = extract_corpus(arguments.corpus, client, tier=arguments.tier)
  except FileNotFoundError as error:
    logger.error("Run `ingest` and `parse` first: %s", error)
    return EXIT_ERROR

  logger.info(
    "Extracted %d Papers (%d failed, %d facts dropped).",
    len(report.extractions),
    len(report.failures),
    len(report.unverified),
  )
  for failure in report.failures:
    logger.warning("  %s: %s", failure.citation_key, failure.reason)
  if report.failures or report.unverified:
    logger.warning(
      "%d Paper(s) rejected, %d fact(s) dropped; see the needs-attention queue.",
      len(report.failures),
      len(report.unverified),
    )
    return EXIT_INCOMPLETE
  return EXIT_OK


def _run_explain(arguments: argparse.Namespace) -> int:
  """LLM-explain each Paper's experiment in domain and lay language."""
  try:
    client = _llm_client(arguments)
  except LlmConfigError as error:
    logger.error("%s", error)
    return EXIT_ERROR

  try:
    report = explain_corpus(arguments.corpus, client, tier=arguments.tier)
  except FileNotFoundError as error:
    logger.error("Run `ingest` and `parse` first: %s", error)
    return EXIT_ERROR

  logger.info(
    "Explained %d Papers (%d failed).",
    len(report.explanation_paths),
    len(report.failures),
  )
  for failure in report.failures:
    logger.warning("  %s: %s", failure.citation_key, failure.reason)
  return EXIT_OK


def _run_aggregate(arguments: argparse.Namespace) -> int:
  """Normalize each Paper's extracted facts onto the axis taxonomies."""
  paths = arguments.taxonomy or list(SEED_PATHS.values())
  try:
    taxonomies = load_pipeline_taxonomies(paths, corpus=arguments.corpus)
  except TaxonomyError as error:
    logger.error("Taxonomies are invalid:")
    for problem in error.problems:
      logger.error("  - %s", problem)
    return EXIT_ERROR

  try:
    client = _llm_client(arguments)
  except LlmConfigError as error:
    logger.error("%s", error)
    return EXIT_ERROR

  try:
    report = aggregate_corpus(arguments.corpus, client, taxonomies, tier=arguments.tier)
  except FileNotFoundError as error:
    logger.error("Run `extract` first: %s", error)
    return EXIT_ERROR

  logger.info(
    "Normalized %d Papers (%d failed).",
    len(report.normalized),
    len(report.failures),
  )
  for failure in report.failures:
    logger.warning("  %s: %s", failure.citation_key, failure.reason)
  return EXIT_OK


def _run_detect(arguments: argparse.Namespace) -> int:
  """Find the Corpus's Candidate Gaps and write the CandidateGaps artifact."""
  try:
    client = _llm_client(arguments)
    embedding_config = EmbeddingConfig.from_env()
  except (LlmConfigError, EmbeddingConfigError) as error:
    logger.error("%s", error)
    return EXIT_ERROR

  try:
    report = detect_corpus(
      arguments.corpus,
      client,
      tier=arguments.tier,
      embedder=build_embedder(embedding_config),
      merge_threshold=embedding_config.merge_threshold,
    )
  except FileNotFoundError as error:
    logger.error("Run `ingest`, `extract`, and `aggregate` first: %s", error)
    return EXIT_ERROR

  logger.info(
    "Found %d candidate gaps over %d Papers. %s",
    len(report.gaps),
    report.corpus_paper_count,
    report.candidate_rule,
  )
  return EXIT_OK


def _run_promote_gold(arguments: argparse.Namespace) -> int:
  """Build the Gold Extraction from the Extraction plus its review overlay."""
  try:
    gold = promote_gold(arguments.corpus)
  except FileNotFoundError as error:
    logger.error("Run `extract` and review the Extraction first: %s", error)
    return EXIT_ERROR
  except GoldPromotionRefusedError as error:
    # Logged last so a caller that shows only the final line shows the reason.
    logger.error(
      "Gold promotion refused, nothing written. %s. Fix or remove that fact "
      "on the Verify page, then promote again.",
      error,
    )
    return EXIT_ERROR

  output_path = arguments.out or default_gold_path(arguments.corpus)
  write_gold(gold, output_path)
  fact_count = sum(
    len(getattr(extraction.fields, name))
    for extraction in gold.extractions
    for name in _GOLD_FIELD_NAMES
  )
  logger.info(
    "Promoted %d Papers to a Gold Extraction (%d facts) at %s.",
    len(gold.extractions),
    fact_count,
    output_path,
  )
  return EXIT_OK


def _run_taxonomy(arguments: argparse.Namespace) -> int:
  """Validate a taxonomy file (any axis) and report any problems."""
  try:
    taxonomy = load_taxonomy(arguments.file)
  except TaxonomyError as error:
    logger.error("Taxonomy '%s' is invalid:", arguments.file)
    for problem in error.problems:
      logger.error("  - %s", problem)
    return EXIT_ERROR

  logger.info(
    "Taxonomy '%s' is valid: %d %s categories for domain '%s' (source: %s).",
    arguments.file,
    len(taxonomy.topics),
    taxonomy.meta.axis,
    taxonomy.meta.domain,
    taxonomy.meta.source,
  )
  return EXIT_OK


def _add_aliases_parser(subcommands) -> None:
  """Add the `aliases` command: suggest, review, and promote Corpus aliases."""
  aliases = subcommands.add_parser(
    "aliases",
    help="Suggest, approve, reject, and promote a Corpus's taxonomy aliases.",
  )
  actions = aliases.add_subparsers(dest="alias_action", required=True)

  suggest = actions.add_parser(
    "suggest-mesh",
    help="Log MeSH entry terms and child descriptors as proposed aliases (network).",
  )
  suggest.add_argument("corpus", type=Path, help="Path to the corpus directory.")
  _add_taxonomy_option(suggest)

  listing = actions.add_parser("list", help="List the Corpus's alias overlay.")
  listing.add_argument("corpus", type=Path, help="Path to the corpus directory.")
  listing.add_argument(
    "--status",
    choices=["proposed", "approved", "rejected", "promoted"],
    default=None,
    help="Only list aliases with this status.",
  )

  unmapped = actions.add_parser(
    "suggest-unmapped",
    help="List recurring verified unmapped terms that may become aliases.",
  )
  unmapped.add_argument("corpus", type=Path, help="Path to the corpus directory.")
  _add_taxonomy_option(unmapped)

  for name, text in (
    ("approve", "Approve a proposed alias for this Corpus only."),
    (
      "approve-unmapped",
      "Map an offered unmapped term onto a category, for this Corpus only.",
    ),
    ("reject", "Reject a proposed alias, or reverse an approval."),
    ("promote", "Copy an approved alias into the shared taxonomy file."),
  ):
    action = actions.add_parser(name, help=text)
    action.add_argument("corpus", type=Path, help="Path to the corpus directory.")
    action.add_argument("axis", choices=list(AXES), help="The category's axis.")
    action.add_argument("category", help="The category id the alias belongs to.")
    action.add_argument("alias", help="The alias or term phrase, as listed.")
    _add_taxonomy_option(action)


def _add_taxonomy_option(parser: argparse.ArgumentParser) -> None:
  """Add the repeatable `--taxonomy` option the alias actions read."""
  parser.add_argument(
    "--taxonomy",
    type=Path,
    action="append",
    default=None,
    help="Taxonomy file, once per axis (default: the shipped seeds).",
  )


def _run_aliases(arguments: argparse.Namespace) -> int:
  """Run one `aliases` action against a Corpus's Alias Overlay."""
  overlay = read_alias_overlay(arguments.corpus)
  if arguments.alias_action == "list":
    _log_overlay(overlay, arguments.status)
    return EXIT_OK

  paths: list[Path] = arguments.taxonomy or list(SEED_PATHS.values())
  try:
    by_path = {path: load_taxonomy(path) for path in paths}
  except TaxonomyError as error:
    logger.error("Taxonomies are invalid:")
    for problem in error.problems:
      logger.error("  - %s", problem)
    return EXIT_ERROR

  try:
    updated = _alias_action(arguments, overlay, by_path)
  except (AliasOverlayError, FileNotFoundError, httpx.HTTPError) as error:
    logger.error("%s", _alias_failure(error))
    return EXIT_ERROR

  if updated is not None:
    path = write_alias_overlay(arguments.corpus, updated)
    logger.info("Alias overlay written to %s.", path)
  return EXIT_OK


def _alias_failure(error: Exception) -> str:
  """Explain why an `aliases` action failed."""
  if isinstance(error, FileNotFoundError):
    return f"Run extract and aggregate on the Corpus first: {error}"
  if isinstance(error, httpx.HTTPError):
    return f"MeSH could not be reached: {error}"
  return str(error)


def _alias_action(
  arguments: argparse.Namespace,
  overlay: AliasOverlay,
  by_path: dict[Path, Taxonomy],
) -> AliasOverlay | None:
  """Apply one alias action and return the overlay, or None if nothing changed."""
  taxonomies = list(by_path.values())
  if arguments.alias_action == "suggest-unmapped":
    _log_screen(_screen(arguments.corpus, taxonomies, overlay))
    return None
  if arguments.alias_action == "suggest-mesh":
    mesh = MeshVocabulary()
    before = len(overlay.entries)
    for taxonomy in taxonomies:
      overlay = propose_aliases(overlay, suggest_mesh_aliases(taxonomy, mesh))
    logger.info(
      "Proposed %d new MeSH aliases; approve each with `aliases approve`.",
      len(overlay.entries) - before,
    )
    return overlay

  target = (arguments.axis, arguments.category, arguments.alias)
  if arguments.alias_action == "approve-unmapped":
    screen = _screen(arguments.corpus, taxonomies, overlay)
    candidate = find_candidate(screen, arguments.axis, arguments.alias)
    if candidate is None:
      raise AliasOverlayError(
        f"'{arguments.alias}' is not an offered {arguments.axis} term; see "
        "`aliases suggest-unmapped` for the terms on offer and why others are not."
      )
    return promote_unmapped_term(overlay, taxonomies, candidate, arguments.category)
  if arguments.alias_action == "approve":
    return approve_alias(overlay, taxonomies, *target)
  if arguments.alias_action == "reject":
    return reject_alias(overlay, *target)
  path = next(
    (p for p, taxonomy in by_path.items() if taxonomy.meta.axis == arguments.axis),
    None,
  )
  if path is None:
    raise AliasOverlayError(f"No {arguments.axis} taxonomy file was given.")
  promoted = promote_alias(overlay, path, *target)
  logger.info("Promoted '%s' into %s.", arguments.alias, path)
  return promoted


def _screen(
  corpus: Path, taxonomies: list[Taxonomy], overlay: AliasOverlay
) -> UnmappedTermScreen:
  """Screen a Corpus's unmapped terms from its Extraction and aggregate artifacts."""
  return screen_unmapped_terms(
    read_normalized_facts(corpus), read_extractions(corpus), taxonomies, overlay
  )


def _log_screen(screen: UnmappedTermScreen) -> None:
  """Log the offered unmapped terms with their Papers, and a tally of the rest."""
  logger.info(
    "%d unmapped terms offered (verified Evidence, 2+ Papers, past the stoplist).",
    len(screen.offered),
  )
  for candidate in screen.offered:
    logger.info(
      "  %s: %s  (%d Papers: %s)",
      candidate.axis,
      candidate.term,
      len(candidate.occurrences),
      ", ".join(candidate.citation_keys),
    )
  reasons = Counter(term.reason for term in screen.set_aside)
  logger.info(
    "%d set aside: %s.",
    len(screen.set_aside),
    ", ".join(f"{count} {reason}" for reason, count in sorted(reasons.items()))
    or "none",
  )
  if screen.offered:
    logger.info(
      "Map one with `aliases approve-unmapped <corpus> <axis> <category> <term>`."
    )


def _log_overlay(overlay: AliasOverlay, status: str | None) -> None:
  """Log each overlay alias with its status and provenance."""
  entries = [e for e in overlay.entries if status is None or e.status == status]
  logger.info("%d aliases in the overlay.", len(entries))
  for entry in entries:
    logger.info(
      "  [%s] %s / %s: %s  (%s)",
      entry.status,
      entry.axis,
      entry.category_id,
      entry.alias,
      entry.provenance.describe(),
    )


if __name__ == "__main__":
  sys.exit(main())
