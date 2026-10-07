---
name: ui-audit
description: Audit the Streamlit dashboard's UI against the project's usability and research-integrity checklist, scoring every page and writing findings to docs/research/. Run by hand after a UI or navigation change.
disable-model-invocation: true
---

Score every dashboard page against one fixed checklist, so the same audit runs
the same way each time the UI changes. The checklist is the single source of
truth; this skill is the **process** that applies it.

## The checklist (do not restate it here)

`docs/research/ux-heuristics-for-evidence-dashboards.md` holds the whole
checklist: Part A (Nielsen's 10, H1–H10), Part B (the project's integrity rules
I1–I7), and the 0–4 severity scale. Read it in full before auditing. When a
heuristic or severity is unclear, that note is the authority, not memory.

## How the audit reads the UI

The agent audits by **reading the rendering code**, which is deterministic and
repeatable, not by looking at a live browser. Two heuristics still need a human
eye — **H8 (aesthetic/minimalist)** as it actually renders, and **colour-blind
safety** of charts — so those are flagged for a human screenshot pass rather
than scored from code alone.

For each page you read its renderer module plus the user-facing strings it
pulls from `src/research_gap_dashboard/dashboard/text.py` (all UI copy lives
there). Judge the copy and the control/layout logic against the checklist.

## Steps

1. **Enumerate the pages from the code, not from memory.** List the current
   pages from `_PAGE_RENDERERS` / `_select_page` in
   `src/research_gap_dashboard/dashboard/app.py`, cross-checked against the
   dashboard table in `docs/FUNCTIONALITY.md`. Audit the app shell
   (navigation, Corpus picker, banners in `app.py`) as its own entry. The list
   is whatever the code says today — this keeps the audit generalisable as
   pages are added or removed.
2. **Read the checklist** (`ux-heuristics-for-evidence-dashboards.md`) so
   H1–H10, I1–I7 and the severity scale are in context.
3. **Audit each page in turn.** Read its renderer module and the `text.py`
   strings it uses. Walk **all** of H1–H10 and **all** of I1–I7 against it.
   Record every problem found as a row (format below). A heuristic with no
   problem gets no row; a page with no problems says so explicitly.
4. **Flag the visual-only checks.** For each page, note which H8 / colour
   concerns need a human screenshot pass (`just dashboard`), rather than
   scoring them from code.
5. **Write the findings** to `docs/research/ui-audit-findings.md` (format
   below), matching the existing `docs/research/` note style.
6. **Roll up.** End the file with a severity rollup: counts of severity-4 and
   severity-3 findings, and the top fixes ordered by severity. Integrity
   violations (I1–I7) start at severity 3 unless lowered with a stated reason.

Completion criterion: every current page has been scored against every one of
H1–H10 and I1–I7 — no page skipped, no heuristic skipped. A page you judged
clean is recorded as clean, not omitted.

## Output format

One `docs/research/ui-audit-findings.md`. A short intro naming the commit/branch
and the checklist note, then one section per page:

```markdown
### <Page name> (`<module>.py`)

| Heuristic | Location | Severity | Finding / suggested fix |
| --- | --- | --- | --- |
| I2 | Coverage heatmap caption | 3 | No denominator shown; state N Papers the matrix is computed over. |

**Human screenshot pass:** H8 — <what to look at>; colour — <which chart>.
```

Then the final severity rollup.

## Boundaries

- This skill audits; it does not fix. Findings feed wayfinder → to-spec →
  to-tickets (`docs/journeys.md`, the handoff plan). Do not edit dashboard code
  from here.
- Chart truthfulness and colour-blind safety beyond the flag get their own
  deeper pass with the `scientific-visualization` skill (the chart audit, step
  3 of the plan). This audit only flags them.
