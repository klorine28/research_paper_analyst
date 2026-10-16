"""
The `aliases` command: suggest, review, and promote a Corpus's taxonomy aliases.

Split from `cli` so the pipeline-stage commands stay in one readable module.
Every action reads and writes the Corpus's Alias Overlay (ADR 0010); the LLM
for `suggest-llm` is built by `cli`, which owns the LLM cache and usage log.
"""

import argparse
import logging
from collections import Counter
from collections.abc import Callable
from pathlib import Path

import httpx

from research_gap_dashboard.aggregate import read_normalized_facts
from research_gap_dashboard.extract import read_extractions
from research_gap_dashboard.llm import LlmConfigError
from research_gap_dashboard.llm_aliases import (
  AliasSuggestionModel,
  suggest_llm_aliases,
  thin_categories,
)
from research_gap_dashboard.taxonomy import (
  AXES,
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

logger = logging.getLogger(__name__)

# Mirrors `cli.EXIT_OK` / `cli.EXIT_ERROR`; restated so `cli` can import this.
EXIT_OK = 0
EXIT_ERROR = 1


def add_aliases_parser(subcommands) -> None:
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
  add_taxonomy_option(suggest)

  llm = actions.add_parser(
    "suggest-llm",
    help="Ask the LLM, once, for aliases of thin categories; log them as proposed.",
  )
  llm.add_argument("corpus", type=Path, help="Path to the corpus directory.")
  llm.add_argument(
    "--tier",
    choices=["default", "cheap"],
    default="cheap",
    help="Model tier to suggest with (default: the cheap model).",
  )
  add_taxonomy_option(llm)

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
  add_taxonomy_option(unmapped)

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
    add_taxonomy_option(action)


def add_taxonomy_option(parser: argparse.ArgumentParser) -> None:
  """Add the repeatable `--taxonomy` option the alias actions read."""
  parser.add_argument(
    "--taxonomy",
    type=Path,
    action="append",
    default=None,
    help="Taxonomy file, once per axis (default: the shipped seeds).",
  )


def run_aliases(
  arguments: argparse.Namespace,
  suggestion_model: Callable[[argparse.Namespace], AliasSuggestionModel],
) -> int:
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
    updated = _alias_action(arguments, overlay, by_path, suggestion_model)
  except (
    AliasOverlayError,
    FileNotFoundError,
    httpx.HTTPError,
    LlmConfigError,
  ) as error:
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
    return f"The vocabulary source could not be reached: {error}"
  return str(error)


def _alias_action(
  arguments: argparse.Namespace,
  overlay: AliasOverlay,
  by_path: dict[Path, Taxonomy],
  suggestion_model: Callable[[argparse.Namespace], AliasSuggestionModel],
) -> AliasOverlay | None:
  """Apply one alias action and return the overlay, or None if nothing changed."""
  taxonomies = list(by_path.values())
  if arguments.alias_action == "suggest-unmapped":
    _log_screen(_screen(arguments.corpus, taxonomies, overlay))
    return None
  if arguments.alias_action == "suggest-mesh":
    return _suggest_mesh(overlay, taxonomies)
  if arguments.alias_action == "suggest-llm":
    return _suggest_llm(overlay, taxonomies, suggestion_model(arguments))

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
  return _promote(arguments, overlay, by_path)


def _promote(
  arguments: argparse.Namespace,
  overlay: AliasOverlay,
  by_path: dict[Path, Taxonomy],
) -> AliasOverlay:
  """Copy an approved alias into the shared taxonomy file for its axis."""
  target = (arguments.axis, arguments.category, arguments.alias)
  path = next(
    (p for p, taxonomy in by_path.items() if taxonomy.meta.axis == arguments.axis),
    None,
  )
  if path is None:
    raise AliasOverlayError(f"No {arguments.axis} taxonomy file was given.")
  promoted = promote_alias(overlay, path, *target)
  logger.info("Promoted '%s' into %s.", arguments.alias, path)
  return promoted


def _suggest_mesh(overlay: AliasOverlay, taxonomies: list[Taxonomy]) -> AliasOverlay:
  """Log MeSH entry terms and child descriptors as proposed aliases."""
  mesh = MeshVocabulary()
  before = len(overlay.entries)
  for taxonomy in taxonomies:
    overlay = propose_aliases(overlay, suggest_mesh_aliases(taxonomy, mesh))
  logger.info(
    "Proposed %d new MeSH aliases; approve each with `aliases approve`.",
    len(overlay.entries) - before,
  )
  return overlay


def _suggest_llm(
  overlay: AliasOverlay, taxonomies: list[Taxonomy], llm: AliasSuggestionModel
) -> AliasOverlay:
  """Log LLM suggestions for each taxonomy's thin categories as proposed."""
  for taxonomy in taxonomies:
    thin = thin_categories(taxonomy, overlay)
    logger.info(
      "%s: %d thin categories: %s.",
      taxonomy.meta.axis,
      len(thin),
      ", ".join(topic.id for topic in thin) or "none",
    )
  before = len(overlay.entries)
  for taxonomy in taxonomies:
    overlay = propose_aliases(overlay, suggest_llm_aliases(taxonomy, overlay, llm))
  logger.info(
    "Proposed %d new LLM aliases; approve each with `aliases approve`.",
    len(overlay.entries) - before,
  )
  return overlay


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
