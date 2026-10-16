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
  "No Corpora yet. Start a new analysis below: name the Corpus and state its "
  "scope, then add its Papers."
)
INGESTABLE_FOLDERS_INTRO = (
  "Or ingest a corpus folder that already holds its Papers (a papers/ folder of "
  "PDFs and its paper list):"
)

# Creating a Corpus and its scope statement (issue #74).
NEW_CORPUS_HEADING = "New analysis"
NEW_CORPUS_INTRO = (
  "Create an empty Corpus and state its scope. The scope is shown on every page "
  "so readers know what the Corpus was built from."
)
NEW_CORPUS_NAME_LABEL = "Corpus name"
NEW_CORPUS_NAME_HELP = "Becomes the Corpus folder name: letters, digits, '-' or '_'."
NEW_CORPUS_SUBMIT = "Create Corpus"
SCOPE_QUESTION_LABEL = "Research question"
SCOPE_FIELD_LABEL = "Field"
SCOPE_SEARCHED_LABEL = "What was searched"
SCOPE_SEARCHED_HELP = (
  "Databases, search terms, date range and any filters, so a reader can tell "
  "what this Corpus does and does not cover."
)
CORPUS_SCOPE_STATEMENT = (
  "**Scope of this Corpus.** Question: {question} \u00b7 Field: {field} \u00b7 "
  "Searched: {searched}. Everything shown here covers only the Papers in this "
  "Corpus, not the whole literature."
)
SCOPE_MISSING_PROMPT = (
  "This Corpus has no scope statement yet. Add its question, field and what was "
  "searched, so every page can say what the Corpus covers."
)
SCOPE_ADD_EXPANDER = "Add a scope statement"
SCOPE_SAVE_BUTTON = "Save scope"
CORPUS_HAS_NO_PAPERS = (
  "This Corpus has no ingested Papers yet. Put its PDFs in `{papers_dir}` and "
  "its paper list (a `.bib` file or `dois.txt`) in `{root}`, then run ingest."
)
RUN_INGEST_BUTTON = "Run ingest"
NAV_LABEL = "Page"
PAGE_OVERVIEW = "Corpus Overview"
PAGE_GAP_CARDS = "Gap Cards"
PAGE_META = "Field Meta-Analysis"
PAGE_LIMITATIONS = "Unanswered Limitations"
PAGE_RETRIEVAL = "Retrieval Gaps"
PAGE_EXPLAINER = "Paper Explainer"
PAGE_COMPARISON = "Paper Comparison"
PAGE_ANALYTICS = "Conversational Analytics"
PAGE_VERIFY = "Verify Extractions"
PAGE_ATTENTION = "Needs Attention"
PAGE_PIPELINE = "Pipeline"

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
  "Nothing to report yet: the parse and extract stages haven't run on this Corpus."
)
ATTENTION_NO_PARSED_TEXT_REASON = (
  "no parsed text on disk; the parse stage has not read it"
)
ATTENTION_RERUN_HEADING = "Apply corrections and re-run"
ATTENTION_RERUN_HINT = (
  "After saving corrections below, apply them to the parsed text, then re-run "
  "extract so they reach the pipeline."
)
ATTENTION_UNPARSED_HEADING = "PDFs no parser could read"
ATTENTION_REJECTED_HEADING = "Papers whose extraction was rejected"
ATTENTION_DROPPED_HEADING = "Papers with dropped facts"
ATTENTION_CORRECTION_LABEL = "Paste this Paper's correct full text"
ATTENTION_CORRECTION_HELP = (
  "Saved to a review overlay under judgments/. Use the apply-parse-corrections "
  "and extract buttons at the top of this page to fold it into the pipeline."
)
ATTENTION_SAVE_CORRECTION = "Save correction"
ATTENTION_CORRECTION_SAVED = (
  "Saved. Now run apply-parse-corrections, then extract, with the buttons at the "
  "top of this page."
)
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
  "promoting them to a Gold Extraction fixture is a separate, deliberate step."
)
VERIFY_NO_EXTRACTIONS = (
  "No Extractions to review yet: the extract stage hasn't run on this Corpus."
)
VERIFY_NO_PARSED_TEXT = (
  "This Paper has no parsed text, so edits and added facts cannot be grounded. "
  "The parse stage has not read its PDF yet."
)
VERIFY_PAPER_PICKER_LABEL = "Paper to verify"
VERIFY_SUMMARY = (
  "{reviewed} of {extracted} extracted facts reviewed; {added} facts added."
)
VERIFY_CORPUS_PROGRESS = (
  "{reviewed} of {total} extracted facts reviewed across the Corpus; "
  "{fully} of {papers} Papers fully reviewed."
)
VERIFY_CORPUS_COMPLETE = (
  "Every extracted fact in the Corpus has a verdict: verification is done."
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
  "Completeness can't be measured yet: run the parse and extract stages."
)

# Per-axis frequency bars: how often each Topic, Method, Population, and Dataset
# appears across the Papers placed on that axis (issue #45).
AXIS_FREQUENCIES_HEADING = "What the Corpus studies"
AXIS_FREQUENCIES_QUESTION = (
  "How often does each Topic, Method, Population, and Dataset appear across the Corpus?"
)
AXIS_FREQUENCIES_NO_DATA = (
  "No Papers placed yet: the aggregate stage places Papers on the Topic, "
  "Method, Population, and Dataset axes."
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
  "No facts to count yet: the extract stage measures how many facts each Paper "
  "contributed."
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
  "No candidate gaps yet: the detect stage hasn't run on this Corpus. It needs "
  "the Corpus ingested, extracted and aggregated first."
)
RUN_STAGE_BUTTON = "\u25b6 Run {stage}"
RUN_DETECT_BUTTON = RUN_STAGE_BUTTON.format(stage="detect")
RUN_INGEST_CORPUS_BUTTON = "\u25b6 Run ingest on {name}"
RUN_INGEST_RESOLVE_BUTTON = "\u25b6 Run ingest with DOI resolution"


def run_stage_button(stage: str) -> str:
  """Label the button that runs one pipeline stage on the chosen Corpus."""
  return RUN_STAGE_BUTTON.format(stage=stage)


# Pipeline runs launched from the dashboard (ADR 0007). `{stage}` is the CLI
# command name. Exit code 2 is "completed but incomplete" (ADR 0006), never a
# failure; exit code 1 is a failure.
RUN_IN_PROGRESS = "Running `{stage}` on this Corpus\u2026"
RUN_SUCCEEDED = "`{stage}` succeeded."
RUN_INCOMPLETE = (
  "`{stage}` completed but incomplete: some Papers fell out. See Needs "
  "Attention and the run output below."
)
RUN_FAILED = "`{stage}` failed. See the run output below for the reason."
RUN_LOCKED = (
  "Another run is already in progress on this Corpus. Wait for it to finish, "
  "then try again."
)
RUN_MISSING_KEY = (
  "`{stage}` needs an Anthropic API key and was not started. Set "
  "ANTHROPIC_API_KEY in your .env (see .env.example), then reload."
)
RUN_OUTPUT_LABEL = "Run output"
RUN_COMMAND_LABEL = "Command: `{command}`  \u00b7  exit code {exit_code}"
RUN_STDOUT_LABEL = "Standard output"
RUN_STDERR_LABEL = "Standard error"
RUN_NO_OUTPUT = "(no output)"
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

# Confidence filter (issue #108): the hidden count is always shown, even when
# zero, so filtering never makes gaps disappear silently.
GAP_FILTER_LABEL = "Show confidence levels"

# Per-card labels.
GAP_CONFIDENCE_LABEL = "Confidence: {confidence}"
GAP_CELL_COUNT_LABEL = "Papers in this cell: {count} of {total}."
GAP_EVIDENCE_HEADING = "Evidence"
GAP_EVIDENCE_SOURCE = "{citation_key} — {section}"
# Three-tier cards (ADR 0008, issue #107): a headline, then two expanders read
# straight from the artifact, so no LLM runs at view time.
GAP_WHY_CELL = (
  "{row_count} of {total} Papers cover {row} and {column_count} of {total} "
  "cover {column}; {count} combine them."
)
GAP_WHY_LIMITATION = (
  "{source_count} Paper(s) state this; none of {later_count} later Paper(s) "
  "addressed it."
)
GAP_TIER_JUSTIFICATION_LABEL = "Why this is a candidate: Evidence"
GAP_TIER_DATA_LABEL = "Show the data"
GAP_SOURCE_PAPERS_GROUP = "Source Papers"
GAP_DATA_PAPERS_HEADING = "Papers in each category"
GAP_DATA_CATEGORY_PAPERS = "**{label}** ({count}): {papers}"
GAP_DATA_NO_PAPERS = "none"
GAP_DATA_NEIGHBOURS_HEADING = "Neighbouring cells"
GAP_DATA_NEIGHBOUR = "- {row} × {column}: {count} Paper(s)"
GAP_DATA_NO_NEIGHBOURS = "No neighbouring cells in this Coverage Matrix."
GAP_DATA_NEIGHBOURS_MORE = "…and {count} more neighbouring cells."
GAP_DATA_SCORE_HEADING = "Score and reasons"
GAP_DATA_SCORE = (
  "Expected about {expected:.1f} Papers in this cell if its categories were "
  "independent across the {total} Papers; {observed} observed. This is a ranking "
  "score, not a significance test."
)
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
# Triage controls (issue #79). The hidden count is always stated so a filtered
# list is never mistaken for every Candidate Gap (CODING_STANDARDS.md > Show the
# denominator).
GAP_FILTER_STATUS_LABEL = "Show status"
GAP_FILTER_TYPE_LABEL = "Show Gap Types"
GAP_FILTER_SUMMARY = (
  "Showing {shown} of {total} candidate gaps; {hidden} hidden by the filters."
)
GAP_FILTER_NO_MATCH = "No candidate gaps match these filters."
GAP_NEXT_UNDECIDED_BUTTON = "Next undecided"
GAP_NO_UNDECIDED_SHOWN = "No undecided gaps among the cards shown."
GAP_FOCUS_HEADING = "Current gap"
GAP_OTHERS_HEADING = "Other gaps"

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

# The Trends chart reads the artifacts the pipeline wrote (ADR 0002); every count
# states its denominator (CODING_STANDARDS.md > Research integrity).
HOW_TO_READ_LABEL = "How to read this chart"

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

# Field Meta-Analysis page (issue #49). A comprehensive-but-honest layer of
# network graphs over the whole Corpus. Every graph names the question it
# answers and inherits the incomplete-Corpus caveat; absent edges and isolated
# nodes are surfaced, never hidden, because that is where the gaps live.
META_HEADING = "Field Meta-Analysis"
META_INTRO = (
  "A field-level view of the whole Corpus as network graphs \u2014 a **map for "
  "human judgment, not a set of verdicts**. Every graph names the question it "
  "answers; an absent edge or an isolated node is a fact about *this* Corpus, "
  "never a measured zero, and is where a candidate gap may hide."
)
META_CAVEAT = (
  "Computed only over the Papers in this Corpus. A missing edge means no Paper "
  "here links the two \u2014 it is not a claim about the wider literature."
)
META_NO_MANIFEST = (
  "This Corpus hasn't been ingested yet; the meta-analysis reads the "
  "CorpusManifest that ingest writes."
)
META_NO_NORMALIZED = (
  "The aggregate stage hasn't run on this Corpus yet; the co-occurrence graphs "
  "read the NormalizedFacts artifact."
)
META_QUESTION_LABEL = "Question this graph answers"
META_GAP_LENS_LABEL = "Gap lens"
META_DATA_TABLE_LABEL = "Data table (accessible fallback)"
META_EMPTY_GRAPH = "Nothing to draw here yet for this Corpus."
META_LEGEND_TITLE = "Node"
META_CONNECTED_LEGEND = "Connected"
META_ISOLATED_LEGEND = "Isolated"
META_SELECTED_LEGEND = "Selected"
META_NEIGHBOR_LEGEND = "Neighbour"
META_FADED_LEGEND = "Other"
META_CONNECTED_COLOR = "#2c7fb8"
META_ISOLATED_COLOR = "#d62728"
META_SELECTED_COLOR = "#e6a817"
META_CLICK_HINT = (
  "Click a node to highlight it and its neighbours; click empty space to clear. "
  "The data table below is the non-interactive way to read the same links."
)
META_SELECTED_CAPTION = "Selected **{label}** \u2014 {count} neighbour(s): {neighbours}"
META_SELECTED_NONE = "Selected **{label}** \u2014 no neighbours in this graph."
META_CLEAR_SELECTION = "Clear selection"
META_MISSING_PAIR_JUMP = "View in Gap Cards"
META_MISSING_PAIRS_HINT = (
  "Each pair below is an absent edge \u2014 a candidate gap, strongest first "
  "(both categories well-studied but never combined). Jump to the Gap Cards "
  "page to see the matching card and its Evidence."
)
META_MISSING_MORE = "+{more} more absent pairs (see the data table for all links)."
META_MISSING_PAIRS_LABEL = "Missing pairs (candidate gaps)"
META_NO_MISSING_PAIRS = "Every pair of shown categories co-occurs in some Paper."
META_MISSING_PAIR_ROW = "{source} \u00d7 {target}"

# The meta-analysis gallery: the six graphs as collapsible tiles (shared idiom).
META_GALLERY_HINT = (
  "Each graph is a tile below; open the ones you want to read full size."
)
META_PREVIEW_EMPTY = "No data to preview yet \u2014 open the tile for details."

# Graph 1: Citation network.
META_CITATION_TITLE = "Citation network (in-Corpus citations)"
META_CITATION_QUESTION = (
  "Which Papers in this Corpus cite which other Papers in this Corpus?"
)
META_CITATION_GAP_LENS = (
  "An isolated node \u2014 a Paper no in-Corpus citation touches \u2014 is a "
  "candidate Retrieval Gap: the work that would connect it may be missing."
)
META_CITATION_DENOMINATOR = (
  "Drawn over the {resolved} of {total} Papers with an OpenAlex id."
)
META_CITATION_UNRESOLVED = (
  "{count} Paper(s) have no OpenAlex id and are not on the graph (resolve their "
  "DOIs with the button below): {keys}."
)
META_CITATION_NO_DOI = (
  "{count} Paper(s) have no DOI, so they cannot be resolved and are not on "
  "the graph: {keys}."
)
META_CITATION_SPARSE = (
  "Sparse citation structure: fewer in-Corpus citations than Papers. On a "
  "tight topical Corpus this is expected; read isolated nodes with care."
)
META_CITATION_ISOLATED_HOVER = "No in-Corpus citation \u2014 candidate Retrieval Gap"

# Graphs 2 and 6: Topic and Method co-occurrence.
META_TOPIC_TITLE = "Topic co-occurrence"
META_TOPIC_QUESTION = "Which Topics do Papers in this Corpus study together?"
META_TOPIC_GAP_LENS = (
  "A missing edge between two Topics is a candidate Knowledge Gap: no Paper "
  "here studies them together."
)
META_METHOD_TITLE = "Method co-occurrence"
META_METHOD_QUESTION = "Which Methods do Papers in this Corpus apply together?"
META_METHOD_GAP_LENS = (
  "A missing edge between two Methods is a candidate Coverage Gap: no Paper "
  "here combines them. This is method *co-occurrence*, not method similarity."
)
META_COOCCURRENCE_DENOMINATOR = "Nodes are the {total} {axis} categories shown."
META_COOCCURRENCE_CAPPED = (
  "Showing the {shown} most-covered of {total} {axis} categories to keep the "
  "graph legible."
)
META_COOCCURRENCE_NODE_HOVER = "{label}: {count} Paper(s)"
META_COOCCURRENCE_EDGE_HOVER = "{left} + {right}: {count} Paper(s)"
META_COOCCURRENCE_WEAK_HIDDEN = (
  "{count} link(s) backed by fewer than {threshold} Paper(s) are hidden as weak "
  "signal; lower the threshold to show them."
)
META_COOCCURRENCE_EMPTY_REASON = (
  "NormalizedFacts placed no Corpus Paper on the {axis} axis, so there is "
  "nothing to co-occur. Run the aggregate stage, or check the {axis} taxonomy "
  "covers this Corpus."
)
META_METHOD_THIN_NOTE = (
  "Few Methods mapped: the Method axis holds study designs only, so clinical "
  "procedures and treatments count as Topics and are set aside here as 'not a "
  "study design'. A thin graph here usually means the Papers share few "
  "designs, or the Extraction predates the study-design fact (re-run extract)."
)
META_COOCCURRENCE_CONTROLS_LABEL = "Graph controls"
META_MIN_EDGE_LABEL = "Minimum Papers per link"
META_MAX_NODES_LABEL = "Maximum categories shown"
META_FIGURE_SUMMARY = (
  "Network figure: {nodes} nodes, {edges} links, {isolated} isolated. "
  "The data table below lists every link."
)
META_MISSING_STRENGTH = "both well-studied ({strength}+ Papers each)"
META_OPEN_NODE_JUMP = "Open in Paper Explainer"
META_LIMITATION_ALL_OPEN_NOTE = (
  "Every group is still open. {no_later} had no later Paper in the Corpus to "
  "check (their source Paper is among the most recent); the rest were checked "
  "against later Papers and none addressed them. This may be a real signal or a "
  "chronologically clustered Corpus \u2014 read it as a candidate, not a verdict."
)

# Graph 4: Author collaboration.
META_COLLABORATION_TITLE = "Author collaboration"
META_COLLABORATION_QUESTION = (
  "Which authors co-wrote Papers in this Corpus with which others?"
)
META_COLLABORATION_GAP_LENS = (
  "Field-meta, not a gap: this describes the Corpus's collaboration structure. "
  "An author with no co-author here is not any kind of gap."
)
META_COLLABORATION_DENOMINATOR = (
  "Nodes are the {authors} authors across the {papers} Papers that list authors."
)
META_COLLABORATION_MISSING = (
  "{count} Paper(s) list no authors and contribute no node (resolve their DOIs "
  "with the button below)."
)
META_COLLABORATION_TABLE_CAPPED = (
  "Showing the {shown} most-published of {total} authors."
)
META_COLLABORATION_COL_AUTHOR = "Author"
META_COLLABORATION_COL_PAPERS = "Papers in Corpus"
META_COLLABORATION_COL_COLLABORATORS = "Distinct co-authors"

# Graph 5: Limitation follow-up (segmented status bar, not a per-group Sankey).
META_LIMITATION_TITLE = "Limitation follow-up"
META_LIMITATION_QUESTION = (
  "Which stated limitations did a later Paper in this Corpus address?"
)
META_LIMITATION_GAP_LENS = (
  "A limitation still open after later Papers were checked is a candidate "
  "Unanswered Limitation: no later Paper here addressed it."
)
META_LIMITATION_ADDRESSED_LABEL = "Addressed by a later Paper"
META_LIMITATION_OPEN_LABEL = "Still open (later Papers checked)"
META_LIMITATION_NO_LATER_LABEL = "No later Paper to check yet"
META_LIMITATION_BAR_ROW = "Limitation groups"
META_LIMITATION_BAR_X = "Number of limitation groups"
META_LIMITATION_SUMMARY = (
  "Of {total} limitation group(s): {addressed} addressed by a later Paper, "
  "{open} still open after later Papers were checked, and {no_later} with no "
  "later Paper in the Corpus to check yet."
)
META_LIMITATION_TOP_ADDRESSED = "Most-addressed limitations:"
META_LIMITATION_TOP_ROW = "\u2022 {label} \u2014 {count} follow-up(s)"
META_LIMITATION_NO_DATA = "This Corpus produced no limitation groups."

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
  "No limitation groups yet: the detect stage hasn't run on this Corpus."
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
  "No out-of-corpus candidates yet: the retrieve stage hasn't run on this Corpus."
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
RETRIEVAL_NO_DOI = (
  "{count} Paper(s) have no DOI, so they carry no citation list and are not "
  "ranked over."
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
  "This Corpus has no Papers to explain yet: ingest it to list its Papers."
)
EXPLAINER_PAPER_PICKER_LABEL = "Paper"
EXPLAINER_SUMMARY = "{explained} of {total} Papers have an explanation."
NO_EXPLANATION_FOR_PAPER = (
  "No explanation for this Paper yet: the explain stage hasn't covered it."
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
  "This Corpus has not been extracted and aggregated yet: run the extract "
  "stage, then aggregate."
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
  "This Corpus has not been ingested yet: ingest it to start a conversation."
)
ANALYTICS_NO_KEY = (
  "Conversational Analytics needs an Anthropic API key to answer. Set "
  "ANTHROPIC_API_KEY in your environment (see .env.example), then reload."
)
ANALYTICS_CHAT_HEADING = "Chat"
ANALYTICS_CHAT_INPUT_LABEL = "Ask about the Papers, gaps, or data"

# Conversation management (issue #50): several persisted threads \u2014 a general one
# and one per Candidate Gap \u2014 each listable, renamable, and deletable.
ANALYTICS_GENERAL_TITLE = "General (whole Corpus)"
ANALYTICS_CONVERSATION_PICKER_LABEL = "Conversation"
ANALYTICS_CONVERSATION_OPTION = "{title} ({turns} turns)"
ANALYTICS_NEW_CONVERSATION_BUTTON = "\u2795 New conversation"
ANALYTICS_NEW_CONVERSATION_TITLE = "New conversation"
ANALYTICS_DELETE_CONVERSATION_BUTTON = "\U0001f5d1 Delete this conversation"
ANALYTICS_RENAME_LABEL = "Rename this conversation"
ANALYTICS_RENAME_BUTTON = "Rename"

# Curated research journal (issue #50): the only cross-conversation channel. Only
# snippets the researcher explicitly keeps enter it; each records its provenance
# and is reused labelled as the researcher's own note (CODING_STANDARDS.md >
# Research integrity).
ANALYTICS_KEEP_BUTTON = "\U0001f4cc Keep this in the journal"
ANALYTICS_KEEP_CONFIRM = "Kept in the research journal."
ANALYTICS_JOURNAL_HEADING = "Research journal"
ANALYTICS_JOURNAL_INTRO = (
  "Snippets you chose to keep. They carry across conversations as your own "
  "notes, and any claim built on them still cites only Papers in this Corpus."
)
ANALYTICS_JOURNAL_EMPTY = (
  "Nothing kept yet. Use 'Keep this in the journal' on an answer to save it "
  "here with its source gap and Papers."
)
ANALYTICS_JOURNAL_DELETE_BUTTON = "Remove from journal"
ANALYTICS_JOURNAL_SOURCE_GAP = "From gap: {gap}"
ANALYTICS_JOURNAL_SOURCE_PAPERS = "Papers: {papers}"
ANALYTICS_JOURNAL_SOURCE_NONE = "Kept from the whole-Corpus chat."


def journal_source_label(gap_id: str | None, citation_keys: list[str]) -> str:
  """Describe a kept note's provenance: the gap and Corpus Papers behind it."""
  parts: list[str] = []
  if gap_id:
    parts.append(ANALYTICS_JOURNAL_SOURCE_GAP.format(gap=gap_id))
  if citation_keys:
    parts.append(
      ANALYTICS_JOURNAL_SOURCE_PAPERS.format(papers=", ".join(citation_keys))
    )
  return "  \u00b7  ".join(parts) if parts else ANALYTICS_JOURNAL_SOURCE_NONE


ANALYTICS_THINKING = "Grounding an answer in the Corpus\u2026"
ANALYTICS_CITATIONS_LABEL = "Grounded in: {papers}"
ANALYTICS_NO_CITATIONS = "This answer cites no Corpus Paper."
ANALYTICS_DROPPED_LABEL = (
  "\u26a0\ufe0f Dropped {count} citation(s) that do not resolve to a Corpus "
  "Paper: {papers}."
)
ANALYTICS_SUMMARY_HEADING = "Narrative summary"
ANALYTICS_SUMMARY_INTRO = (
  "A few paragraphs for a 'gaps in the literature' section, drawn only from "
  "the Candidate Gaps you accepted on the Gap Cards page and citing only this "
  "Corpus's Papers."
)
ANALYTICS_SUMMARY_NEEDS_ACCEPTED = (
  "You have not accepted any Candidate Gaps yet ({undecided} still undecided). "
  "The narrative summary is built only from gaps you accept, so open the Gap "
  "Cards page, accept the gaps you judge real, then come back here."
)
ANALYTICS_SUMMARY_DENOMINATOR = (
  "Covers {accepted} accepted gap(s) over {papers} Papers. "
  "Excluded: {rejected} rejected and {undecided} undecided gap(s)."
)
ANALYTICS_SUMMARY_NEEDS_GAPS = (
  "No Candidate Gaps found for this Corpus yet: run the detect stage to "
  "generate a narrative summary."
)
ANALYTICS_GENERATE_SUMMARY_BUTTON = "Generate narrative summary"

# Scoped-to-one-gap chat (issue #48), now a persisted thread (issue #50). Reached
# from a gap card's "Discuss this gap" button; the thread is grounded in just that
# gap's source Papers and Evidence, and persists so it can carry over months.
ANALYTICS_SCOPED_TITLE = "Gap: {title}"
ANALYTICS_SCOPED_HEADING = "Discuss one gap: {title}"
ANALYTICS_SCOPED_INTRO = (
  "This thread is scoped to a single Candidate Gap \u2014 only its source Papers "
  "and Evidence \u2014 so it answers 'why is this a gap, and what would close "
  "it?' from just that slice. It remains a candidate for human judgment, not a "
  "verdict, and this conversation is saved so you can return to it."
)

# Pipeline status panel (ADR 0007, issue #70). Status is read from which
# artifacts exist and when they were written; re-run is always offered. Cost
# appears only inside the collapsed Run details, so it never distracts.
PIPELINE_HEADING = "Pipeline"
PIPELINE_INTRO = (
  "Each stage writes an artifact. A stage is **stale** when one of its inputs "
  "changed after it last ran; re-run it to bring it up to date. Any stage can be "
  "re-run at any time: unchanged work is served from the cache."
)
STAGE_STATE_LABELS = {
  "done": "\u2705 Done",
  "not_run": "\u26aa Not run",
  "stale": "\U0001f7e0 Stale",
}
STAGE_ROW = "**{stage}** \u00b7 {state} \u00b7 writes `{artifact}`"
STAGE_LAST_RUN = "Last written {when:%Y-%m-%d %H:%M}."
STAGE_NEWER_INPUTS = "Changed since: {inputs}"
STAGE_NEWER_INPUTS_MORE = " and {count} more"
RUN_STAGE_BUTTON = "\u25b6 Run {stage}"
RERUN_STAGE_BUTTON = "\u21bb Re-run {stage}"
RUN_DETAILS_LABEL = "Run details"
RUN_DETAILS_INTRO = (
  "Estimates are a rough upper bound: Papers in the Corpus ({papers}) times a "
  "per-Paper token allowance at `{model}` list prices, as if nothing were "
  "cached. Re-runs usually cost far less. A run estimated above "
  "{threshold} (LLM_COST_CONFIRM_ABOVE) asks for confirmation first."
)
RUN_COST_HEADER = (
  "| Stage | Estimated (upper bound) | Last run | Calls (cached) |\n|---|---|---|---|"
)
RUN_COST_ROW = "| {stage} | {estimate} | {actual} | {calls} |"
RUN_COST_UNPRICED = "unknown (no price for this model)"
RUN_COST_NOT_RECORDED = "not recorded"
RUN_COST_NOT_APPLICABLE = "\u2014"
RUN_CONFIRM_PROMPT = (
  "`{stage}`'s estimated cost is above your confirmation threshold "
  "(LLM_COST_CONFIRM_ABOVE). Open Run details for the estimate. Run it anyway?"
)
RUN_CONFIRM_BUTTON = "Run anyway"
RUN_CANCEL_BUTTON = "Cancel"
