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
# The last pipeline run's result survives the rerun that follows it, so the
# outcome stays on screen after the page redraws with the new artifact (ADR 0007).
LAST_RUN_KEY = "last_pipeline_run"
