"""
Behavior of deterministic Evidence-window expansion around a verbatim anchor.

Integrity rests on the anchor staying verbatim ground truth while the code — not
a model — supplies the surrounding paragraph. These tests pin that the window is
a real slice of the Paper, the anchor is never trimmed, the char budget is
honoured, ungrounded anchors degrade to the quote alone, and intra-paper
pointers are labeled without being resolved.
"""

import json
from pathlib import Path

from research_gap_dashboard.dashboard.evidence_context import (
  WINDOW_CHAR_BUDGET,
  EvidenceExpander,
  expand_evidence,
)
from research_gap_dashboard.dashboard.grounding import ParsedPaperRead


def _parsed(*sections: tuple[str, str]) -> ParsedPaperRead:
  """Build a parsed paper from (heading, text) pairs, all labeled 'other'."""
  return ParsedPaperRead.model_validate(
    {"sections": [{"heading": h, "text": t} for h, t in sections]}
  )


def test_anchor_widens_to_its_containing_paragraph():
  """The window carries the whole paragraph, with the anchor as its own slice."""
  paragraph = (
    "Takotsubo cardiomyopathy is reversible. No randomized trial has tested "
    "beta-blockers in this population. Future work should address this."
  )
  parsed = _parsed(("Discussion", paragraph))

  expanded = expand_evidence(
    "No randomized trial has tested beta-blockers in this population.", parsed
  )

  assert expanded.grounded
  assert (
    expanded.anchor
    == "No randomized trial has tested beta-blockers in this population."
  )
  assert expanded.has_context
  assert expanded.window in paragraph
  assert expanded.before.endswith("reversible. ")
  assert expanded.after.startswith(" Future work")


def test_window_is_capped_but_anchor_is_never_trimmed():
  """A budget smaller than the paragraph clips context yet keeps the anchor whole."""
  anchor = "the core verbatim claim stays intact"
  paragraph = ("padding " * 200) + anchor + (" trailing" * 200)
  parsed = _parsed(("Results", paragraph))

  expanded = expand_evidence(anchor, parsed, budget=120)

  assert expanded.anchor == anchor
  assert len(expanded.window) <= 120 + len("padding")  # clip is word-boundary slack
  assert expanded.truncated_before
  assert expanded.truncated_after


def test_long_anchor_survives_even_past_budget():
  """An anchor longer than the budget still shows in full with no context."""
  anchor = "x" * (WINDOW_CHAR_BUDGET + 50)
  parsed = _parsed(("Methods", f"lead in {anchor} tail"))

  expanded = expand_evidence(anchor, parsed)

  assert expanded.anchor == anchor
  assert not expanded.has_context


def test_ungrounded_anchor_degrades_to_the_quote_alone():
  """An anchor absent from the text yields no invented context."""
  parsed = _parsed(("Intro", "Unrelated sentence about something else."))

  expanded = expand_evidence("a quote that is not present", parsed)

  assert not expanded.grounded
  assert not expanded.has_context
  assert expanded.anchor == "a quote that is not present"


def test_expansion_matches_across_line_wrapping():
  """A quote that the PDF wrapped across lines still anchors to its paragraph."""
  paragraph = "Mortality was\nhigh among\nolder patients in the cohort."
  parsed = _parsed(("Results", paragraph))

  expanded = expand_evidence("Mortality was high among older patients", parsed)

  assert expanded.grounded
  assert expanded.anchor == "Mortality was high among older patients"


def test_pointers_are_labeled_but_not_resolved():
  """Intra-paper pointers in the window surface as distinct, ordered labels."""
  paragraph = (
    "As shown in Table 2, the effect held. See Section 4.2 and Figure 3 for "
    "details. Table 2 is repeated here."
  )
  parsed = _parsed(("Discussion", paragraph))

  expanded = expand_evidence("the effect held", parsed)

  labels = [pointer.label for pointer in expanded.pointers]
  assert labels == ["Table 2", "Section 4.2", "Figure 3"]


def test_full_section_backs_the_show_more_expander():
  """The full section is carried whole, distinct from the capped window."""
  paragraph_one = "First paragraph with the anchor sentence inside it here."
  paragraph_two = "Second paragraph that the window never reaches at all."
  parsed = _parsed(("Discussion", f"{paragraph_one}\n\n{paragraph_two}"))

  expanded = expand_evidence("the anchor sentence", parsed)

  assert "Second paragraph" in expanded.full_section
  assert "Second paragraph" not in expanded.window
  assert expanded.full_section_adds_more
  assert expanded.section_heading == "Discussion"


def test_expander_returns_none_without_a_corpus_root():
  """The caching expander is a no-op when it has no corpus to read from."""
  expander = EvidenceExpander(None)

  assert expander.context_for("paper-a", "any passage") is None


def test_expander_reads_parsed_text_from_the_corpus(tmp_path: Path):
  """Given a corpus root, the expander grounds a passage against parsed text."""
  paper_data = tmp_path / "paper-data"
  paper_data.mkdir()
  (paper_data / "paper-a.parsed.json").write_text(
    json.dumps(
      {
        "sections": [
          {
            "heading": "Discussion",
            "text": "Context before. The anchor sentence sits here. Context after.",
          }
        ]
      }
    ),
    encoding="utf-8",
  )
  expander = EvidenceExpander(tmp_path)

  context = expander.context_for("paper-a", "The anchor sentence sits here.")

  assert context is not None
  assert context.grounded
  assert context.has_context
  # A second read for the same Paper is served from the cache, not the disk.
  assert expander.context_for("paper-a", "Context after.") is not None


def test_expander_returns_none_for_a_paper_without_parsed_text(tmp_path: Path):
  """A Paper with no parsed file on disk yields no context, not an error."""
  expander = EvidenceExpander(tmp_path)

  assert expander.context_for("missing", "any passage") is None
