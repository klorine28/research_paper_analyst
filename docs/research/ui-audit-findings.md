# UI audit findings — Streamlit dashboard

A single-evaluator heuristic audit of every dashboard page against the checklist
in `docs/research/ux-heuristics-for-evidence-dashboards.md` (Nielsen's 10 =
H1–H10; the project's integrity rules = I1–I7; NN/g 0–4 severity scale).

- **Audited at:** commit `989d73a`, branch `feat/49-meta-analysis`.
- **Method:** read each page's renderer in
  `src/research_gap_dashboard/dashboard/app.py` plus its data module and the
  strings it pulls from `text.py`. Pages enumerated from `_select_page` /
  `_PAGE_RENDERERS`. Scored from code; visual-only checks (H8 as rendered,
  colour-blind safety) are flagged for a human `just dashboard` pass, not scored
  here.
- **Scope of the audit:** this is the usability/integrity audit (plan step 2).
  Chart truthfulness and colour get a deeper pass with
  `scientific-visualization` (step 3); those findings only *flag* charts here.

## Headline

The dashboard is strong on **research integrity**. Denominators travel with
almost every count and chart (I2), Evidence is quoted verbatim and widened to
its paragraph inline on gap cards and Verify (I3), every gap-bearing page
repeats "candidate for human judgment, not a verdict" (I4), generated text drops
ungrounded citations and reports what it dropped (I5), and Papers that fell out
are surfaced on Needs Attention and in a banner on every page (I6). These are
the hard things and they are done well.

The weaknesses are **structural and in the seams**, and they match the six pain
points in `docs/journeys.md`:

1. **Navigation (H8/H4/H2).** Ten equally-weighted flat pages in an order that
   does not follow the task; the two trust gates (Verify, Needs Attention) sit
   last. One severity-3 structural finding, cross-cutting.
2. **Terminal hand-offs (H9).** Nearly every empty/guard state tells the
   researcher to go run a CLI command (`Run \`extract\` first`,
   `Run \`apply-parse-corrections\` then \`extract\``). Recurring severity-3.
3. **Scope not fully stated (I1).** What was *searched* (query, sources, dates)
   is never recorded; the scope/candidates statement shows only on Overview.
4. **No in-app system status (H1).** Nothing shows which stages have run beyond
   the guard messages inferred per page.

---

### App shell & navigation (`app.py` `main`, `_select_page`, `_select_corpus`, `_render_incompleteness_banner`)

| Heuristic | Location | Severity | Finding / suggested fix |
| --- | --- | --- | --- |
| H8, H4 | `_select_page` radio (10 pages) | 3 | Ten pages presented flat and equally weighted, no task order, no grouping. The signal (gaps + evidence) does not dominate. Group into the journey (Scope → Build → Trust → Explore → Judge → Grow → Write) per `docs/journeys.md`. |
| H2, H4 | `_select_page` order | 3 | Verify Extractions and Needs Attention — the checks that decide whether anything else can be trusted — sit at the bottom. Move the trust step before the explore pages. |
| H1 | shell (no pipeline status) | 3 | Nothing shows which stages have run for the chosen Corpus; the researcher learns it only by visiting a page and hitting its guard message. No place shows cost/cache either. (Target UX "Build" panel — needs the runner ADR.) |
| H1 | `_select_corpus` sidebar | 2 | The Corpus picker shows only names; no paper count, no "built through stage X" at the point of choosing. |
| H7 | `main` / `_corpora_root` | 2 | No memory of the last Corpus across reloads, no deep links to a page+corpus, no keyboard accelerators for frequent triage. |
| I1 | `_render_incompleteness_banner` + scope | 3 | The banner (denominator) shows on every page, good; but the scope statement and "what Corpus am I in / what was searched" do not. Corpus identity lives only in the sidebar; search scope is never recorded. |

**Human screenshot pass:** H8 — does the sidebar radio read as a flat wall? Is the always-on incompleteness banner so loud it competes with page content?

---

### Corpus Overview (`overview.py`, `app.py` `render_overview`)

| Heuristic | Location | Severity | Finding / suggested fix |
| --- | --- | --- | --- |
| I1 | `SCOPE_STATEMENT` / `render_overview` | 3 | The scope statement is generic (candidates-not-verdicts + "computed only over this Corpus"). It never states **what was searched**: the query, sources, or date range the Corpus was built from (Brief: "what was searched and what was excluded"). Exclusions (unmatched/orphans) are shown; the search side is absent. |
| H2 | `_render_overview_gallery` tiles | 1 | "Fact density" / "Axis frequencies" are internal framings; a first-time researcher may not map them to a question. Give each tile a one-line "what this answers". |
| H10 | gallery tiles | 1 | How-to-read expanders exist on the expanded panels (good), but the collapsed tiles give no hint of what the chart is for before opening. |

Integrity otherwise strong: denominators on every axis chart (`AXIS_DENOMINATOR`), completeness gauge is an explicit honesty meter (I6), exclusions tabled (I6).

**Human screenshot pass:** H8 — the 2×2 tinted-blue tile gallery plus completeness gauge plus exclusions tables: does it read as one scannable page or a long scroll? Colour — the four blue tile shades as sole tile identity (fine, labels present, but check contrast of pinned-dark text on the darkest shade `#6fa4df`).

---

### Field Meta-Analysis (`meta_analysis.py`, `app.py` `_render_meta_page` et al.)

| Heuristic | Location | Severity | Finding / suggested fix |
| --- | --- | --- | --- |
| H8 | `_META_TILES` (6 graphs) | 2 | Six graph tiles on one page, each with sliders, selection state, missing-pair lists and data tables. Dense; the gallery-collapse idiom mitigates it, but an opened panel is heavy. |
| H2 | tile titles ("Field Meta-Analysis") | 1 | The page name is jargon for the primary user; "what connects to what, and what never does" is the real question. The per-graph `META_QUESTION_LABEL` captions do carry it — promote that framing up. |
| H5 | co-occurrence sliders | 1 | Slider state persists per axis in session; a reader can filter a graph to near-empty and not realise the emptiness is a filter, not the data. The "reports what it hides" note helps; confirm it fires at the fully-filtered extreme. |
| I3 | missing-pair jump | 2 | "Jump to Gap Cards" from a missing pair lands on the whole Gap Cards page, not the specific gap (already noted in `meta-analysis-graph-audit.md`). The claim (this pair is a gap) is traceable only by hand after the jump. |

Integrity strong: every graph carries question, gap-lens, node/edge/isolated counts, a data-table fallback (accessibility), and a caveat; empty/degenerate states explain themselves.

**Human screenshot pass:** H8 — an opened network panel with sliders + selection + missing pairs + data table: overload check. Colour — network node/edge colours and the limitation follow-up bar; verify categories are distinguishable without relying on hue alone (chart audit, step 3).

---

### Gap Cards (`gaps.py`, `app.py` `_render_gap_card`)

| Heuristic | Location | Severity | Finding / suggested fix |
| --- | --- | --- | --- |
| H3 | accept/reject/clear buttons | 2 | Accept and reject are one-click with no undo prompt; "Clear" is the only recovery and is a third button, not an obvious undo. Low risk (clear exists) but a misclick silently rewrites a verdict. |
| H7 | per-card triage | 2 | Triaging many gaps means scrolling and clicking each; no keyboard accelerator, no "next undecided", no filter by status/type/confidence. The judgment summary counts exist but can't filter the list. |
| H6 | "Discuss this gap" | 1 | Good: opens a scoped chat in context (addresses the cross-page-recall pain). No finding — noted as a strength. |

Integrity exemplary: confidence + reasoning (I4), cell-count denominator vs. corpus total (I2), source Papers and verbatim Evidence widened to paragraph inline (I3).

**Human screenshot pass:** H8 — a long unfiltered list of bordered cards each with evidence blockquotes; check scannability and whether status is visible at a glance.

---

### Unanswered Limitations (`limitations.py`, `app.py` `render_limitations`)

| Heuristic | Location | Severity | Finding / suggested fix |
| --- | --- | --- | --- |
| H4 | page vs. Meta-Analysis graph 5 | 2 | The same limitation-follow-up data appears here as a list and on Meta-Analysis as a Sankey/bar. Two homes for one concept risks inconsistent reads; confirm they reconcile and cross-reference. |
| H2 | "Unanswered Limitations" | 1 | Clear enough; minor — a reader may not know "addressed by a later Paper" means *within this Corpus* until reading the caption. |

Integrity strong: summary denominator (groups over N Papers, unanswered/addressed), verbatim source passages, follow-up counts with the later-Paper denominator.

**Human screenshot pass:** none critical; list view.

---

### Retrieval Gaps (`retrieval.py`, `app.py` `render_retrieval_gaps`)

| Heuristic | Location | Severity | Finding / suggested fix |
| --- | --- | --- | --- |
| H9 | dead-end (no action) | 2 | The page suggests likely-missing Papers but offers no way to act — adding one means leaving for the terminal and re-running the whole pipeline (pain point #5). No in-app "add to Corpus". (Target UX "Grow" — needs the runner/ingest ADR.) |
| I1 | `RETRIEVAL_SUMMARY` | 2 | States the coupled/corpus denominator and min-overlap well; but the source ("via OpenAlex") and the fact that only cited-by-Corpus works are considered could be clearer as a scope caveat. |

Integrity exemplary on separation: a loud warning that these are **not** evidence-linked gaps, carry no in-Corpus Evidence, and are candidates for expanding the search (I4, the out-of-Corpus labelling).

**Human screenshot pass:** H8 — confirm the warning banner reads as a persistent frame, not a one-time alert a reader scrolls past.

---

### Paper Explainer (`explainer.py`, `app.py` `_render_explainer_page`)

| Heuristic | Location | Severity | Finding / suggested fix |
| --- | --- | --- | --- |
| I7 | domain/lay explanation | 2 | The explanations are LLM-generated and grounded, but the view shows no model/prompt-version/cache provenance to the reader, so a shown explanation isn't visibly reproducible. (Recorded in the artifact per `FUNCTIONALITY.md`; just not surfaced.) |
| I3 | explanation grounding | 2 | Unlike gap cards and Verify, the explainer shows no Evidence passages behind its sentences — it's grounded in the Paper's text at generation but the reader can't trace a given sentence to the source text. |
| H6 | paper picker | 1 | Good: a citation-network node click preselects the Paper here (recognition over recall). No finding — strength. |

**Human screenshot pass:** none critical.

---

### Paper Comparison (`comparison.py`, `app.py` `_render_comparison_page`, `render_comparison`)

| Heuristic | Location | Severity | Finding / suggested fix |
| --- | --- | --- | --- |
| H8 | `_render_field_comparison` columns | 2 | Up to 15 Papers become up to 15 narrow `st.columns`; past ~4 the columns are unreadable. The 6–15 chunk-and-summarize path mitigates data volume but still renders many columns. |
| I7 | chunk summaries | 2 | For 6–15 Papers the compared text is LLM-summarized per set (`COMPARISON_CHUNK_NOTICE` says so, good), but the summary's provenance isn't shown and the reader compares summaries, not source statements, without an easy path back to the verbatim fact. |
| H2 | "mini coverage matrix" | 1 | Framed as blind spots with a denominator (good); the "mini coverage matrix" label may not land — the caption carries the real meaning. |

Integrity good: blind-spots denominator (I2), agreements/differences from shared normalized categories.

**Human screenshot pass:** H8 — render 8–15 Papers and check column legibility; this is the most likely layout break.

---

### Conversational Analytics (`analytics.py` + `conversations.py` + `grounding.py`, `app.py` `_render_analytics_page`)

| Heuristic | Location | Severity | Finding / suggested fix |
| --- | --- | --- | --- |
| I2 | `_render_narrative_summary` | 3 | The narrative summary is built from **all** candidate gaps (`_grounding_context` loads every gap), not the researcher's **accepted** ones, and it never states which/how-many gaps it covers. A "gaps in the literature" write-up should reflect the verdicts and state its denominator (pain point #6; `journeys.md` "Write"). |
| H9 | `ANALYTICS_NO_KEY` | 2 | Degrades to a clear message when no API key (good, not a crash), but the message points to `.env.example` and a reload — another environment hand-off rather than in-app setup. |
| H1 | chat / summary generation | 2 | A spinner shows during generation (good), but long generations give no progress or token/cost feedback; a reader can't tell a slow answer from a stalled one. |
| I5 | grounding flags | 1 | Exemplary: ungrounded citations are dropped and the drop is reported (`_render_grounding_flags`). No finding — strength, recorded. |

**Human screenshot pass:** H8 — the page stacks conversation picker + thread + narrative summary + journal with dividers; check it isn't an overwhelming single scroll.

---

### Verify Extractions (`verify.py`, `extraction_review.py`, `app.py` `_render_verify_page`)

| Heuristic | Location | Severity | Finding / suggested fix |
| --- | --- | --- | --- |
| H9 | `VERIFY_NO_PARSED_TEXT`, `VERIFY_NO_EXTRACTIONS` | 3 | Both guards send the reader to the terminal (`Run \`parse\``, `Run \`extract\` first`). This is the trust gate — the researcher most needs to act here and can't, in-app. |
| H5 | approve/flag/remove buttons | 2 | Four verbs per fact as adjacent buttons, no confirm on "Remove (hallucinated)"; a misclick removes a fact (recoverable via Clear, but not obviously). |
| H1 | review progress | 2 | The `VERIFY_SUMMARY` shows reviewed/extracted/added counts per Paper (good), but there's no corpus-wide "how much of the Corpus is verified" to tell the researcher when the trust step is done. |
| I3, I7 | edit/add forms | 1 | Strong: edits and added facts are refused unless the Evidence quotes the parsed text verbatim (`UngroundedEvidenceError`). No finding — strength, recorded. |

**Human screenshot pass:** H8 — a Paper with many facts × four buttons each; check the wall-of-buttons density.

---

### Needs Attention (`attention.py`, `app.py` `render_attention`)

| Heuristic | Location | Severity | Finding / suggested fix |
| --- | --- | --- | --- |
| H9 | `ATTENTION_CORRECTION_HELP`, `ATTENTION_CORRECTION_SAVED` | 3 | The correction box accepts pasted text (good) but then says `Run \`apply-parse-corrections\` then \`extract\``. The fix does nothing in-app until three CLI commands run (pain point #4). The highest-friction seam: the researcher does the work, then must leave to apply it. |
| H2 | `ATTENTION_NO_PIPELINE` | 1 | "Run `parse` and `extract` first" uses artifact/stage names; fine for the CLI user, opaque for the researcher persona. |
| H1 | banner reconciliation | 1 | Good: the page and the global banner share `build_attention`, so the denominator reconciles (I6). No finding — strength, recorded. |

**Human screenshot pass:** none critical.

---

## Severity rollup

**Severity 4 (must fix before release):** none found. The integrity floor holds.

**Severity 3 (major, high priority):**

1. **Flat 10-page navigation with no task order** (shell, H8/H4) — the structural root; everything else is reached through it.
2. **Trust gates (Verify, Needs Attention) ordered last** (shell, H2/H4) — the checks that decide trust sit after the pages that assume it.
3. **No in-app pipeline status** (shell, H1) — the researcher can't see what's been run; discovers it via guard messages. *(Blocked on the runner ADR.)*
4. **Scope "what was searched" never recorded** (Overview/shell, I1) — query, sources, dates absent.
5. **Verify guards hand off to the terminal** (Verify, H9) — the trust step is un-actionable in-app.
6. **Needs-Attention fix is a terminal round-trip** (Needs Attention, H9) — paste, then run three CLI commands.
7. **Narrative summary ignores verdicts and omits its denominator** (Analytics, I2) — a write-up over all gaps, not accepted ones. *(Cheap: read `judgments/`.)*

**Recurring theme across severity-3 and -2:** the **H9 terminal hand-off** appears in Verify, Needs Attention, Retrieval, Comparison setup, Gap Cards, Meta-Analysis, Explainer, Analytics, and Overview guard states — the single most pervasive usability defect, and the one the target UX's "Build" and inline-fix steps are designed to remove. It is gated on the runner/upload ADR (`docs/journeys.md`, `docs/HANDOFF.md`).

**Severity 2 (minor):** per-card/per-fact triage lacks accelerators and filters (H7, Gap Cards/Verify); Comparison columns break past ~4 Papers (H8); explainer and comparison lack visible provenance/traceability (I3/I7); Retrieval and chat dead-end without an in-app next action (H9).

**Severity 1 (cosmetic):** jargon labels in a few page/tile titles (H2); missing per-tile "what this answers" hints (H10).

## What feeds the next steps

- **Chart audit (step 3):** the H8/colour flags on Meta-Analysis network graphs, the Trends line chart, and the Overview tile shades, plus `scientific-visualization`'s truthfulness and colour-blind checks.
- **Prototype (step 4):** the severity-3 navigation findings (1, 2) are the thing a guided-flow mock-up should answer.
- **Runner/upload ADR (step 5):** findings 3, 5, 6 and the pervasive H9 theme are all gated on it.
- **Wayfinder (step 6):** this rollup is the "today" evidence against the `docs/journeys.md` target.
