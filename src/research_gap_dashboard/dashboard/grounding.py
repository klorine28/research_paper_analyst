"""
The dashboard's own verbatim-Evidence matcher for Extraction Review corrections.

Any edited or added Evidence a reviewer saves must quote the Paper's parsed text
verbatim (CODING_STANDARDS > Research integrity; ADR 0003). The dashboard may not
import a pipeline stage (ADR 0002), so it owns this copy of the read logic and the
whitespace normalisation instead of calling `extract._verify_evidence`.

The two must normalise identically, or a correction valid in one could be
rejected by the other. `normalize` mirrors `extract._normalize` exactly; a test
pins the two so drift is caught. The dashboard reads `paper-data/<key>.parsed.json`
through its own read model, tolerating fields it does not use like every other
artifact reader here.
"""

import re
from pathlib import Path

from pydantic import BaseModel, ConfigDict

PAPER_DATA_DIR = "paper-data"
PARSED_SUFFIX = ".parsed.json"

# Mirror of `extract._normalize`: collapse whitespace so a quote matches across a
# PDF's line wrapping. Keep this identical to the pipeline's normaliser (a test
# enforces it), or a correction grounded there could be refused here.
_WHITESPACE = re.compile(r"\s+")


class UngroundedEvidenceError(ValueError):
  """Raised when a correction's Evidence is not verbatim in the Paper's text."""


class _ReadModel(BaseModel):
  """A read model that tolerates parsed-paper fields the dashboard does not use."""

  model_config = ConfigDict(extra="ignore")


class ParsedSectionRead(_ReadModel):
  """One labeled section of a Paper's parsed text, as the dashboard reads it."""

  label: str = "other"
  heading: str = ""
  text: str = ""


class ParsedPaperRead(_ReadModel):
  """A Paper's parsed sections, projected onto what grounding needs."""

  sections: list[ParsedSectionRead] = []

  @property
  def full_text(self) -> str:
    """The whole Paper as plain text, in reading order (mirrors ParsedPaper)."""
    return "\n\n".join(section.text for section in self.sections if section.text)

  @property
  def section_labels(self) -> list[str]:
    """The distinct section labels present, in first-seen order, for picking one."""
    seen: list[str] = []
    for section in self.sections:
      if section.label not in seen:
        seen.append(section.label)
    return seen


def normalize(text: str) -> str:
  """Collapse whitespace so quotes match across a PDF's line wrapping."""
  return _WHITESPACE.sub(" ", text).strip()


def _parsed_path(corpus_root: Path, citation_key: str) -> Path:
  """Return where a corpus directory keeps a Paper's parsed text."""
  return corpus_root / PAPER_DATA_DIR / f"{citation_key}{PARSED_SUFFIX}"


def has_parsed_paper(corpus_root: Path, citation_key: str) -> bool:
  """Report whether a Paper has parsed text the parse stage wrote."""
  return _parsed_path(corpus_root, citation_key).is_file()


def read_parsed_paper(corpus_root: Path, citation_key: str) -> ParsedPaperRead:
  """Read a Paper's parsed sections, so a correction can be grounded against them."""
  path = _parsed_path(corpus_root, citation_key)
  if not path.is_file():
    raise FileNotFoundError(
      f"No parsed text at '{path}'. Run `parse` on '{corpus_root}' first."
    )
  return ParsedPaperRead.model_validate_json(path.read_text(encoding="utf-8"))


def is_grounded(passage: str, parsed: ParsedPaperRead) -> bool:
  """Report whether a passage appears verbatim in the Paper's parsed text."""
  cleaned = passage.strip()
  if not cleaned:
    return False
  return normalize(cleaned) in normalize(parsed.full_text)


def verify_grounded(passage: str, parsed: ParsedPaperRead) -> None:
  """Refuse a correction whose Evidence is not verbatim in the Paper's text."""
  if not passage.strip():
    raise UngroundedEvidenceError("Evidence passage is empty.")
  if not is_grounded(passage, parsed):
    raise UngroundedEvidenceError(
      f"Evidence passage is not found verbatim in the paper text: {passage[:80]!r}"
    )
