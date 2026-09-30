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
PAGE_ANALYTICS = "Conversational Analytics"
PAGE_VERIFY = "Verify Extractions"
PAGE_ATTENTION = "Needs Attention"

# Needs-Attention page and the corpus-wide incompleteness banner (issue #46
# Step 3). The banner states the denominator research integrity demands so a
# partial Corpus is never mistaken for the whole one; the queue lists the Papers
# that fell out and lets a researcher paste a bad Paper's correct full text.
ATTENTION_HEADING = "Needs Manual Attention"
ATTENTION_ALL_CLEAR = (
  "Every Paper reached the artifact with fully verified facts. Nothing needs "
  "manual attention."
)
ATTENTION_NO_PIPELINE = (
  "Run `parse` and `extract` on this Corpus first; there is nothing to report yet."
)
ATTENTION_UNPARSED_HEADING = "PDFs no parser could read"
ATTENTION_REJECTED_HEADING = "Papers whose extraction was rejected"
ATTENTION_DROPPED_HEADING = "Papers with dropped facts"
ATTENTION_CORRECTION_LABEL = "Paste this Paper's correct full text"
ATTENTION_CORRECTION_HELP = (
  "Saved to a review overlay under judgments/. Run `apply-parse-corrections` "
  "then `extract` to fold it into the pipeline."
)
ATTENTION_SAVE_CORRECTION = "Save correction"
ATTENTION_CORRECTION_SAVED = "Saved. Run `apply-parse-corrections` then `extract`."
ATTENTION_CORRECTION_EMPTY = "Nothing to save: paste the Paper's text first."


def incompleteness_banner(
  missing: int, dropped_papers: int, total: int, reached: int
) -> str:
  """Compose the loud banner that states how incomplete a Corpus is."""
  parts = [f"{reached}/{total} Papers reached the artifact with real facts."]
  if missing:
    parts.append(f"{missing} produced nothing and are missing from every view.")
  if dropped_papers:
    parts.append(f"{dropped_papers} are missing some facts (dropped as unverifiable).")
  parts.append("See the Needs Attention page.")
  return " ".join(parts)


# Verify Extractions page. The reviewer checks a Paper's Extraction against its
# text and leaves per-field verdicts; verdicts are annotation-only and never
# change the pipeline's Extraction (ADR 0003).
VERIFY_HEADING = "Verify Extractions"
VERIFY_INTRO = (
  "Check each extracted fact against the Paper's own text. Your verdicts are "
  "saved as a review overlay and never change the pipeline's Extraction; "
  "promoting them to a Gold Extraction fixture is a separate command-line step."
)
VERIFY_NO_EXTRACTIONS = "No Extractions to review. Run `extract` on this Corpus first."
VERIFY_NO_PARSED_TEXT = (
  "This Paper has no parsed text, so edits and added facts cannot be grounded. "
  "Run `parse` on this Corpus first."
)
VERIFY_PAPER_PICKER_LABEL = "Paper to verify"
VERIFY_SUMMARY = (
  "{reviewed} of {extracted} extracted facts reviewed; {added} facts added."
)
VERIFY_PDF_LINK = "Open the PDF"
VERIFY_NO_FACTS = "No facts extracted for this field."
VERIFY_ADDED_BADGE = "added by reviewer"
VERIFY_SECTION_LABEL = "Section: {section}"
VERIFY_VERDICT_APPROVED = "Approved"
VERIFY_VERDICT_EDITED = "Edited"
VERIFY_VERDICT_FLAGGED = "Flagged wrong"
VERIFY_VERDICT_REMOVED = "Removed (hallucinated)"
VERIFY_VERDICT_UNREVIEWED = "Unreviewed"
VERIFY_APPROVE_BUTTON = "Approve"
VERIFY_FLAG_BUTTON = "Flag wrong"
VERIFY_REMOVE_BUTTON = "Remove"
VERIFY_EDIT_BUTTON = "Save edit"
VERIFY_CLEAR_BUTTON = "Clear verdict"
VERIFY_DELETE_ADDED_BUTTON = "Delete added fact"
VERIFY_EDIT_STATEMENT_LABEL = "Fact text"
VERIFY_EDIT_PASSAGE_LABEL = "Evidence passage (verbatim from the Paper)"
VERIFY_EDIT_SECTION_LABEL = "Section"
VERIFY_ADD_FACT_HEADING = "Add a missing fact"
VERIFY_ADD_STATEMENT_LABEL = "Fact text"
VERIFY_ADD_PASSAGE_LABEL = "Evidence passage (verbatim from the Paper)"
VERIFY_ADD_SECTION_LABEL = "Section"
VERIFY_ADD_BUTTON = "Add fact"
VERIFY_UNGROUNDED_ERROR = (
  "That Evidence is not a verbatim quote from the Paper's text, so it was "
  "refused. Copy the passage exactly as it appears in the Paper."
)

# Corpus Overview page.
OVERVIEW_HEADING = "Corpus Overview"
METRIC_PAPER_COUNT = "Papers in the Corpus"
METRIC_VENUE_COUNT = "Venues"
METRIC_YEAR_SPAN = "Year span"
PAPERS_PER_YEAR_HEADING = "Papers per year"
PAPERS_PER_YEAR_QUESTION = "When were the Papers in this Corpus published?"
VENUES_HEADING = "Venues"
VENUES_QUESTION = "Where were the Papers in this Corpus published?"

# The parse/extract completeness gauge: the honesty meter that mirrors the
# corpus-wide incompleteness banner (issue #45; issue #46). Every chart below it
# is computed only over the Papers that reached the artifact, so this states the
# denominator the rest of the page inherits (CODING_STANDARDS.md > Research
# integrity: show the denominator).
COMPLETENESS_HEADING = "Parse & extract completeness"
COMPLETENESS_QUESTION = (
  "How much of the Corpus actually reached the charts below? Everything on this "
  "page is computed only over the Papers that reached the artifact."
)
COMPLETENESS_METRIC = "Papers with real facts"
COMPLETENESS_PROGRESS = "{reached} of {total} Papers reached the artifact ({percent}%)"
COMPLETENESS_COMPLETE = "Every Paper reached the artifact with fully verified facts."


def completeness_caveat(missing: int, dropped_papers: int) -> str:
  """State how many Papers are absent from or thin in the charts below."""
  parts: list[str] = []
  if missing:
    parts.append(f"{missing} produced nothing and are missing from every chart below.")
  if dropped_papers:
    parts.append(f"{dropped_papers} are missing some facts (dropped as unverifiable).")
  parts.append("See the Needs Attention page.")
  return " ".join(parts)


COMPLETENESS_NO_DATA = (
  "Run `parse` and `extract` on this Corpus to measure completeness."
)

# Per-axis frequency bars: how often each Topic, Method, Population, and Dataset
# appears across the Papers placed on that axis (issue #45).
AXIS_FREQUENCIES_HEADING = "What the Corpus studies"
AXIS_FREQUENCIES_QUESTION = (
  "How often does each Topic, Method, Population, and Dataset appear across the Corpus?"
)
AXIS_FREQUENCIES_NO_DATA = (
  "Run `aggregate` on this Corpus to place Papers on the Topic, Method, "
  "Population, and Dataset axes."
)
AXIS_DENOMINATOR = "{placed} of {total} Papers placed on {axis}."
COLUMN_CATEGORY = "Category"

# Facts-per-paper extraction density (issue #45, optional expander).
FACT_DENSITY_HEADING = "Facts-per-paper extraction density"
FACT_DENSITY_QUESTION = "How many verified facts did each Paper contribute?"
FACT_DENSITY_SUMMARY = (
  "{facts} verified facts across {papers} extracted Papers (mean {mean:.1f})."
)
FACT_DENSITY_NO_DATA = (
  "Run `extract` on this Corpus to measure how many facts each Paper contributed."
)
COLUMN_PAPER = "Paper"
COLUMN_FACTS = "Facts"

# The Corpus Overview chart gallery: a grid of clickable tiles, each a chart in
# its own shade of blue, that expands to the chart and a "how to read" explainer
# (issue #45 follow-up). Every explainer follows the same four-line shape so a
# reader learns the pattern once: what it answers, how to read it, what a gap
# looks like here, and the denominator caveat (CODING_STANDARDS.md > Research
# integrity).
OVERVIEW_GALLERY_HEADING = "Corpus charts"
OVERVIEW_GALLERY_HINT = (
  "Each card previews one chart in its own shade of blue. Expand a card for the "
  "full-size chart and a short guide on how to read it."
)
OVERVIEW_TILE_EXPAND = "Expand \u25be"
OVERVIEW_TILE_COLLAPSE = "Collapse \u25b4"
OVERVIEW_TILE_PREVIEW_EMPTY = "No data yet \u2014 expand for details."

# One-line headline stats shown on each preview tile, so a collapsed card still
# carries a number, not just a shape.
PREVIEW_PAPERS_YEAR = "{count} papers \u00b7 {span}"
PREVIEW_VENUES = "{count} venues \u00b7 top: {top} ({n})"
PREVIEW_AXES = "{count} {axis} categories \u00b7 top: {top} ({n})"
PREVIEW_FACTS = "{facts} facts \u00b7 mean {mean:.1f}/paper"

PAPERS_PER_YEAR_EXPLAINER = (
  "**What it answers:** When were the Papers in this Corpus published?\n\n"
  "**How to read it:** Each bar is one year; its height is how many Papers "
  "carry that year. Papers with no year are collected under 'Unknown'.\n\n"
  "**What a gap looks like here:** A short or missing bar in recent years can "
  "mean the Corpus thins out there \u2014 not that the field went quiet.\n\n"
  "**Caveat:** Counted over every Paper in this Corpus."
)
VENUES_EXPLAINER = (
  "**What it answers:** Where were the Papers in this Corpus published?\n\n"
  "**How to read it:** Each row is a venue; the count is how many Papers came "
  "from it, most-published first.\n\n"
  "**What a gap looks like here:** A Corpus dominated by one venue may be "
  "narrow; read any coverage claim with that in mind.\n\n"
  "**Caveat:** Counted over every Paper in this Corpus."
)
AXIS_FREQUENCIES_EXPLAINER = (
  "**What it answers:** How often does each Topic, Method, Population, and "
  "Dataset appear across the Corpus?\n\n"
  "**How to read it:** One chart per axis; each bar is a category and its "
  "height is how many distinct Papers were placed on it.\n\n"
  "**What a gap looks like here:** A category with few or no Papers is where "
  "the Corpus is thin \u2014 a candidate Coverage Gap. Missing *pairings* show "
  "up on the Coverage matrix.\n\n"
  "**Caveat:** Counted only over the Papers placed on each axis (the "
  "denominator shown above each chart)."
)
FACT_DENSITY_EXPLAINER = (
  "**What it answers:** How many verified facts did each Paper contribute?\n\n"
  "**How to read it:** One bar per Paper; its height is the number of facts "
  "that passed Evidence verification.\n\n"
  "**What a gap looks like here:** A very short bar means little was extracted "
  "\u2014 the Paper is thin in the analysis, not necessarily thin in reality. "
  "Check the Needs Attention page.\n\n"
  "**Caveat:** Only Papers that produced an Extraction appear here."
)

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
# Evidence-window expansion (issue #47): the anchor quote shown in its paragraph.
EVIDENCE_POINTERS_LABEL = "Points to: {pointers}"
EVIDENCE_SHOW_FULL_SECTION = "Show full section"
# One-click scoped-context chat (issue #48): each card can seed a chat narrowed
# to that gap's source Papers and Evidence, over on the Conversational Analytics
# page. It stays a Candidate Gap, never a verdict.
GAP_DISCUSS_BUTTON = "\U0001f4ac Discuss this gap"
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
HOW_TO_READ_LABEL = "How to read this chart"
HEATMAP_EXPLAINER = (
  "**What it answers:** Which pairs of categories do Papers in this Corpus "
  "study together?\n\n"
  "**How to read it:** Rows and columns are categories. Each cell shows how "
  "many Papers cover *both* its row and its column; the deeper the blue, the "
  "more Papers. A blank cell labelled '" + HEATMAP_NO_PAPERS + "' means no "
  "Paper combines that pair \u2014 it is a fact about the Corpus, not a "
  "measured zero, so it is painted separately from the low end of the scale."
  "\n\n"
  "**What a gap looks like here:** An empty cell between two otherwise "
  "well-studied categories is a candidate Knowledge Gap.\n\n"
  "**Caveat:** Counts are over the Papers placed on both axes (shown below)."
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
TRENDS_EXPLAINER = (
  "**What it answers:** How has each Topic's publication volume moved over "
  "time?\n\n"
  "**How to read it:** Each line is a Topic; the x-axis is the year and the "
  "y-axis is how many Papers covered it that year. Bold solid lines are "
  "*emerging* Topics (they first appear in the recent half of the span); bold "
  "dashed lines are *abandoned* Topics (no Paper since then).\n\n"
  "**What a gap looks like here:** An abandoned Topic may hold an unanswered "
  "question; an emerging one shows where the field is moving.\n\n"
  "**Caveat:** Built only from Papers with a known year (denominator below)."
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

# Conversational Analytics page. A chat and a narrative summary, both grounded in
# the Corpus and citing only its Papers (CONTEXT.md > Conversational Analytics;
# CODING_STANDARDS.md > Research integrity). History persists under judgments/.
ANALYTICS_HEADING = "Conversational Analytics"
ANALYTICS_INTRO = (
  "Discuss this Corpus's Papers, Candidate Gaps, and data in a chat grounded "
  "only in the Corpus. Answers cite only Papers in the Corpus, and the "
  "conversation persists so it can carry over months."
)
ANALYTICS_NO_MANIFEST = (
  "This Corpus has not been ingested yet. Run the pipeline (`ingest`) against "
  "a corpus directory first."
)
ANALYTICS_NO_KEY = (
  "Conversational Analytics needs an Anthropic API key to answer. Set "
  "ANTHROPIC_API_KEY in your environment (see .env.example), then reload."
)
ANALYTICS_CHAT_HEADING = "Chat"
ANALYTICS_CHAT_INPUT_LABEL = "Ask about the Papers, gaps, or data"
ANALYTICS_CLEAR_CHAT_BUTTON = "Clear conversation"
ANALYTICS_THINKING = "Grounding an answer in the Corpus\u2026"
ANALYTICS_CITATIONS_LABEL = "Grounded in: {papers}"
ANALYTICS_NO_CITATIONS = "This answer cites no Corpus Paper."
ANALYTICS_DROPPED_LABEL = (
  "\u26a0\ufe0f Dropped {count} citation(s) that do not resolve to a Corpus "
  "Paper: {papers}."
)
ANALYTICS_SUMMARY_HEADING = "Narrative summary"
ANALYTICS_SUMMARY_INTRO = (
  "A few paragraphs for a 'gaps in the literature' section, drawn from this "
  "Corpus's Candidate Gaps and citing only its Papers."
)
ANALYTICS_SUMMARY_NEEDS_GAPS = (
  "No Candidate Gaps found for this Corpus yet. Run the pipeline through "
  "`detect` to generate a narrative summary."
)
ANALYTICS_GENERATE_SUMMARY_BUTTON = "Generate narrative summary"

# Scoped-to-one-gap chat (issue #48). Reached from a gap card's "Discuss this gap"
# button; the chat is grounded in just that gap's source Papers and Evidence. The
# conversation is ephemeral for now \u2014 per-gap persistent conversations are a
# separate v2 feature (issue #44).
ANALYTICS_SCOPED_HEADING = "Discuss one gap: {title}"
ANALYTICS_SCOPED_INTRO = (
  "This chat is scoped to a single Candidate Gap \u2014 only its source Papers "
  "and Evidence \u2014 so it answers 'why is this a gap, and what would close "
  "it?' from just that slice. It remains a candidate for human judgment, not a "
  "verdict, and this conversation is not saved."
)
ANALYTICS_SCOPED_CHAT_INPUT_LABEL = "Ask about this gap"
ANALYTICS_SCOPED_EXIT_BUTTON = "\u2190 Back to the whole-Corpus chat"
ANALYTICS_SCOPED_GAP_MISSING = (
  "That Candidate Gap is no longer available. Showing the whole-Corpus chat."
)
