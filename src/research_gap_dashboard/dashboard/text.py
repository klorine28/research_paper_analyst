"""
Every user-facing string the dashboard shows, in one place.

The language of dashboard text is an open decision (CODING_STANDARDS.md >
Language and vocabulary); keeping the strings together means they can be
translated later without hunting through the layout code.
"""

APP_TITLE = "Research Gap Dashboard"

# The scope-and-limits statement is shown prominently on every corpus so a
# reader never mistakes a Candidate Gap for a verdict (CONTEXT.md > Candidate
# Gap; CODING_STANDARDS.md > Research integrity).
SCOPE_STATEMENT = (
  "These research gaps are **candidates for human judgment, not verdicts**. "
  "The tool surfaces where a Corpus looks thin or silent; deciding whether a "
  "gap is real, and worth pursuing, is the researcher's call. Every claim "
  "here is computed only over the Papers in this Corpus, shown below with the "
  "entries and PDFs it could not place."
)

# Corpus selection and navigation.
CORPUS_PICKER_LABEL = "Corpus"
NO_CORPORA = (
  "No analysed corpora found. Run the pipeline (`ingest`) against a corpus "
  "directory first, then point the dashboard at the folder that holds it."
)
NAV_LABEL = "Page"
PAGE_OVERVIEW = "Corpus Overview"
PAGE_GAP_CARDS = "Gap Cards"
PAGE_COVERAGE = "Coverage & Trends"

# Corpus Overview page.
OVERVIEW_HEADING = "Corpus Overview"
METRIC_PAPER_COUNT = "Papers in the Corpus"
METRIC_VENUE_COUNT = "Venues"
METRIC_YEAR_SPAN = "Year span"
PAPERS_PER_YEAR_HEADING = "Papers per year"
VENUES_HEADING = "Venues"
EXCLUSIONS_HEADING = "What was left out"
NO_EXCLUSIONS = "Every paper-list entry and every PDF was placed in the Corpus."
UNMATCHED_ENTRIES_LABEL = "Paper-list entries with no matching PDF"
ORPHAN_PDFS_LABEL = "PDFs with no matching paper-list entry"
UNKNOWN_YEAR_LABEL = "Unknown"

# Column headers for the small tables the overview renders.
COLUMN_YEAR = "Year"
COLUMN_VENUE = "Venue"
COLUMN_PAPERS = "Papers"
COLUMN_ENTRY = "Entry"
COLUMN_REASON = "Reason"
COLUMN_FILE = "File"

# Gap Cards page. Every card is a Candidate Gap: a signal for human judgment,
# never a verdict (CONTEXT.md > Candidate Gap; CODING_STANDARDS.md > Research
# integrity).
GAP_CARDS_HEADING = "Gap Cards"
NO_CANDIDATE_GAPS_ARTIFACT = (
  "No candidate gaps found for this Corpus. Run the pipeline through `detect` "
  "against this corpus directory first."
)
NO_GAPS_DETECTED = (
  "The detect stage found no Candidate Gaps in this Corpus. That is a result "
  "about this Corpus, not a claim that no gaps exist."
)
GAP_CARDS_INTRO = (
  "Each card below is a **candidate for human judgment, not a verdict**. "
  "Accept or reject each one; your decisions are saved and restored when you "
  "reopen this Corpus."
)
GAP_JUDGMENT_SUMMARY = (
  "{total} candidate gaps: {accepted} accepted, {rejected} rejected, "
  "{undecided} undecided."
)

# Per-card labels.
GAP_CONFIDENCE_LABEL = "Confidence: {confidence}"
GAP_CELL_COUNT_LABEL = "Papers in this cell: {count} of {total}."
GAP_SOURCE_PAPERS_LABEL = "Source Papers: {papers}."
GAP_EVIDENCE_HEADING = "Evidence"
GAP_EVIDENCE_SOURCE = "{citation_key} — {section}"
GAP_STATUS_ACCEPTED = "Accepted"
GAP_STATUS_REJECTED = "Rejected"
GAP_STATUS_UNDECIDED = "Undecided"
GAP_ACCEPT_BUTTON = "Accept"
GAP_REJECT_BUTTON = "Reject"
GAP_CLEAR_BUTTON = "Reset to undecided"

# How each Candidate Gap's kind and confidence read to the researcher; kept here
# so the dashboard never imports the detect stage's labels (ADR 0002).
GAP_TYPE_LABELS: dict[str, str] = {
  "knowledge_gap": "Knowledge Gap",
  "coverage_gap": "Coverage Gap",
  "unanswered_limitation": "Unanswered Limitation",
  "retrieval_gap": "Retrieval Gap",
}
CONFIDENCE_LABELS: dict[str, str] = {
  "low": "Low",
  "medium": "Medium",
  "high": "High",
}
EVIDENCE_ROLE_LABELS: dict[str, str] = {
  "row": "establishes",
  "column": "establishes",
  "cell": "combines",
}
EVIDENCE_TERM_DETAIL = "{role} \u201c{term}\u201d"

# Coverage & Trends page. The Coverage Matrix heatmap and the publication-volume
# Trends chart both read the artifacts the pipeline wrote (ADR 0002). An empty
# cell is a fact about the Corpus ("no Paper combines these"), never a measured
# zero (CODING_STANDARDS.md > Research integrity).
COVERAGE_HEADING = "Coverage & Trends"
COVERAGE_INTRO = (
  "Where the Corpus looks thin or silent \u2014 a **signal for human judgment, "
  "not a verdict**. An empty heatmap cell means no Paper in this Corpus combines "
  "those two categories; it is not a measured value of zero."
)
NO_CANDIDATE_GAPS_ARTIFACT_COVERAGE = (
  "No Coverage Matrices found for this Corpus. Run the pipeline through `detect` "
  "against this corpus directory first."
)
NO_NORMALIZED_FACTS_ARTIFACT = (
  "No normalized facts found for this Corpus. Run the pipeline through "
  "`aggregate` against this corpus directory first."
)

# Coverage Matrix heatmap.
HEATMAP_HEADING = "Coverage Matrix"
HEATMAP_AXIS_LABEL = "Compare Topics against"
HEATMAP_TOPIC_PAIR_OPTION = "Topic (co-occurrence)"
HEATMAP_NO_PAPERS = "no papers"
HEATMAP_NO_PAPERS_COLOR = "#e6e6e6"
HEATMAP_COLORBAR_TITLE = "Papers"
HEATMAP_DENOMINATOR = "Counts are over the {total} Papers in the Corpus."
HEATMAP_NO_MATRICES = (
  "No Coverage Matrix has both rows and columns to show for this Corpus."
)

# Trends chart.
TRENDS_HEADING = "Publication volume per Topic over time"
TRENDS_X_TITLE = "Year"
TRENDS_Y_TITLE = "Papers"
TRENDS_LEGEND_TITLE = "Topic"
TRENDS_STATUS_SUFFIX = {
  "emerging": " (emerging)",
  "abandoned": " (abandoned)",
  "steady": "",
}
TRENDS_DENOMINATOR = (
  "Lines are built from the {with_year} of {total} Papers in the Corpus that "
  "carry a publication year."
)
TRENDS_MISSING_YEAR = (
  "{without_year} Paper(s) carry no publication year and are not on the timeline."
)
TRENDS_EMERGING_LABEL = "Emerging lines (first appear in the recent half): {topics}"
TRENDS_ABANDONED_LABEL = "Abandoned lines (no Paper since the recent half): {topics}"
TRENDS_NO_HIGHLIGHTS = "No Topic reads as clearly emerging or abandoned in this span."
TRENDS_NO_DATA = (
  "No Topic could be placed on a timeline: the Corpus's Papers carry no known "
  "publication years."
)
