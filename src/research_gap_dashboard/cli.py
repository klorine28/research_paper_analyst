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

from research_gap_dashboard.ingest import (
  CorpusLayoutError,
  CorpusSizeError,
  ingest_corpus,
)
from research_gap_dashboard.sources import OpenAlexAdapter

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
    help="Resolve each DOI against OpenAlex for canonical metadata (needs network).",
  )
  ingest.add_argument(
    "--mailto",
    default=None,
    help="Contact email to join OpenAlex's polite pool when resolving.",
  )
  return parser


def main(argv: Sequence[str] | None = None) -> int:
  """Run one pipeline command; return the process exit code."""
  logging.basicConfig(level=logging.INFO, format="%(message)s")
  arguments = build_parser().parse_args(argv)

  adapter = OpenAlexAdapter(mailto=arguments.mailto) if arguments.resolve else None
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


if __name__ == "__main__":
  sys.exit(main())
