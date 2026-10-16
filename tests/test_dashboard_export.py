"""
Behaviour of the write-up export (issue #82).

The export turns a generated narrative summary into three files a researcher can
take to a thesis or grant: the narrative as Markdown, a BibTeX file of the Corpus
Papers it cites, and the scope-and-limits statement. These tests drive the public
`build_write_up` seam with plain read models, no Streamlit and no network.
"""

from datetime import datetime, timezone
from pathlib import Path

import pytest

from research_gap_dashboard.analytics import (
  CorpusPaper,
  GapFact,
  GroundedNarrative,
  GroundingContext,
)
from research_gap_dashboard.dashboard.artifacts import (
  ManifestArtifact,
  PaperRecord,
  UnmatchedEntryRecord,
)
from research_gap_dashboard.dashboard.corpus_scope import CorpusScope
from research_gap_dashboard.dashboard.entry_log import (
  EntryLog,
  EntryRoute,
  LogEntry,
  UnavailablePaper,
  build_entry_log_view,
)
from research_gap_dashboard.dashboard.export import build_write_up
from research_gap_dashboard.dashboard.gaps import NarrativeScope


def _manifest() -> ManifestArtifact:
  return ManifestArtifact(
    corpus_root=Path("corpus"),
    papers=[
      PaperRecord(
        citation_key="smith2019",
        doi="10.1000/abc",
        title="Takotsubo & beta-blockers",
        year=2019,
        journal="Heart",
        authors=["Smith, Jane", "Doe, John"],
      ),
      PaperRecord(
        citation_key="lee2021",
        title="A thesis on 50% recovery",
        year=2021,
        authors=["Lee, Ann"],
      ),
      PaperRecord(citation_key="kim2015", title="Uncited paper", year=2015),
    ],
    unmatched_entries=[UnmatchedEntryRecord(citation_key="lost2020")],
    orphan_pdfs=[Path("papers/orphan.pdf")],
  )


def _coverage() -> NarrativeScope:
  context = GroundingContext(
    papers=[CorpusPaper(citation_key="smith2019"), CorpusPaper(citation_key="lee2021")],
    gaps=[GapFact(gap_type="Coverage Gap", title="No RCTs on Takotsubo")],
  )
  return NarrativeScope(
    context=context,
    accepted_count=1,
    rejected_count=2,
    undecided_count=3,
    paper_count=3,
  )


def _scope() -> CorpusScope:
  return CorpusScope(
    question="What is missing on Takotsubo treatment?",
    field="Cardiology",
    searched="PubMed, 'takotsubo' 2010-2024",
    recorded_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
  )


def _narrative(citations: list[str] | None = None) -> GroundedNarrative:
  return GroundedNarrative(
    paragraphs=["No trial randomised beta-blockers (smith2019).", "Second para."],
    citations=citations if citations is not None else ["smith2019", "lee2021"],
    dropped_citations=[],
  )


def _write_up(narrative: GroundedNarrative | None = None, scope=None):
  return build_write_up(
    corpus_name="takotsubo",
    narrative=narrative or _narrative(),
    coverage=_coverage(),
    manifest=_manifest(),
    scope=scope if scope is not None else _scope(),
  )


def test_bibtex_holds_only_the_cited_corpus_papers():
  """The BibTeX lists the Papers the narrative cites, and nothing else."""
  bibtex = _write_up().bibtex

  assert "@article{smith2019," in bibtex
  assert "@misc{lee2021," in bibtex
  assert "kim2015" not in bibtex
  assert "doi = {10.1000/abc}" in bibtex
  assert "author = {Smith, Jane and Doe, John}" in bibtex
  assert r"Takotsubo \& beta-blockers" in bibtex
  assert r"50\% recovery" in bibtex


def test_a_citation_outside_the_corpus_never_reaches_the_bibtex():
  """A key that does not resolve to a Corpus Paper is left out, not invented."""
  write_up = _write_up(_narrative(["smith2019", "ghost2022"]))

  assert "ghost2022" not in write_up.bibtex
  assert write_up.cited_keys == ["smith2019"]


def test_scope_statement_states_the_denominator_and_what_was_searched():
  """The scope statement says what was searched and what the write-up covers."""
  statement = _write_up().scope_statement

  assert "PubMed, 'takotsubo' 2010-2024" in statement
  assert "What is missing on Takotsubo treatment?" in statement
  assert "Cardiology" in statement
  assert "1 accepted" in statement
  assert "3 Papers" in statement
  assert "2015\u20132021" in statement
  assert "2 rejected" in statement
  assert "3 undecided" in statement
  assert "at High, Medium, Low confidence" in statement
  assert "1 unmatched" in statement
  assert "1 PDF" in statement
  assert "candidates for human judgment" in statement


def test_scope_statement_says_when_no_scope_was_recorded():
  """A Corpus with no scope record says so instead of omitting what was searched."""
  write_up = build_write_up(
    corpus_name="takotsubo",
    narrative=_narrative(),
    coverage=_coverage(),
    manifest=_manifest(),
    scope=None,
  )

  assert "not recorded" in write_up.scope_statement


def test_markdown_holds_the_narrative_references_and_scope():
  """The Markdown carries the paragraphs, the cited Papers and the scope."""
  write_up = _write_up()

  assert "No trial randomised beta-blockers (smith2019)." in write_up.markdown
  assert "Second para." in write_up.markdown
  assert "Takotsubo & beta-blockers" in write_up.markdown
  assert "Uncited paper" not in write_up.markdown
  assert write_up.scope_statement in write_up.markdown


def test_markdown_flags_dropped_citations():
  """Citations dropped during grounding are flagged in the exported text too."""
  narrative = _narrative().model_copy(update={"dropped_citations": ["ghost2022"]})

  assert "ghost2022" in _write_up(narrative).markdown


@pytest.mark.parametrize("field", ["markdown", "bibtex", "scope_statement"])
def test_exports_are_reproducible_from_the_same_inputs(field: str):
  """The same narrative, judgments and Corpus give byte-identical exports."""
  assert getattr(_write_up(), field) == getattr(_write_up(), field)
  reordered = _narrative(["lee2021", "smith2019"])
  assert getattr(_write_up(), field) == getattr(_write_up(reordered), field)


def test_scope_statement_discloses_entry_routes_and_unavailable_papers():
  """The exported scope says how the Papers entered and what could not be had."""
  when = datetime(2024, 1, 1, tzinfo=timezone.utc)
  log = EntryLog(
    entries=[
      LogEntry(citation_key="lee2021", route=EntryRoute.SATURATION_HIT, entered_at=when)
    ],
    unavailable=[
      UnavailablePaper(
        doi="10.9/x", title="X", route=EntryRoute.SATURATION_HIT, marked_at=when
      )
    ],
  )
  write_up = build_write_up(
    corpus_name="takotsubo",
    narrative=_narrative(),
    coverage=_coverage(),
    manifest=_manifest(),
    scope=_scope(),
    entry_log=build_entry_log_view(log, _manifest()),
  )

  assert "2 imported \u00b7 1 from saturation checks" in write_up.scope_statement
  assert "1 suggested paper(s) could not be obtained" in write_up.scope_statement
