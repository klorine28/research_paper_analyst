"""
Exporting the write-up: the narrative as Markdown, BibTeX, and the scope statement.

A researcher takes the narrative summary (issue #81) to a thesis or grant, so
it leaves the dashboard as three files (issue #82): the narrative as Markdown,
a BibTeX file of the Corpus Papers it cites, and the scope-and-limits statement
with its denominator.

Only Corpus Papers ever reach the BibTeX: each cited key is resolved against
the manifest, and one that does not resolve is left out (CODING_STANDARDS.md >
Never invent citations). The output is a pure function of its inputs, with
entries in a fixed order and no export timestamp, so the same judgments and
narrative export byte-identical files; the narrative itself is reproducible
because its LLM call is cached by its inputs (ADR 0001).

Plain functions with no Streamlit dependency, importing no pipeline stage
(ADR 0002).
"""

from pydantic import BaseModel

from research_gap_dashboard.analytics import GroundedNarrative
from research_gap_dashboard.dashboard import text
from research_gap_dashboard.dashboard.artifacts import ManifestArtifact, PaperRecord
from research_gap_dashboard.dashboard.corpus_scope import CorpusScope
from research_gap_dashboard.dashboard.entry_log import EntryLogView
from research_gap_dashboard.dashboard.gaps import NarrativeScope

# Characters LaTeX treats as special inside a BibTeX field value. Braces are left
# alone: they are BibTeX's own grouping and a title may use them on purpose.
_BIBTEX_ESCAPES = {"&": r"\&", "%": r"\%", "#": r"\#", "$": r"\$", "_": r"\_"}


class WriteUp(BaseModel):
  """The three exported files of one narrative, and the Corpus Papers it cites."""

  markdown: str
  bibtex: str
  scope_statement: str
  cited_keys: list[str]


def build_write_up(
  *,
  corpus_name: str,
  narrative: GroundedNarrative,
  coverage: NarrativeScope,
  manifest: ManifestArtifact,
  scope: CorpusScope | None,
  entry_log: EntryLogView | None = None,
) -> WriteUp:
  """
  Build the Markdown, BibTeX and scope statement for one generated narrative.

  `coverage` is the accepted-gaps scope the narrative was generated over, so the
  scope statement states the same denominator the page showed. `entry_log`,
  when given, adds how the Papers entered the Corpus and the suggested papers
  that could not be obtained (ADR 0010 §4).
  """
  papers = {paper.citation_key: paper for paper in manifest.papers}
  cited = sorted({key for key in narrative.citations if key in papers})
  cited_papers = [papers[key] for key in cited]
  scope_statement = _scope_statement(coverage, manifest, scope, entry_log)
  return WriteUp(
    markdown=_markdown(corpus_name, narrative, cited_papers, scope_statement),
    bibtex="\n".join(_bibtex_entry(paper) for paper in cited_papers),
    scope_statement=scope_statement,
    cited_keys=cited,
  )


def _scope_statement(
  coverage: NarrativeScope,
  manifest: ManifestArtifact,
  scope: CorpusScope | None,
  entry_log: EntryLogView | None,
) -> str:
  """State what was searched, the denominator, and what the Corpus left out."""
  if scope is None:
    searched = text.EXPORT_SCOPE_NOT_RECORDED
  else:
    searched = text.EXPORT_SCOPE_RECORDED.format(
      question=scope.question, field=scope.field, searched=scope.searched
    )
  denominator = text.EXPORT_SCOPE_DENOMINATOR.format(
    accepted=coverage.accepted_count,
    papers=coverage.paper_count,
    years=_year_range(manifest),
    rejected=coverage.rejected_count,
    undecided=coverage.undecided_count,
  )
  limits = text.EXPORT_SCOPE_LIMITS.format(
    unmatched=len(manifest.unmatched_entries), orphans=len(manifest.orphan_pdfs)
  )
  parts = [searched, denominator, limits]
  if entry_log is not None:
    if entry_log.papers:
      parts.append(text.EXPORT_SCOPE_ENTRY_LOG.format(summary=entry_log.summary))
    if entry_log.unavailable:
      parts.append(
        text.ENTRY_LOG_UNAVAILABLE_SCOPE.format(count=len(entry_log.unavailable))
      )
  return "\n\n".join([*parts, text.EXPORT_SCOPE_CAVEAT])


def _year_range(manifest: ManifestArtifact) -> str:
  """Return the span of publication years in the Corpus, as a reader writes it."""
  years = [paper.year for paper in manifest.papers if paper.year is not None]
  if not years:
    return text.EXPORT_SCOPE_YEARS_UNKNOWN
  first, last = min(years), max(years)
  return str(first) if first == last else f"{first}\u2013{last}"


def _markdown(
  corpus_name: str,
  narrative: GroundedNarrative,
  cited_papers: list[PaperRecord],
  scope_statement: str,
) -> str:
  """Lay out the narrative, its cited Corpus Papers, and the scope as Markdown."""
  lines = [f"# {text.EXPORT_TITLE.format(corpus=corpus_name)}", ""]
  for paragraph in narrative.paragraphs:
    lines += [paragraph, ""]
  if narrative.dropped_citations:
    dropped = ", ".join(sorted(set(narrative.dropped_citations)))
    note = text.EXPORT_DROPPED_NOTE.format(
      count=len(set(narrative.dropped_citations)), papers=dropped
    )
    lines += [f"_{note}_", ""]
  lines += [f"## {text.EXPORT_REFERENCES_HEADING}", ""]
  if cited_papers:
    lines += [f"- {_reference(paper)}" for paper in cited_papers]
  else:
    lines.append(text.EXPORT_NO_REFERENCES)
  lines += ["", f"## {text.EXPORT_SCOPE_HEADING}", "", scope_statement, ""]
  return "\n".join(lines)


def _reference(paper: PaperRecord) -> str:
  """One cited Paper as a reference-list line, keyed so it matches the BibTeX."""
  parts = [f"[{paper.citation_key}]"]
  if paper.authors:
    parts.append("; ".join(paper.authors))
  if paper.year is not None:
    parts.append(f"({paper.year})")
  if paper.title:
    parts.append(f"{paper.title}.")
  if paper.journal:
    parts.append(f"_{paper.journal}_.")
  if paper.doi:
    parts.append(f"https://doi.org/{paper.doi}")
  return " ".join(parts)


def _bibtex_entry(paper: PaperRecord) -> str:
  """
  One Corpus Paper as a BibTeX entry built from its manifest record.

  A Paper with a journal is an `@article`; one without (a preprint, thesis or
  grey literature, CONTEXT.md > Paper) is a `@misc`, which needs no venue.
  """
  entry_type = "article" if paper.journal else "misc"
  fields = [
    ("author", " and ".join(paper.authors)),
    ("title", paper.title),
    ("journal", paper.journal),
    ("year", str(paper.year) if paper.year is not None else ""),
    ("doi", paper.doi),
  ]
  body = [
    f"  {name} = {{{_escape(value) if name != 'doi' else value}}},"
    for name, value in fields
    if value
  ]
  return "\n".join([f"@{entry_type}{{{paper.citation_key},", *body, "}", ""])


def _escape(value: str) -> str:
  """Escape the LaTeX specials a BibTeX field would otherwise break on."""
  return "".join(_BIBTEX_ESCAPES.get(char, char) for char in value)
