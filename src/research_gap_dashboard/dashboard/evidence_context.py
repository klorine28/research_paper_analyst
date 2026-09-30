"""
Deterministic paragraph-window expansion around a verbatim Evidence anchor.

Research integrity (CODING_STANDARDS) keeps the verbatim-verified anchor quote
as ground truth. This module never asks a model for context: it takes the anchor
and the Paper's own parsed text and, purely in code, widens the quote to the
paragraph that contains it, hard-capped at a character budget, so a reader sees
the anchor in situ without the dashboard inventing anything. Because the context
is a verbatim slice of the Paper, this strengthens traceability rather than
weakening it. The anchor is returned as its own slice so the view can highlight
it inside the window, with the untouched paragraph offered behind an expander.

It also surfaces intra-paper pointers ("as shown in Table 2", "see Section 4.2",
"(Figure 3)") found inside the window as labels. It only labels them: resolving
a pointer to its target, or a citation marker to a Corpus Paper (#44), needs
reference parsing that does not exist yet and is deliberately out of scope here.
"""

import re
from pathlib import Path

from pydantic import BaseModel

from research_gap_dashboard.dashboard.grounding import (
  ParsedPaperRead,
  has_parsed_paper,
  normalize,
  read_parsed_paper,
)

# The whole expanded window (context plus anchor) is capped here. The anchor is
# ground truth and is never trimmed, so a quote longer than the budget still
# shows in full; only the surrounding context is clipped to fit.
WINDOW_CHAR_BUDGET = 600

# Paragraphs inside a parsed section are separated by a blank line; split on that
# before whitespace-normalising so the paragraph boundary survives normalisation.
_PARAGRAPH_BREAK = re.compile(r"\n\s*\n")

# Intra-paper pointers the code labels but never resolves. The keyword fixes the
# kind; the identifier is a figure/table number, a dotted section number, or a
# single appendix letter. A leading word boundary keeps "fig" out of "config".
_POINTER_PATTERN = re.compile(
  r"\b(?P<word>Tables?|Figures?|Fig|Sections?|Sec|Equations?|Eq|Appendix|Appendices)"
  r"\.?\s*(?P<id>[A-Z]?\d+(?:\.\d+)*[a-z]?|[A-Z])\b",
  re.IGNORECASE,
)

_KIND_CANONICAL: dict[str, str] = {
  "table": "Table",
  "tables": "Table",
  "figure": "Figure",
  "figures": "Figure",
  "fig": "Figure",
  "section": "Section",
  "sections": "Section",
  "sec": "Section",
  "equation": "Equation",
  "equations": "Equation",
  "eq": "Equation",
  "appendix": "Appendix",
  "appendices": "Appendix",
}


class IntraPaperPointer(BaseModel):
  """A within-Paper reference found near the anchor, labeled but not resolved."""

  kind: str
  label: str
  text: str


class ParsedSectionSpan(BaseModel):
  """A section's normalised heading and full text, used to label the expander."""

  heading: str
  text: str


class ExpandedEvidence(BaseModel):
  """A verbatim anchor quote widened to its containing paragraph, hard-capped."""

  anchor: str
  before: str = ""
  after: str = ""
  full_section: str = ""
  section_heading: str = ""
  grounded: bool = True
  truncated_before: bool = False
  truncated_after: bool = False
  pointers: list[IntraPaperPointer] = []

  @property
  def window(self) -> str:
    """The context-plus-anchor slice the card shows, anchor in the middle."""
    return f"{self.before}{self.anchor}{self.after}"

  @property
  def has_context(self) -> bool:
    """Whether expansion added anything to show beyond the anchor itself."""
    return bool(self.before or self.after)

  @property
  def full_section_adds_more(self) -> bool:
    """Whether the untouched paragraph section holds more than the window."""
    return self.full_section.strip() != self.window.strip()


def _paragraphs(section_text: str) -> list[str]:
  """Split a section into whitespace-normalised, non-empty paragraphs."""
  parts = _PARAGRAPH_BREAK.split(section_text)
  return [normalized for part in parts if (normalized := normalize(part))]


def _clip_before(before: str, budget: int) -> tuple[str, bool]:
  """Keep the tail of the leading context within budget, on a word boundary."""
  if len(before) <= budget:
    return before, False
  clipped = before[len(before) - budget :]
  space = clipped.find(" ")
  if space != -1:
    clipped = clipped[space + 1 :]
  return clipped, True


def _clip_after(after: str, budget: int) -> tuple[str, bool]:
  """Keep the head of the trailing context within budget, on a word boundary."""
  if len(after) <= budget:
    return after, False
  clipped = after[:budget]
  space = clipped.rfind(" ")
  if space != -1:
    clipped = clipped[:space]
  return clipped, True


def _detect_pointers(window: str) -> list[IntraPaperPointer]:
  """Label the distinct intra-paper pointers appearing in the window, in order."""
  pointers: list[IntraPaperPointer] = []
  seen: set[str] = set()
  for match in _POINTER_PATTERN.finditer(window):
    kind = _KIND_CANONICAL[match.group("word").lower().rstrip(".")]
    label = f"{kind} {match.group('id')}"
    if label in seen:
      continue
    seen.add(label)
    pointers.append(IntraPaperPointer(kind=kind, label=label, text=match.group(0)))
  return pointers


def _build(
  paragraph: str, start: int, length: int, section: ParsedSectionSpan, budget: int
) -> ExpandedEvidence:
  """Widen an anchor to its paragraph, cap the context, and read off pointers."""
  anchor = paragraph[start : start + length]
  before = paragraph[:start]
  after = paragraph[start + length :]

  context_budget = max(0, budget - len(anchor))
  half = context_budget // 2
  keep_after = min(len(after), context_budget - min(len(before), half))
  keep_before = min(len(before), context_budget - keep_after)

  before, truncated_before = _clip_before(before, keep_before)
  after, truncated_after = _clip_after(after, keep_after)

  evidence = ExpandedEvidence(
    anchor=anchor,
    before=before,
    after=after,
    full_section=section.text,
    section_heading=section.heading,
    truncated_before=truncated_before,
    truncated_after=truncated_after,
  )
  evidence.pointers = _detect_pointers(evidence.window)
  return evidence


def expand_evidence(
  anchor: str, parsed: ParsedPaperRead, *, budget: int = WINDOW_CHAR_BUDGET
) -> ExpandedEvidence:
  """
  Expand a verbatim anchor to its containing paragraph, hard-capped at budget.

  Searches the Paper's parsed paragraphs for the one that contains the anchor
  verbatim (whitespace-normalised, matching the grounding invariant). When the
  anchor cannot be located — e.g. it spans a paragraph break the parser inserted
  — the anchor is returned ungrounded with no context, so the card degrades to
  the quote alone rather than inventing surroundings.
  """
  normalized_anchor = normalize(anchor)
  if normalized_anchor:
    for section in parsed.sections:
      full_section = normalize(section.text)
      for paragraph in _paragraphs(section.text):
        start = paragraph.find(normalized_anchor)
        if start != -1:
          return _build(
            paragraph,
            start,
            len(normalized_anchor),
            ParsedSectionSpan(heading=section.heading, text=full_section),
            budget,
          )
  return ExpandedEvidence(anchor=normalized_anchor or anchor.strip(), grounded=False)


class EvidenceExpander:
  """Caches each Paper's parsed text so a page expands many passages cheaply."""

  def __init__(self, corpus_root: Path | None) -> None:
    self._corpus_root = corpus_root
    self._cache: dict[str, ParsedPaperRead | None] = {}

  def context_for(self, citation_key: str, passage: str) -> ExpandedEvidence | None:
    """Expand a passage against its Paper, or None when no parsed text exists."""
    if self._corpus_root is None:
      return None
    parsed = self._parsed(citation_key)
    if parsed is None:
      return None
    return expand_evidence(passage, parsed)

  def _parsed(self, citation_key: str) -> ParsedPaperRead | None:
    """Read (and cache) a Paper's parsed sections, or None when absent."""
    if citation_key not in self._cache:
      root = self._corpus_root
      assert root is not None
      self._cache[citation_key] = (
        read_parsed_paper(root, citation_key)
        if has_parsed_paper(root, citation_key)
        else None
      )
    return self._cache[citation_key]
