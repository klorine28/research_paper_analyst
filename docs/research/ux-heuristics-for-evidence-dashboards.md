# UX heuristics for an evidence dashboard — the audit checklist

Question: against what checklist should we audit the Research Gap Dashboard's
UI? This note fuses two sources into one checklist that later steps (the UI
audit and the chart audit) score each page against:

1. **Nielsen's 10 usability heuristics** — the field's standard set of
   general-purpose heuristics for interactive systems.
2. **This project's research-integrity rules** — the non-negotiable rules that
   make an *evidence* dashboard trustworthy, not just usable. They are not
   optional UX niceties here; they are product requirements.

The split matters: Nielsen's 10 ask "is this usable?"; the integrity rules ask
"can a researcher *act* on what this says?". A page can pass the first and fail
the second, which for this tool is still a failure.

## Sources

- Jakob Nielsen, *10 Usability Heuristics for User Interface Design*, Nielsen
  Norman Group, first published 1994-04-24, updated 2024-01-30.
  <https://www.nngroup.com/articles/ten-usability-heuristics/> — the canonical
  statement of the ten, with each heuristic's own NN/g explainer article.
- Jakob Nielsen, *Enhancing the Explanatory Power of Usability Heuristics*,
  Proc. CHI '94, ACM, 1994 — the empirical derivation of the set from a factor
  analysis of 249 usability problems.
- Jakob Nielsen, *How to Conduct a Heuristic Evaluation*, NN/g, 1994, updated
  2025. <https://www.nngroup.com/articles/how-to-conduct-a-heuristic-evaluation/>
  — the evaluation method (3–5 evaluators, severity ratings) this audit adapts
  to a single-evaluator pass.
- Jakob Nielsen, *Severity Ratings for Usability Problems*, NN/g, 1995.
  <https://www.nngroup.com/articles/how-to-rate-the-severity-of-usability-problems/>
  — the 0–4 severity scale used for scoring below.
- This repo: `CODING_STANDARDS.md` > *Research integrity*; `CONTEXT.md`
  (Corpus, Evidence, Candidate Gap, Gap Type); `docs/BRIEF.md` (scope
  statement, "states its own scope"); `docs/journeys.md` (the target UX and the
  six pain points this audit must confirm or refute).

## Part A — Nielsen's 10 (NN/g 2024 wording, with this dashboard's reading)

Each heuristic is stated in NN/g's terms, then given a concrete reading for an
evidence dashboard so the audit has something testable to look for.

1. **Visibility of system status.** The system keeps users informed through
   timely feedback. *Here:* pipeline/stage state, which Corpus is loaded, how
   many Papers, loading/empty states, and whether a saved verdict took effect.
2. **Match between the system and the real world.** Speak the users'
   language, follow real-world conventions. *Here:* use the `CONTEXT.md`
   glossary terms (Corpus, Candidate Gap, Evidence, Gap Type) consistently;
   no internal artifact names (`normalized_facts.json`) leaking into UI text.
3. **User control and freedom.** Clearly marked "emergency exits"; undo/redo.
   *Here:* can a researcher undo an accept/reject, change a verdict, leave a
   page mid-task without losing work, clear a filter?
4. **Consistency and standards.** Follow platform and internal conventions;
   same word = same thing. *Here:* consistent gap-card layout, consistent
   placement of Evidence, consistent chart legends across pages.
5. **Error prevention.** Prevent problems before they occur; confirm
   destructive actions. *Here:* guard against analysing a corpus that hasn't
   been built, rejecting a gap by misclick, uploading an unmatched PDF.
6. **Recognition rather than recall.** Make elements visible; don't force
   users to remember across screens. *Here:* don't make the researcher
   remember a gap ID while hopping to Comparison; surface Evidence inline
   instead of behind a jump (this is pain point #3/#6 in `journeys.md`).
7. **Flexibility and efficiency of use.** Accelerators for experts, tailorable
   frequent actions. *Here:* keyboard/quick triage of gaps, remembering the
   last Corpus, deep links.
8. **Aesthetic and minimalist design.** No irrelevant or rarely needed
   information competing with the relevant. *Here:* 10 equally-weighted flat
   pages (pain point #3) is the prime suspect; the signal (gaps + evidence)
   should dominate.
9. **Help users recognize, diagnose, and recover from errors.** Plain-language
   error messages, precise problem, constructive fix. *Here:* "run `detect`
   first" style messages (pain point #2/#4) — do they tell the researcher what
   to do *in* the tool, or dump them to a terminal?
10. **Help and documentation.** Easy to search, task-focused, concrete steps.
    *Here:* is there in-context guidance on what a Gap Type means, what the
    denominator covers, what to do next?

## Part B — Research-integrity heuristics (project-specific, mandatory)

These come straight from `CODING_STANDARDS.md` > Research integrity and the
Brief. They are the rules an evidence dashboard must pass even if it is already
"usable" by Part A. Failing any of these is a **severity 3–4** finding by
default, because a researcher could act on a false claim.

- **I1 · State the scope.** Every analysis shows what Corpus it ran over and,
  per the Brief, what was searched/excluded. The reader must never have to
  guess the dashboard's scope. (Brief: "states its own scope".)
- **I2 · Show the denominator.** Every count, chart, or statistic states what
  it was computed over: how many Papers, which sources, which years. A chart
  without its denominator is a defect, not a style issue.
- **I3 · Every claim is traceable to its Evidence.** Any Candidate Gap,
  statistic, or generated sentence links to the Papers and Evidence passages it
  came from, reachable from where the claim is shown — ideally inline.
- **I4 · Candidates, not verdicts.** Gaps are shown as candidates with a
  confidence level and the reasoning, never worded as proven fact.
- **I5 · Cite only Corpus Papers.** Generated/narrative text cites only Papers
  in the Corpus; no invented citations, no citing the tool's own sources.
  (Also: never cite the skill-collection's paper — AGENTS.md.)
- **I6 · Nothing dropped silently.** Papers that fell out (parse failure, zero
  verified facts, dropped facts) are surfaced, not hidden — the denominator and
  Needs Attention must reconcile.
- **I7 · Reproducibility is visible.** Where a result came from an LLM/API
  call, the model name / prompt version / cache status is recoverable, so a
  shown number is defensibly reproducible.

## Scoring scheme (per page, per heuristic)

Adapt NN/g's heuristic-evaluation method to a single-evaluator pass. For each
page, walk all 10 Part-A heuristics and all 7 Part-B rules, and record each
problem found with:

- **Heuristic violated** (e.g. "H8" or "I2").
- **Location** (page + element).
- **Severity (NN/g 0–4 scale):**
  - 0 — not a usability problem
  - 1 — cosmetic; fix if time permits
  - 2 — minor; low priority
  - 3 — major; high priority to fix
  - 4 — catastrophe; must fix before release
- **Note / suggested fix.**

Rule of thumb for this project: any violation of a Part-B integrity rule
(I1–I7) starts at severity 3 and is only lowered with a reason, because the
cost of a researcher acting on an untraceable or mis-scoped claim is high.

## How later steps use this

- **UI audit (step 2):** score each of the dashboard's pages against Parts A
  and B, one findings table per page, in `docs/research/`.
- **Chart audit (step 3):** the Coverage heatmap, Meta-Analysis graphs and
  Trends get Part-B I2 (denominator) and I3 (traceability) plus the
  `scientific-visualization` skill's truthfulness and colour-blind-safety
  checks layered on top.
- **Wayfinder (step 6):** audit findings become evidence for the route from
  today's UX to the target in `docs/journeys.md`.
