# Verdict: guided-flow navigation prototype

**Question:** what navigation structure should replace the flat 10-page
sidebar (UI audit finding #1, `docs/research/ui-audit-findings.md`)?

**Answer:** **Variant B** (home screen + phase-grouped sidebar) as the base, for
its look and its closeness to today's Streamlit shell, **plus the following
from Variant A** to make the data and user path explicit:

1. A step bar on every page: 1 Scope → 2 Build → 3 Trust → 4 Explore → 5 Judge
   → 6 Grow → 7 Write; completed steps green, current step highlighted.
2. Sidebar items numbered in step order (free navigation stays).
3. "← Previous / Next →" buttons naming the adjacent step.
4. A "Recommended next step" button on the home screen, driven by pipeline
   status.
5. A **soft Trust gate**: Explore, Judge, Grow and Write stay locked until
   Trust is handled, meaning the fallout is fixed **or** the researcher
   acknowledges the denominator ("these gaps come from 10 of 15 Papers"). The
   acknowledgement is recorded and the incompleteness banner stays visible.
   Not a hard lock, because an unrecoverable Paper would otherwise block the
   Corpus forever (ADR 0006, "surface, don't block").

**Rejected:** Variant C (single scrolling report), too long and too far from
Streamlit's page model.

Decided 2026-10-04. The prototype code stays on this branch; only this
decision goes into the real implementation.
