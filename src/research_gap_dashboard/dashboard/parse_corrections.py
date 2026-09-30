"""
Manual parse corrections as a researcher-review overlay (issue #46 Step 3).

When a PDF parses to nothing or to garbled text, the needs-attention queue lets
a researcher paste the Paper's correct full text. Like every dashboard write
(ADR 0002, ADR 0003) that correction is an *overlay* under `judgments/`, never a
direct edit of the parse artifact: the dashboard runs no pipeline logic. A
pipeline-side step (`apply-parse-corrections`) later reads this overlay and
writes the corrected `paper-data/<key>.parsed.json`, so `extract` reruns on the
fixed text. The split keeps the browser out of the deterministic pipeline.
"""

from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel

JUDGMENTS_DIR = "judgments"
PARSE_CORRECTIONS_NAME = "parse-corrections.json"


class EmptyCorrectionError(ValueError):
  """Raised when a parse correction has no text; a blank fix is never saved."""


class ParseCorrection(BaseModel):
  """One Paper's researcher-supplied full text, replacing a bad parse."""

  citation_key: str
  corrected_text: str
  corrected_at: datetime


class ParseCorrectionLog(BaseModel):
  """Every manual parse correction for one Corpus, on disk as JSON."""

  corrections: list[ParseCorrection] = []

  def correction_for(self, citation_key: str) -> ParseCorrection | None:
    """Return the correction saved for a Paper, or None when there is none."""
    return next((c for c in self.corrections if c.citation_key == citation_key), None)


def _corrections_path(corpus_root: Path) -> Path:
  """Return where a corpus directory keeps its parse-correction overlay."""
  return corpus_root / JUDGMENTS_DIR / PARSE_CORRECTIONS_NAME


def load_parse_corrections(corpus_root: Path) -> ParseCorrectionLog:
  """Read the parse-correction overlay, or an empty log when none is saved yet."""
  path = _corrections_path(corpus_root)
  if not path.is_file():
    return ParseCorrectionLog()
  return ParseCorrectionLog.model_validate_json(path.read_text(encoding="utf-8"))


def record_parse_correction(
  corpus_root: Path, citation_key: str, corrected_text: str
) -> None:
  """Save (or replace) a Paper's manual full-text correction in the overlay."""
  text = corrected_text.strip()
  if not text:
    raise EmptyCorrectionError(
      f"refusing to save an empty parse correction for '{citation_key}'"
    )
  log = load_parse_corrections(corpus_root)
  kept = [c for c in log.corrections if c.citation_key != citation_key]
  kept.append(
    ParseCorrection(
      citation_key=citation_key,
      corrected_text=text,
      corrected_at=datetime.now(timezone.utc),
    )
  )
  _write_log(corpus_root, ParseCorrectionLog(corrections=kept))


def clear_parse_correction(corpus_root: Path, citation_key: str) -> None:
  """Drop a Paper's correction from the overlay (e.g. it was a mistake)."""
  log = load_parse_corrections(corpus_root)
  kept = [c for c in log.corrections if c.citation_key != citation_key]
  _write_log(corpus_root, ParseCorrectionLog(corrections=kept))


def _correction_key(correction: ParseCorrection) -> str:
  """Sort key that keeps the overlay in a stable, diff-friendly order."""
  return correction.citation_key


def _write_log(corpus_root: Path, log: ParseCorrectionLog) -> None:
  """Write the overlay under `judgments/`, sorted for a stable diff."""
  ordered = ParseCorrectionLog(corrections=sorted(log.corrections, key=_correction_key))
  path = _corrections_path(corpus_root)
  path.parent.mkdir(parents=True, exist_ok=True)
  path.write_text(ordered.model_dump_json(indent=2), encoding="utf-8")
