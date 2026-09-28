"""
Command line entry point driving the pipeline stages.

The CLI only parses arguments, configures logging, and turns stage errors
into exit codes; all behavior lives in the stage modules.
"""

import argparse
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

from research_gap_dashboard.aggregate import aggregate_corpus, load_pipeline_taxonomies
from research_gap_dashboard.dashboard.extraction_review import (
  FIELD_NAMES as _GOLD_FIELD_NAMES,
)
from research_gap_dashboard.detect import detect_corpus
from research_gap_dashboard.explain import explain_corpus
from research_gap_dashboard.extract import extract_corpus
from research_gap_dashboard.ingest import (
  CorpusLayoutError,
  CorpusSizeError,
  ingest_corpus,
)
from research_gap_dashboard.llm import LlmConfigError, build_llm_client
from research_gap_dashboard.parsing import parse_corpus
from research_gap_dashboard.promote_gold import (
  default_gold_path,
  promote_gold,
  write_gold,
)
from research_gap_dashboard.retrieval import (
  DEFAULT_LIMIT,
  DEFAULT_MIN_OVERLAP,
  detect_retrieval_gaps,
)
from research_gap_dashboard.sources import OpenAlexAdapter, PubMedAdapter
from research_gap_dashboard.taxonomy import (
  CARDIOLOGY_SEED_PATH,
  SEED_PATHS,
  TaxonomyError,
  load_taxonomy,
)

LLM_CACHE_DIR = ".llm-cache"

logger = logging.getLogger("research_gap_dashboard")

EXIT_OK = 0
EXIT_ERROR = 1


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
    help="Contact email to join OpenAlex's polite pool when resolving.",
  )
  parse = subcommands.add_parser(
    "parse",
    help="Parse each Paper's PDF into sectioned text under paper-data/.",
  )
  parse.add_argument("corpus", type=Path, help="Path to the corpus directory.")
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
    help="Contact email to join OpenAlex's polite pool when resolving.",
  )
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
  return parser


def main(argv: Sequence[str] | None = None) -> int:
  """Run one pipeline command; return the process exit code."""
  logging.basicConfig(level=logging.INFO, format="%(message)s")
  arguments = build_parser().parse_args(argv)

  runners = {
    "ingest": _run_ingest,
    "parse": _run_parse,
    "extract": _run_extract,
    "explain": _run_explain,
    "aggregate": _run_aggregate,
    "detect": _run_detect,
    "retrieve": _run_retrieve,
    "promote-gold": _run_promote_gold,
    "taxonomy": _run_taxonomy,
  }
  return runners[arguments.command](arguments)


def _run_ingest(arguments: argparse.Namespace) -> int:
  """Pair the paper list with the PDFs and write the CorpusManifest."""
  adapter = _build_source(arguments) if arguments.resolve else None
  try:
    manifest = ingest_corpus(arguments.corpus, adapter=adapter)
  except (CorpusSizeError, CorpusLayoutError) as error:
    logger.error("%s", error)
    return EXIT_ERROR

  unresolved = sum(1 for paper in manifest.papers if paper.resolution_error)
  logger.info(
    "Corpus: %d Papers, %d unmatched entries, %d orphan PDFs, %d DOIs unresolved.",
    len(manifest.papers),
    len(manifest.unmatched_entries),
    len(manifest.orphan_pdfs),
    unresolved,
  )
  return EXIT_OK


def _build_source(arguments: argparse.Namespace):
  """Build the source adapter the ingest run resolves DOIs against."""
  if arguments.source == "pubmed":
    return PubMedAdapter()
  return OpenAlexAdapter(mailto=arguments.mailto)


def _run_retrieve(arguments: argparse.Namespace) -> int:
  """Find out-of-corpus candidates by citation overlap and write the artifact."""
  adapter = OpenAlexAdapter(mailto=arguments.mailto)
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
  return EXIT_OK


def _run_extract(arguments: argparse.Namespace) -> int:
  """LLM-extract structured facts with verified Evidence into the artifact."""
  try:
    client = build_llm_client(cache_dir=arguments.corpus / LLM_CACHE_DIR)
  except LlmConfigError as error:
    logger.error("%s", error)
    return EXIT_ERROR

  try:
    report = extract_corpus(arguments.corpus, client, tier=arguments.tier)
  except FileNotFoundError as error:
    logger.error("Run `ingest` and `parse` first: %s", error)
    return EXIT_ERROR

  logger.info(
    "Extracted %d Papers (%d failed).",
    len(report.extractions),
    len(report.failures),
  )
  for failure in report.failures:
    logger.warning("  %s: %s", failure.citation_key, failure.reason)
  return EXIT_OK


def _run_explain(arguments: argparse.Namespace) -> int:
  """LLM-explain each Paper's experiment in domain and lay language."""
  try:
    client = build_llm_client(cache_dir=arguments.corpus / LLM_CACHE_DIR)
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
    taxonomies = load_pipeline_taxonomies(paths)
  except TaxonomyError as error:
    logger.error("Taxonomies are invalid:")
    for problem in error.problems:
      logger.error("  - %s", problem)
    return EXIT_ERROR

  try:
    client = build_llm_client(cache_dir=arguments.corpus / LLM_CACHE_DIR)
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
    client = build_llm_client(cache_dir=arguments.corpus / LLM_CACHE_DIR)
  except LlmConfigError as error:
    logger.error("%s", error)
    return EXIT_ERROR

  try:
    report = detect_corpus(arguments.corpus, client, tier=arguments.tier)
  except FileNotFoundError as error:
    logger.error("Run `ingest`, `extract`, and `aggregate` first: %s", error)
    return EXIT_ERROR

  logger.info(
    "Found %d candidate gaps over %d Papers (cells with at most %d Papers).",
    len(report.gaps),
    report.corpus_paper_count,
    report.sparse_max_count,
  )
  return EXIT_OK


def _run_promote_gold(arguments: argparse.Namespace) -> int:
  """Build the Gold Extraction from the Extraction plus its review overlay."""
  try:
    gold = promote_gold(arguments.corpus)
  except FileNotFoundError as error:
    logger.error("Run `extract` and review the Extraction first: %s", error)
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


if __name__ == "__main__":
  sys.exit(main())
