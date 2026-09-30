"""
Apply manual parse corrections from the dashboard overlay to the parse artifact.

The dashboard writes a researcher's pasted full text to a `judgments/` overlay
(ADR 0002/0003); it never edits the parse artifact itself. This pipeline step
reads that overlay and writes the corrected `paper-data/<key>.parsed.json` so the
downstream `extract` stage reruns on the fixed text (issue #46 Step 3, ADR 0006).

The corrected text is stored as a single `other` section: a manual paste has no
reliable heading structure, but the extract stage reads the Paper's full text,
so a flat section is enough to recover the facts. Each rewritten Paper is
stamped with the `manual` parser so its provenance is visible.
"""

import logging
from pathlib import Path

from research_gap_dashboard.corpus_layout import inspect_corpus_layout
from research_gap_dashboard.dashboard.parse_corrections import load_parse_corrections
from research_gap_dashboard.ingest import read_manifest
from research_gap_dashboard.parsing import PARSED_SUFFIX, ParsedPaper, ParsedSection

logger = logging.getLogger(__name__)

MANUAL_PARSER = "manual"


def apply_parse_corrections(root: Path) -> list[str]:
  """
  Write each overlay correction to the parse artifact; return the keys applied.

  A correction for a citation key not in the manifest is skipped (logged), so a
  stale overlay entry never invents a Paper.
  """
  log = load_parse_corrections(root)
  if not log.corrections:
    return []

  known = {paper.citation_key for paper in read_manifest(root).papers}
  paper_data_dir = inspect_corpus_layout(root).layout.paper_data_dir
  paper_data_dir.mkdir(parents=True, exist_ok=True)

  applied: list[str] = []
  for correction in log.corrections:
    key = correction.citation_key
    if key not in known:
      logger.warning("Skipping parse correction for unknown Paper '%s'.", key)
      continue
    parsed = ParsedPaper(
      source_pdf=Path(f"{key}.pdf"),
      sections=[
        ParsedSection(label="other", heading="", text=correction.corrected_text)
      ],
      parser=MANUAL_PARSER,
    )
    out_path = paper_data_dir / f"{key}{PARSED_SUFFIX}"
    out_path.write_text(parsed.model_dump_json(indent=2), encoding="utf-8")
    applied.append(key)

  logger.info(
    "Applied %d manual parse correction(s) into %s.", len(applied), paper_data_dir
  )
  return applied
