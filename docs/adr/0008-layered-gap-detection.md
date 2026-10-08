# 0008: Layered gap detection with confidence-first surfacing

## Status

Accepted (refines the detect stage; promotes `docs/BRIEF.md` decision 0 out of
v2). The algorithm details were settled by
`docs/research/gap-detection-layers.md` (research ticket #60); four of its
answers refined this ADR's original wording and were approved by the user
(L2 roll-up, two-witness boost-only, a shared embedding capability, L5 scope).

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
     confidence with the one-sided hypergeometric (Fisher exact) lower-tail
     probability `P(X ≤ observed)` on the cell's 2×2 table. It is a ranking
     score, not a significance test: no multiplicity correction, and no card
     claims significance. Starting level: ≤ 0.05 High, ≤ 0.20 Medium, else
     Low. Lift/PMI and chi-square are rejected (they cannot rank empty cells /
     are invalid at our expected counts). Deterministic.
   - **L2: taxonomy roll-up.** Uses the taxonomies' `parent` hierarchy
     (derivable from MeSH tree numbers; related to #56). A same-axis cell
     between an ancestor and its descendant is **rejected** as a taxonomy
     artifact. A child-level cell whose parent-level cell holds a Paper placed
     *only* on the parent is **downgraded one level** (that Paper may or may
     not cover the child). A parent cell filled only through sibling children
     has no effect. Deterministic.
   - **L5: LLM pseudo-gap rejection and self-critical review.** Reject the
     five pseudo-gap classes (generic upgrade, template reuse, pseudo-firstness,
     non-closable, low-value replication) and challenge each survivor against
     its Evidence, plus two Corpus-specific classes (taxonomy artifact, little
     meaning; after Mine the Gap). Runs only on cell candidates at Medium or
     High after the deterministic layers, and on Unanswered Limitations. It
     keeps or rejects and may lower confidence, never raise it. 3-shot prompt
     with synthetic examples; Grounds cite only the given Evidence ids, so ADR
     0004 verification applies. Cached by inputs with model and prompt version
     recorded (ADR 0001), so reruns are free and reproducible.
   - **L3: semantic near-coverage is not adopted.** Near-misses between
     categories are a vocabulary problem, fixed at the source by ADR 0010.
     **L4: duplicate merging** for cell gaps is handled by L2; for limitation
     statements it uses the shared embedding capability below.
   - **Shared embedding capability.** One local open-source model
     (`NeuML/pubmedbert-base-embeddings` via `sentence-transformers`) as an
     optional extra, with recorded vectors in tests. It shortlists cross-Paper
     limitation merges (#54) and two-witness matches, and serves chat
     retrieval (#97). Without it, grouping is LLM-only and chat uses keywords.
   - **Bridges (ABC/AnC).** For an empty A×C cell, a bridge B is a category on
     any axis with A–B and B–C each supported by ≥ 2 Papers, excluding A, C,
     their ancestors/descendants and near-universal categories (≥ 90 % of
     Papers). ≥ 2 bridges → +1 level. Bridges are ordered by Adamic–Adar weight
     in Audit Basis and Tier 3. Deterministic.
   - **Two-witness intersection.** One cached LLM pass tags each limitation
     group with a limitation-type bucket (study design, population,
     setting/data, sample size, measurement, follow-up, analysis, open
     question, other) and the observed categories it concerns; intersection
     with cells is deterministic. A corroborated cell gains +1 level and the
     label "also flagged by authors". The layer is **boost-only**: an empty
     cell nobody mentions is not downgraded, only noted in Tier 3. Depends on
     cross-Paper limitation grouping (#54).
   - **Combination.** Start at the L1 level, apply L2, bridges and two
     witnesses, clamp to Low…High, then L5 (Medium+ only). Each step records
     its layer and reason.

   Because Corpora are small (10–75 Papers), L1 has too little power to gate
   on its own and is used to **rank**. The corroborating layers (bridges, two
   witnesses, L5) decide what surfaces (`docs/research/gap-detection-landscape.md`).
2. **Confidence-first surfacing.** The sparse threshold stays a wide
   candidate-generation net, and confidence plus the layers decide what
   surfaces. The net is one rule at every Corpus size: a cell is a candidate
   if it is empty, or holds one Paper while more than one was expected. The
   25-Paper switch (`SPARSE_CORPUS_SIZE`) retires.
3. **Actionable cards.** A surviving gap carries **Audit Basis** (what the
   Corpus covers around the gap) and **Minimal Study** (the smallest study that
   would close it), produced by the L5 pass and grounded in Corpus Evidence
   (so Low-confidence cards, which skip L5, carry neither).
   L5 emits a TABI-style structure (GAPMAP): **Claim** (the gap), **Grounds**
   (Evidence passages), **Warrant** (one inspectable reasoning sentence),
   **Bucket** (confidence), using few-shot prompts, since zero-shot output tends
   to be vague. Minimal Study is shown as a **suggestion**: a real gap does not
   imply a feasible study.
4. **Digestible cards: three tiers, deterministic at view time.** Tier 1
   (headline: statement, confidence, one-line why, cell count) → Tier 2
   (Evidence, Audit Basis, Minimal Study) → Tier 3 ("show the data": the Papers
   in each category, neighbouring cells, the L1 score, and any
   downgrade/rejection reason). The L1 score is phrased as expected vs
   observed counts and labelled a ranking score, and the gap list states the
   smallest category sizes that could rate High at this Corpus's size. No LLM
   call at view time. The scoped "Discuss"
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
- A third LLM use (limitation tagging for two witnesses) is one batched,
  cached pass per Corpus.
- An optional `embeddings` extra adds `sentence-transformers` and a ~438 MB
  model download; `torch` is already pulled in by the `pdf` extra.
- The candidate-gaps artifact schema grows: a per-candidate layer verdict and
  reason, Audit Basis, Minimal Study, and the L1 score. The dashboard read
  models follow.
- Conversational Analytics and the narrative summary should consume only
  surfaced gaps, not the raw candidate set.
- Credit to aipoch/medical-research-skills (MIT) for the pseudo-gap classes and
  card fields belongs in README acknowledgments, never in product output.
