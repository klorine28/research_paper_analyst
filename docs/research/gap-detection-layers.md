# Research task: gap-detection layers (open)

**Status:** open. This note gates ADR 0008 (Proposed → Accepted). Run it with
`/skill:research` before implementing any layer.

## Questions to answer, each with a recommendation and sources

1. **L1: which statistical test?** Compare Fisher's exact test, lift/PMI and
   chi-square for scoring a Coverage Matrix cell's co-occurrence against
   chance, at Corpus sizes of 10–75 Papers (small counts, many zero cells).
   Cover: multiple-comparison handling across hundreds of cells, mapping a
   score onto High/Medium/Low, and how a 0-count cell between two rare
   categories should score. Primary sources: statistics references; the
   `statistical-analysis` skill.
2. **L2: taxonomy roll-up feasibility.** Do the shipped taxonomies
   (`taxonomies/`) carry, or could they cheaply carry, a parent/child hierarchy?
   What is the rule when a child cell is empty but its parent cell is filled?
   Related: #56 (narrow Method taxonomy).
3. **L3/L4: embeddings for near-coverage and duplicate merging.** Is a local
   FOSS embedding model (e.g. sentence-transformers) good enough on biomedical
   text, at what install and runtime cost, versus an API? It must work offline
   in tests (recorded vectors). Would it also serve chat retrieval (the
   Conversational Analytics epic)?
4. **Sparse threshold (BRIEF decision -1).** With confidence filtering in place,
   does the provisional threshold (empty cells only under 25 Papers;
   empty-or-single at 25+) still matter, and what should it be?
5. **L5 prompt design.** Turn the AIPOCH pseudo-gap classes, Mine the Gap's
   non-gap types (indexing byproducts, little meaning) and the confidence rubric
   into a reviewable **few-shot TABI prompt** (Claim/Grounds/Warrant/Bucket),
   plus a recorded-response fixture for tests. Check GAPMAP's full paper for
   the unverified figures before relying on them.
6. **Small-N power.** How should L1 rank cells when expected counts are far
   below Mine the Gap's ≥10 rule, and how is that caveat shown to the
   reader?
7. **Bridges.** Over a 10–75-Paper matrix, is ≥2 bridges (AnC) the right
   bar? Should bridges be weighted by their own coverage?
8. **Two-witness intersection.** How is a limitation group matched to a cell's
   categories (via its normalized terms, or an LLM judgment)? What boost or
   downgrade does each outcome earn? Which limitation-type buckets fit clinical
   literature?

## Output

A recommendation per question, then update ADR 0008: fill in the *pending
research* items and set Status: Accepted.
