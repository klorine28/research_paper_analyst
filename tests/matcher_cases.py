"""
Quote-verification cases shared by the pipeline and dashboard matcher tests.

The extract stage and the dashboard each own a verbatim-Evidence matcher (ADR
0002 forbids the dashboard importing a stage), and ADR 0004 requires both to
normalise identically. Running both against this one list keeps them honest:
a case that verifies in one must verify in the other.
"""

from typing import NamedTuple


class MatcherCase(NamedTuple):
  """A Paper's parsed text, a quoted passage, and whether the quote must verify."""

  id: str
  paper_text: str
  passage: str
  verifies: bool


MATCHER_CASES: list[MatcherCase] = [
  MatcherCase(
    "verbatim",
    "We studied outcomes in older patients.",
    "outcomes in older patients",
    True,
  ),
  MatcherCase(
    "whitespace-wrap",
    "We studied outcomes\n  in older   patients.",
    "outcomes in older patients",
    True,
  ),
  MatcherCase(
    "line-break-hyphen",
    "Treatment reduced in-\nhospital mortality.",
    "reduced in-hospital mortality",
    True,
  ),
  MatcherCase(
    "line-break-hyphen-already-spaced",
    "Treatment reduced in- hospital mortality.",
    "reduced in-hospital mortality",
    True,
  ),
  MatcherCase(
    "line-break-hyphen-kept-in-quote",
    "Treatment reduced in-hospital mortality.",
    "reduced in-\nhospital mortality",
    True,
  ),
  MatcherCase(
    "line-break-hyphen-is-not-dropped",
    "Treatment reduced in-\nhospital mortality.",
    "reduced inhospital mortality",
    False,
  ),
  MatcherCase(
    "misquote-across-hyphen-break",
    "Treatment did not reduce in-\nhospital mortality.",
    "Treatment reduced in-hospital mortality",
    False,
  ),
  MatcherCase(
    "typo",
    "prolongation by bradycardial pacing in rabbits",
    "prolongation by brachycardial pacing",
    False,
  ),
  MatcherCase(
    "table-pipes",
    "Group A | 10 mice | Male | Infarct size reduced",
    "Group A 10 mice Male",
    True,
  ),
  MatcherCase(
    "compatibility-glyph",
    "The \ufb01nal cohort was large.",
    "The final cohort was large.",
    True,
  ),
]
