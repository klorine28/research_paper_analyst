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
PAGE_LIMITATIONS = "Unanswered Limitations"
PAGE_RETRIEVAL = "Retrieval Gaps"
PAGE_EXPLAINER = "Paper Explainer"
PAGE_COMPARISON = "Paper Comparison"

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

# Unanswered Limitations page. Each group gathers limitation and future-work
# statements that mean the same thing across Papers; a group is "unanswered"
# when no later Paper in the Corpus addressed it. Every group, addressed or not,
# is shown so the grouping and follow-up decisions stay inspectable.
LIMITATIONS_HEADING = "Unanswered Limitations"
LIMITATIONS_INTRO = (
  "Limitation and future-work statements grouped by meaning across Papers \u2014 "
  "a **signal for human judgment, not a verdict**. A group is *unanswered* when "
  "no later Paper in this Corpus addressed it; it may still be addressed outside "
  "the Corpus, or in a passage the extraction did not capture."
)
NO_CANDIDATE_GAPS_ARTIFACT_LIMITATIONS = (
  "No limitation groups found for this Corpus. Run the pipeline through "
  "`detect` against this corpus directory first."
)
NO_LIMITATION_GROUPS = (
  "The detect stage grouped no limitation or future-work statements in this "
  "Corpus. That is a result about this Corpus, not a claim that its Papers "
  "state no limitations."
)
LIMITATIONS_SUMMARY = (
  "{total} limitation groups over {extracted} Papers: {unanswered} unanswered, "
  "{addressed} addressed by a later Paper."
)
LIMITATION_STATUS_UNANSWERED = "Unanswered"
LIMITATION_STATUS_ADDRESSED = "Addressed by a later Paper"
LIMITATION_SOURCE_LABEL = "Stated by {count} Paper(s): {papers}."
LIMITATION_FOLLOW_UP_LABEL = (
  "{follow_up_count} of {later_count} later Paper(s) in the Corpus addressed it."
)
LIMITATION_STATEMENTS_HEADING = "Source statements"
LIMITATION_FOLLOW_UPS_HEADING = "Follow-ups in later Papers"
LIMITATION_SOURCE_PASSAGE = "{citation_key} \u2014 {section}"
LIMITATION_FOLLOW_UP_PASSAGE = "{citation_key} \u2014 {section}"

# Retrieval Gaps page. This is the one Gap Type that looks *outside* the Corpus
# (CONTEXT.md > Gap Type): out-of-corpus candidate papers found by citation
# overlap. They carry no in-Corpus Evidence, so the page is kept clearly
# separate, verbally and visually, from the evidence-linked gap cards
# (CODING_STANDARDS.md > Research integrity).
RETRIEVAL_HEADING = "Retrieval Gaps (out-of-corpus)"
RETRIEVAL_SEPARATION_NOTICE = (
  "\u26a0\ufe0f These are **not evidence-linked gaps**. They are out-of-corpus "
  "candidates for improving the search \u2014 papers the wider literature cites "
  "alongside this Corpus but that the Corpus itself does not include. Unlike the "
  "gap cards, they carry no in-Corpus Evidence and are candidates for expanding "
  "the Corpus, not gaps the tool has confirmed."
)
NO_RETRIEVAL_GAPS_ARTIFACT = (
  "No out-of-corpus candidates found for this Corpus. Run the pipeline through "
  "`retrieve` against this corpus directory first."
)
NO_RETRIEVAL_CANDIDATES = (
  "The retrieve stage found no out-of-corpus candidates for this Corpus. That "
  "is a result about this Corpus, not a claim that none exist."
)
RETRIEVAL_SUMMARY = (
  "{total} out-of-corpus candidates, ranked by citation overlap, from the "
  "{coupled} of {corpus} Papers that carry a resolved citation list. Each "
  "appears in at least {min_overlap} Corpus Papers' references."
)
RETRIEVAL_SOURCE_LABEL = "Ranking read from: {source}."
RETRIEVAL_OVERLAP_LABEL = "Cited by {overlap} Corpus Paper(s): {papers}."
RETRIEVAL_META_LABEL = "{venue}{year}{cited_by}"
RETRIEVAL_YEAR_PART = " \u00b7 {year}"
RETRIEVAL_CITED_BY_PART = " \u00b7 cited by {count} works"
RETRIEVAL_AUTHORS_LABEL = "{authors}"
RETRIEVAL_DOI_LABEL = "DOI: {doi}"

# Paper Explainer page. Each Paper's experiment explained in two registers,
# grounded in the Paper's own text (see `explain`).
EXPLAINER_HEADING = "Paper Explainer"
EXPLAINER_INTRO = (
  "Each Paper's experiment explained twice \u2014 once in the field's own "
  "language, once in plain language \u2014 grounded only in that Paper's text."
)
EXPLAINER_NO_PAPERS = (
  "This Corpus has no Papers to explain. Run the pipeline (`ingest`) against a "
  "corpus directory first."
)
EXPLAINER_PAPER_PICKER_LABEL = "Paper"
EXPLAINER_SUMMARY = "{explained} of {total} Papers have an explanation."
NO_EXPLANATION_FOR_PAPER = (
  "No explanation for this Paper yet. Run the pipeline through `explain` against "
  "this corpus directory first."
)
EXPLAINER_METADATA_HEADING = "Paper"
EXPLAINER_METADATA_LABEL = "{title}"
EXPLAINER_METADATA_VENUE = "{journal}{year}"
EXPLAINER_METADATA_YEAR_PART = " \u00b7 {year}"
EXPLAINER_DOI_LABEL = "DOI: {doi}"
EXPLAINER_DOMAIN_HEADING = "In domain language"
EXPLAINER_LAY_HEADING = "In plain language"

# Paper Comparison page. The researcher picks 2-15 Papers and sees them side by
# side, field by field, with agreements and blind spots grounded in the shared
# Normalized Facts categories (docs/BRIEF.md, decision 8).
COMPARISON_HEADING = "Paper Comparison"
COMPARISON_INTRO = (
  "Pick 2 to 15 Papers and compare them field by field. Agreements and blind "
  "spots are grounded in the shared normalized categories, so two Papers only "
  "agree when the pipeline placed them on the same category."
)
COMPARISON_NO_ARTIFACTS = (
  "This Corpus has not been extracted and aggregated yet. Run the pipeline "
  "through `extract` and `aggregate` against this corpus directory first."
)
COMPARISON_PICKER_LABEL = "Papers to compare (2\u201315)"
COMPARISON_TOO_FEW = "Pick at least 2 Papers to see a comparison."
COMPARISON_CHUNK_NOTICE = (
  "{count} Papers selected. Papers are split into {sets} sets of at most "
  "5, each set summarized, and the summaries compared."
)
COMPARISON_FIELDS_HEADING = "Field by field"
COMPARISON_NO_STATEMENTS = "\u2014"
COMPARISON_AXES_HEADING = "Agreements and differences"
COMPARISON_AGREEMENTS_LABEL = "All agree on:"
COMPARISON_DIFFERENCES_LABEL = "Differ on:"
COMPARISON_DIFFERENCE_ITEM = "{label} (only {keys})"
COMPARISON_NO_AXIS_SIGNAL = (
  "No shared or differing normalized categories for this selection."
)
COMPARISON_BLIND_SPOTS_HEADING = (
  "Mini coverage matrix: what none of the selection covers"
)
COMPARISON_BLIND_SPOTS_DENOMINATOR = (
  "Categories this Corpus of {total} Papers studies that none of the selected "
  "Papers cover."
)
COMPARISON_NO_BLIND_SPOTS = (
  "The selection covers every category this Corpus studies on these axes."
)
COMPARISON_BLIND_SPOT_AXIS = "{axis}: {labels}"
