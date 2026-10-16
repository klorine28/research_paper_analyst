"""
Session-state keys the shell and several pages share.

A page that switches to another page (a gap card's "Discuss this gap", a
citation-network node's Explainer jump) writes these keys; the shell reads them
before building the page picker.
"""

# NAV_PAGE_KEY holds the title of the page shown. A page that switches to
# another one (a gap card's "Discuss this gap") records its target in
# PENDING_NAV_KEY and reruns; the shell applies it before building the sidebar.
# The discuss key names the gap a scoped chat is focused on (issue #48).
NAV_PAGE_KEY = "nav_page"
PENDING_NAV_KEY = "pending_nav_page"
DISCUSS_GAP_KEY = "discuss_gap_id"
# A citation-network node click records the Paper it wants the Explainer to open
# here, so the Explainer page can preselect it after the nav switch (issue #49).
EXPLAINER_PAPER_KEY = "explainer_paper_key"
# Gap triage (issue #79): the status and Gap Type filter widgets, and the card
# "Next undecided" last brought into focus.
GAP_FILTER_STATUS_KEY = "gap_filter_status"
GAP_FILTER_TYPE_KEY = "gap_filter_type"
GAP_FOCUS_KEY = "gap_focus_id"
# The "possibly addressed only" filter of the gap list (issue #90).
GAP_FILTER_POSSIBLY_ADDRESSED_KEY = "gap_filter_possibly_addressed"
# The Gap Cards confidence filter keeps its choice here rather than only under its
# widget key: Streamlit drops a widget's state once a run doesn't render it, so
# without this copy the filter would reset whenever the researcher left the page
# (issue #108).
GAP_CONFIDENCE_FILTER_KEY = "gap_confidence_filter"
GAP_CONFIDENCE_WIDGET_KEY = "gap_confidence_filter_widget"
# The last pipeline run's result survives the rerun that follows it, so the
# outcome stays on screen after the page redraws with the new artifact (ADR 0007).
LAST_RUN_KEY = "last_pipeline_run"
# The corpus picker carries a key so a newly created Corpus can be opened: the New
# analysis form records its name here and the shell applies it before the picker
# is built, as with PENDING_NAV_KEY (issue #74).
CORPUS_PICKER_KEY = "corpus_picker"
PENDING_CORPUS_KEY = "pending_corpus"
# A run whose estimated cost is above LLM_COST_CONFIRM_ABOVE waits here, as
# (corpus root, stages, button key, args), until the researcher confirms or
# cancels it under the button that asked (issue #70).
PENDING_RUN_KEY = "pending_pipeline_run"
# A gap card's "Compare" button records the gap's Papers here so the Comparison
# page opens with them selected after the nav switch (issue #80). The Explainer
# link reuses EXPLAINER_PAPER_KEY.
COMPARISON_PAPERS_KEY = "comparison_paper_keys"
# The Explainer and Comparison pickers carry keys so a preselection is written into
# their state rather than passed as a default: a default that changes between runs
# gives the widget a new identity and throws the researcher's later choice away.
EXPLAINER_PICKER_KEY = "explainer_paper_picker"
COMPARISON_PICKER_KEY = "comparison_paper_picker"
# The last generated narrative summary, as (corpus root, the accepted-gaps scope
# it was generated over, narrative), so it and its export buttons survive the
# rerun a download click triggers, and are dropped once the judgments change
# (issue #82).
NARRATIVE_KEY = "narrative_summary"
# The Add Papers panel keeps what its last upload placed and the pairing check
# that followed, so both stay on screen across the rerun the upload triggers
# (issue #75).
UPLOAD_STATE_KEY = "paper_upload_state"
# The page shown is kept here as plain state, not a widget's: the sidebar, the
# step bar and Previous/Next all write it from their button callbacks. The Home
# screen knows steps, not pages, so its "Recommended next step" button records a
# step under PENDING_STEP_KEY and the shell opens that step's first page
# (issue #76).
PENDING_STEP_KEY = "pending_nav_step"
