# 0009: Saturation check: trying to disprove gaps against the wider literature

## Status

Accepted (completes ADR 0008 decision 7; builds on ADR 0002, 0006, 0007)

## Context

Every Candidate Gap is judged inside a Corpus of 10–75 Papers. A cell that is
empty in the Corpus may be well studied in the wider literature, and the most
expensive mistake a researcher can make is building on a gap that is already
filled. Agentic tools (e.g. Paperguide) address this with an adversarial step:
search for work that would close the gap
(`docs/research/gap-detection-landscape.md`). Doing this means looking outside
the Corpus, which every integrity rule so far has fenced off. The source
adapters could only resolve a known DOI or OpenAlex id; neither could search by
topic.

## Decision

1. **The check can only weaken a gap, never confirm it.** Hits mean "possibly
   addressed: review these". No hits is shown as a dated record of what was
   tried ("no closing work found in OpenAlex + PubMed for queries … on
   2026-10-04"), never as "gap confirmed". Wording stays "candidates, not
   verdicts".
2. **Hybrid trigger.** High-confidence gaps are checked automatically after
   `detect`. All others are checked on demand from the card, and "check all
   accepted" checks every accepted gap. Automatic checks are capped at 20 per
   run (strongest first, the rest logged as left for on-demand), cached by
   query and source with a 30-day freshness window, can be switched off, and
   identify themselves to the APIs (OpenAlex polite pool `mailto`, PubMed API
   key).
3. **Sources: OpenAlex and PubMed**, through a new `search` capability on the
   existing source-adapter seam. More sources plug in later through the same
   seam.
4. **Hits are out-of-Corpus candidates.** They are labelled like Retrieval
   Gaps, are never cited in generated text, and never count as Evidence. A hit
   can be added to the Corpus through the Grow path (ADR 0007). Once it is in,
   it is a Paper like any other, and `detect` shows the gap as filled through
   the normal pipeline.
5. **A pipeline command, run from the UI.** `saturate <corpus>` (`--auto`,
   `--gap <id>`, `--accepted`) writes `artifacts/saturation_checks.json`. The
   pipeline status panel chains `saturate --auto` after `detect`, and the card
   buttons invoke it through the ADR 0007 runner. `detect` stays free of
   literature-search calls. The dashboard reads the artifact; it never calls a
   scholarly API itself.
6. **Annotate, don't downgrade.** A result adds a badge; confidence is
   unchanged and the researcher decides. Automatic downgrading is reconsidered
   once screening has proven reliable.
7. **Queries from controlled vocabulary.** The base queries come from taxonomy
   labels and aliases, the Corpus's own mapped terms, and MeSH descriptors for
   PubMed (ADR 0010). Only when the base finds nothing does an LLM generate
   query variants, labelled as the adversarial second attempt. Every query is
   recorded verbatim.
8. **Screening.** A deterministic title/abstract check counts a hit only if
   both concepts appear there. An LLM then screens the top ~10 hits per gap
   (addresses / partial / no, with a one-line reason). Every hit and its
   verdict are kept and shown.
9. **Dated, append-only snapshots.** Each check records the source, exact
   queries, date, returned work ids and screening verdicts. The dashboard shows
   the snapshot with its date. A manual re-check always queries live and adds a
   new entry; the 30-day cache governs automatic re-checks only.
10. **Gap types checked:** Knowledge, Coverage and Unanswered Limitation (for a
    limitation, the question is whether anyone outside the Corpus followed it
    up). Retrieval Gaps are already out-of-Corpus and are not checked.
11. **One Grow path.** A hit that is also a Retrieval Gap candidate is marked
    in both places ("also cited by 6 Corpus Papers"). "Add to Corpus" goes
    through one path, so a paper is never added twice.
12. **PDF acquisition.** Adding a suggested paper fetches its open-access PDF
    when one exists. Otherwise the user uploads it. If neither is possible, the
    user marks it **unavailable**: recorded with reason and date, no longer
    re-suggested (kept in a collapsed list), disclosed in scope on Overview
    ("2 relevant Papers could not be included"), and reversible by uploading
    later. A paper is never added metadata-only: without full text it cannot
    carry Evidence. Until its PDF arrives it is "pending" and outside every
    denominator.
13. **Cost rule.** Deterministic first; the LLM only where deterministic
    methods cannot decide (query variants on zero hits, screening of top hits,
    alias suggestions for thin categories); every call cached by inputs. Cost
    is **out of the way by default**: none on Overview, cards or the gap list.
    It sits in a collapsed "Run details" expander in the status panel
    (estimate before, actual after). A run asks for confirmation only when its
    estimate exceeds a configurable threshold (`LLM_COST_CONFIRM_ABOVE`).
14. **Display.** Tier 1: a badge (not checked / no closing work found, with
    date / possibly addressed, with N screened hits). Tier 2: a one-paragraph
    result. Tier 3: exact queries, hits with verdicts, check history, and
    actions (re-check, add to Corpus, mark unavailable). The gap list gets a
    "possibly addressed" filter next to the confidence toggle.
15. **Drafts are never hits.** A researcher's own unpublished draft (BRIEF
    item 8) is excluded from saturation results and denominators. "Does my
    draft close this gap?" belongs to the candidate-contribution design.

## Consequences

- Researchers get a record of attempts to close each gap they rely on: a
  pre-commitment check they can show a supervisor.
- `saturate` makes network and LLM calls in a normal chained run. The cap,
  cache, opt-out, offline handling and cost threshold keep that bounded. When
  offline or rate-limited it completes and marks checks "not run (reason)",
  never failing the chain (ADR 0006's "surface, don't block").
- Results are time-dependent by nature. Dated snapshots keep the dashboard
  reproducible: the same artifact always shows the same results.
- Tests: adapter `search` uses a fake fetch with recorded responses; query
  variants and screening use recorded completions; a fixture Corpus with a
  known-filled gap proves the check finds it. `just test` makes no network or
  paid call. Tickets close only after a live end-to-end check on a real Corpus.
- "Possibly addressed" depends on vocabulary coverage; ADR 0010 is the main
  guard against false "no closing work found" results.
