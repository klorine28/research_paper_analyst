# 0008: Layered gap detection with confidence-first surfacing

## Status

Proposed (refines the detect stage; promotes `docs/BRIEF.md` decision 0 out of
v2). Moves to Accepted once `docs/research/gap-detection-layers.md` settles the
algorithm details marked *pending research* below.

## Context

`detect` cards every Coverage Matrix cell at or under the sparse threshold and
grades each by a statistical-surprise heuristic (an empty cell between two
well-covered categories scores higher). Nothing is suppressed. On
`takotsubo-15` this yields 301 Candidate Gaps (106 Knowledge, 181 Coverage, 14
Unanswered Limitation), most of them low-confidence empty cells. The UI can
only organise this volume, not make it meaningful. The AIPOCH analysis
(`docs/research/aipoch-gap-finder-analysis.md`) had deferred pseudo-gap
rejection, a confidence rubric, a self-critical review and richer card fields
to v2. Detection noise is now the main limit on the product's usefulness, so
that deferral is reversed.

## Decision

1. **Layered filtering, cheapest first.** Candidates pass through stacked
   layers. Each layer may downgrade or reject a candidate and must record why:
   - **L1: statistical expected-vs-observed test.** Replace the heuristic
     confidence with a principled co-occurrence test on the cell's 2×2 table
     (Fisher's exact test or lift/PMI; *pending research*). Deterministic.
   - **L2: taxonomy roll-up.** Reject a child-level gap that is filled at
     parent level, where taxonomies carry a hierarchy (*feasibility pending
     research*; related to #56). Deterministic.
   - **L5: LLM pseudo-gap rejection and self-critical review.** Reject the
     five pseudo-gap classes (generic upgrade, template reuse, pseudo-firstness,
     non-closable, low-value replication) and challenge each survivor against
     its Evidence. Cached by inputs with model and prompt version recorded (ADR
     0001), so reruns are free and reproducible.
   - **L3/L4: semantic near-coverage and duplicate merging** via embeddings are
     *not* adopted yet. They add a model dependency and wait on the research
     note.
   - **Bridges (ABC/AnC).** For an empty A×C cell, find the B categories
     linked to both. ≥2 bridges (AnC) supports higher confidence, and the
     bridges go into Audit Basis and Tier 3. Deterministic.
   - **Two-witness intersection.** A cell gap that limitation groups also
     point at is corroborated and boosted; an empty cell nobody mentions is
     downgraded. Depends on cross-Paper limitation grouping (#54) and on
     limitation-type buckets.

   Because Corpora are small (10–75 Papers), L1 has too little power to gate
   on its own and is used to **rank**. The corroborating layers (bridges, two
   witnesses, L5) decide what surfaces (`docs/research/gap-detection-landscape.md`).
2. **Confidence-first surfacing.** The sparse threshold stays a wide
   candidate-generation net, and confidence plus the layers decide what
   surfaces. Tuning the threshold numbers is a research item, not part of this
   decision.
3. **Actionable cards.** A surviving gap carries **Audit Basis** (what the
   Corpus covers around the gap) and **Minimal Study** (the smallest study that
   would close it), produced by the L5 pass and grounded in Corpus Evidence.
   L5 emits a TABI-style structure (GAPMAP): **Claim** (the gap), **Grounds**
   (Evidence passages), **Warrant** (one inspectable reasoning sentence),
   **Bucket** (confidence), using few-shot prompts, since zero-shot output tends
   to be vague. Minimal Study is shown as a **suggestion**: a real gap does not
   imply a feasible study.
4. **Digestible cards: three tiers, deterministic at view time.** Tier 1
   (headline: statement, confidence, one-line why, cell count) → Tier 2
   (Evidence, Audit Basis, Minimal Study) → Tier 3 ("show the data": the Papers
   in each category, neighbouring cells, the L1 score, and any
   downgrade/rejection reason). No LLM call at view time. The scoped "Discuss"
   chat stays available on every card.
5. **Demote, never delete.** Rejected and downgraded candidates stay in the
   artifact with their layer and reason. The dashboard lists them in a
   collapsed "Rejected by review (N)" section, and a researcher can still
   accept one: the human verdict outranks the filter (ADR 0006's "surface,
   don't block").
6. **Confidence toggle.** The gap list filters by High / Medium / Low,
   defaulting to High + Medium, and always states how many gaps are hidden, so
   the denominator stays visible.

7. **Saturation check.** An outward-looking search that tries to disprove a
   gap against the wider literature: see
   `docs/adr/0009-saturation-check-against-wider-literature.md`. Vocabulary
   expansion, which detection and that check both depend on, is in
   `docs/adr/0010-vocabulary-expansion-and-corpus-entry-log.md`.

## Consequences

- The surfaced gap list shrinks to the candidates that pass every layer. The
  full candidate set stays inspectable, so nothing is lost silently.
- `detect` gains a second LLM use (L5) beyond limitation grouping: more
  first-run cost, no rerun cost (cache).
- The candidate-gaps artifact schema grows: a per-candidate layer verdict and
  reason, Audit Basis, Minimal Study, and the L1 score. The dashboard read
  models follow.
- Conversational Analytics and the narrative summary should consume only
  surfaced gaps, not the raw candidate set.
- Credit to aipoch/medical-research-skills (MIT) for the pseudo-gap classes and
  card fields belongs in README acknowledgments, never in product output.
