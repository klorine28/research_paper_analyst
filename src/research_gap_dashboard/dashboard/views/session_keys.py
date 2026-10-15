"""
Session-state keys the shell and several pages share.

A page that switches to another page (a gap card's "Discuss this gap", a
citation-network node's Explainer jump) writes these keys; the shell reads them
before building the page picker.
"""

# The nav radio carries a key so a gap card can switch the page to
# Conversational Analytics programmatically. A widget's key can't be written once
# the widget exists, so a page records its target in PENDING_NAV_KEY and the
# shell applies it before the radio is built; the discuss key names the gap a
# scoped chat is focused on (issue #48).
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
# The Gap Cards confidence filter keeps its choice here rather than only under its
# widget key: Streamlit drops a widget's state once a run doesn't render it, so
# without this copy the filter would reset whenever the researcher left the page
# (issue #108).
GAP_CONFIDENCE_FILTER_KEY = "gap_confidence_filter"
GAP_CONFIDENCE_WIDGET_KEY = "gap_confidence_filter_widget"
# The last pipeline run's result survives the rerun that follows it, so the
# outcome stays on screen after the page redraws with the new artifact (ADR 0007).
LAST_RUN_KEY = "last_pipeline_run"
