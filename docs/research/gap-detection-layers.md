# Research: algorithms and thresholds for layered gap detection

**Status:** answered (2026-10). This note gates ADR 0008 (Proposed → Accepted)
and resolves the research ticket "Research: algorithms and thresholds for
layered gap detection" (#60).

Question: which algorithms and thresholds should ADR 0008's layers use at our
scale (10–75 Papers per Corpus)? Each answer below gives a recommendation, the
evidence, and its sources. Claims are **verified** against a primary source
unless marked otherwise. Numbers marked *measured* come from re-analysing the
committed artifacts of `corpora/takotsubo-15` (N = 10 Papers in the matrices)
and `corpora/takotsubo-50` (N = 45) with throwaway scripts; no product code was
changed.

## Summary of recommendations

| # | Question | Recommendation |
| --- | --- | --- |
| 1 | L1 test | One-sided **hypergeometric (Fisher) lower-tail probability** `P(X ≤ observed)`; no lift/PMI, no chi-square; no multiplicity correction because L1 ranks and never claims significance. Buckets: ≤ 0.05 High, ≤ 0.20 Medium, else Low. |
| 2 | L2 roll-up | Feasible now: `parent` exists in the schema and matches MeSH tree prefixes. Reject only **ancestor × descendant** cells (indexing byproduct). A child cell whose parent cell holds a Paper indexed **only at the parent** is downgraded, not rejected; one filled only by siblings is unaffected. *Refined ADR text; approved.* |
| 3 | Embeddings | `NeuML/pubmedbert-base-embeddings` (Apache-2.0) via `sentence-transformers`, as an optional extra with recorded vectors in tests. Serves limitation de-duplication (#54), the two-witness shortlist and chat retrieval (#97). **L3 near-coverage still not adopted**; cell-level L4 merging is L2's job. *Adopted; approved.* |
| 4 | Sparse threshold | Drop the 25-Paper switch. A cell is a candidate if it is **empty, or holds one Paper where more than one was expected**, at every Corpus size. |
| 5 | L5 prompt | Draft 3-shot TABI prompt below; run L5 only on candidates at **Medium or High after the deterministic layers**. GAPMAP's unverified figures are now verified (with caveats). |
| 6 | Small-N display | Plain-language "expected vs observed" on every card, the probability in Tier 3, and a Corpus-level line stating the smallest margins that could ever reach High at this N. |
| 7 | Bridges | Keep **≥ 2 bridges**, but a bridge needs **≥ 2 Papers on each link** and may not be a near-universal category (≥ 90 % of Papers). Raw common neighbours make the bar meaningless at our N. Adamic–Adar weighting orders bridges in Tier 3 only. |
| 8 | Two witnesses | Tag each limitation group with a **limitation-type bucket and category ids in one cached LLM pass**, then intersect deterministically. Lexical matching found zero matches on real data. Corroboration boosts one level; **absence should not downgrade**. *Changed ADR text; approved.* |

The combined confidence rule that falls out of 1, 7 and 8 is in
[Combining the layers](#combining-the-layers).

## 1. L1: which statistical test?

**Recommendation.** For each candidate cell, compute the one-sided lower-tail
hypergeometric probability `p = P(X ≤ observed)`, where `X` is the number of
Papers in both categories if the two categories were placed independently with
their observed margins (`a` row Papers, `b` column Papers, `N` Papers). This is
the one-sided p-value of Fisher's exact test on the cell's 2×2 table. For an
empty cell it is `C(N−a, b) / C(N, b)`. It needs no new dependency
(`math.comb`). Map it onto buckets: **High ≤ 0.05, Medium ≤ 0.20, Low
otherwise**, and use it to **rank, never to gate**.

**Why not lift/PMI.** PMI is `log P(a,c) / (P(a)P(c))` (Church & Hanks 1990).
For an empty cell `P(a,c) = 0`, so PMI is −∞ (and normalised PMI is −1) for
*every* empty cell: it cannot rank the cells we care about. Lift is 0 for all of
them, for the same reason.

**Why not chi-square.** The usual validity guideline for the chi-square
contingency test is that observed and expected counts are at least 5 in every
cell (SciPy 1.18 `chi2_contingency` notes, which repeat the classical guidance
traced to Cochran 1954). *Measured:* the largest expected count of any empty
cell is 1.2 at N = 10 and 1.56 at N = 45, so the approximation never applies.
Fisher's test conditions on the margins and is exact (SciPy `fisher_exact`).

**Multiple comparisons.** Don't correct, and don't call anything
"significant". *Measured:* the smallest attainable p of any empty cell is 0.17
(N = 10) and 0.067 (N = 45); not one candidate reaches 0.05 in either Corpus,
before any correction. Bonferroni over hundreds of cells, or
Benjamini–Hochberg (1995), would demand thresholds near 10⁻⁴ that a discrete
test on these margins cannot reach. Tarone (1990) makes the same point
formally: with discrete statistics, tests whose margins cannot produce a small
enough p contribute nothing and can be dropped from the correction; at our N
that is nearly all of them. So L1 is a **descriptive surprise score**: the
probability is shown to the reader and drives the ranking, and no card claims
statistical significance.

**How a zero cell between two rare categories scores.** Correctly low:
`a = b = 1` at N = 10 gives p = 0.9, so it lands at the bottom of the ranking.
*Measured:* the median candidate's p is 0.80 (N = 10) and 0.93 (N = 45),
because most categories cover one or two Papers.

**Bucket sanity check.** An empty cell reaches High only when both margins
reach about √(3N) (expected count ≈ 2.6):

| N | equal margins for p ≤ 0.05 (High) | equal margins for p ≤ 0.20 (Medium) |
| --- | --- | --- |
| 10 | 5 each (expected 2.5) | 4 each (expected 1.6) |
| 25 | 8 each (expected 2.6) | 6 each (expected 1.4) |
| 45 | 11 each (expected 2.7) | 8 each (expected 1.4) |
| 75 | 14 each (expected 2.6) | 11 each (expected 1.6) |

That matches the current heuristic's spirit (expected ≥ 2 → High), but it is
exact and also handles single-Paper cells (`P(X ≤ 1)`) without a separate
"downgrade one level" rule.

## 2. L2: taxonomy roll-up feasibility

**Facts.**
- The schema already has a hierarchy: `Topic.parent` (one parent; cycles and
  dangling references are rejected by `taxonomy._parent_problems`).
- *Measured:* parents are set on 19/47 cardiology Topics, 3/18 Methods, 5/19
  Populations, 2/10 Datasets; deriving the parent as "longest MeSH tree number
  in the file that prefixes this one" reproduces every `parent` in cardiology
  and methods, and differs in populations/datasets only where the file
  deliberately omitted a parent (e.g. `humans`→`animals`) or a hand edit
  diverged (`administrative-claims-healthcare`). So a hierarchy is cheap to
  derive wherever `mesh_tree` exists. The hand-authored
  `taxonomies/takotsubo_topics.toml` (untracked) has no tree numbers and no
  parents; roll-up is a no-op there until someone adds them.
- MeSH is a poly-hierarchy (a descriptor can carry several tree numbers); the
  schema keeps one parent. That is acceptable for roll-up and inherits MeSH's
  quirks (e.g. Retrospective Studies sits under Case-Control Studies).
- *Measured (takotsubo-50):* Aggregate placed Papers on the child alone 101
  times and on child and parent together 5 times, so parent counts must
  include descendants' Papers when rolling up.

**The rule.** Mine the Gap's first non-gap type is a "byproduct of MeSH
indexing rules", whose signature was a very high MeSH-hierarchy similarity
(Peng et al. 2017). Our analogue:

1. **Reject** a same-axis cell whose two categories are ancestor and
   descendant (reason: "taxonomy artifact: one category contains the other").
   Aggregate places a Paper on the most specific category, so such a cell is
   empty by construction. *Measured:* 2 of 446 candidates on takotsubo-50.
2. **Downgrade one level** a child-level cell when the parent-level cell holds
   a Paper placed **only on the parent** (not on any of its children): that
   Paper may or may not cover the child. Name the Paper(s) in Tier 3.
   *Measured:* 45 of 446 candidates. Inspection shows why rejection would be
   wrong: e.g. *diabetic cardiomyopathy × heart arrest* is "filled" by a Paper
   indexed as generic *cardiomyopathies* whose subject is Takotsubo, a
   sibling. Rejecting would hide a real gap on ambiguous evidence.
3. **No effect** when the parent cell is filled only through sibling children:
   "no prospective cohort on X, though retrospective ones exist" is exactly a
   gap a researcher wants.

ADR 0008 currently says "reject a child-level gap that is filled at parent
level". Rule 2 makes that a downgrade. Related: #56 (narrow Method taxonomy)
and ADR 0010 (MeSH child descriptors feed both aliases and this hierarchy).

## 3. L3/L4: embeddings

**Constraints.** Free/open-source, works offline in tests (recorded vectors),
ideally also serves chat retrieval (#97). Anthropic offers no embedding model
(its docs point to Voyage AI, a paid API), so "versus an API" means adding a
second paid vendor. That conflicts with the free/open-source rule in ADR 0002.

**Candidates (Hugging Face model cards, checked 2026-10).**

| Model | Licence | Weights | Dim | Notes |
| --- | --- | --- | --- | --- |
| `sentence-transformers/all-MiniLM-L6-v2` | Apache-2.0 | ~91 MB | 384 | General-domain baseline |
| `NeuML/pubmedbert-base-embeddings` | Apache-2.0 | 438 MB | 768 | PubMedBERT fine-tuned for sentence similarity on PubMed |
| `BAAI/bge-small-en-v1.5` | MIT | ~134 MB | 384 | General-domain |
| `pritamdeka/S-PubMedBert-MS-MARCO` | **CC-BY-NC-2.0** | 438 MB | 768 | Non-commercial: excluded |
| `ncbi/MedCPT-*`, `FremyCompany/BioLORD-2023` | "other" | 438 MB | 768 | Non-standard licences: excluded until reviewed |

NeuML's own evaluation (model card; self-reported, not independent) scores it
95.62 average Pearson on three PubMed similarity sets against 93.46 for
all-MiniLM-L6-v2.

**Measured on our data.** All 137 limitation/future-work statements of
takotsubo-50, nearest cross-Paper pairs by cosine similarity:
- **pubmedbert-base-embeddings:** the top 12 pairs are all genuine
  paraphrases (four "retrospective design" limitations, "larger cohort needed
  to confirm", "observational, so no causality", "brain changes cause or
  effect" between klein2017 and templin2019, "randomized trials needed for
  therapy" between bohm2018 and ghadri2018a). Cross-Paper similarity: median
  0.25, 99th percentile 0.59; genuine pairs sit at 0.68–0.76.
- **all-MiniLM-L6-v2:** 3 of its top 4 pairs are false matches driven by
  shared boilerplate ("…in takotsubo cardiomyopathy are needed", e.g. hypoxia
  vs short-term management).
- **Cost:** encoding 137 sentences took 0.9 s on CPU (MiniLM 0.2 s) after a
  one-off model download (~438 MB to the Hugging Face cache). `torch` and
  `transformers` are already in `uv.lock` through the `pdf` extra (docling);
  `sentence-transformers` adds itself and `scikit-learn`.

This one Corpus also shows that **#54's singleton grouping is not for lack of
recurrence**: genuine cross-Paper paraphrases exist and are easy to find.

**Recommendation.**
- Adopt **one shared local embedding capability**:
  `NeuML/pubmedbert-base-embeddings` through `sentence-transformers`, as an
  optional extra (like `pdf`), with vectors recorded to a fixture so tests run
  offline and keyless. Model id and revision live in configuration and in the
  artifact's provenance (as prompt versions do under ADR 0001).
- **Uses:** (a) shortlist cross-Paper limitation pairs (cosine ≥ ~0.65, to be
  tuned) for the grouping step (#54), so the LLM confirms candidate merges
  instead of discovering them; (b) the two-witness shortlist (Q8); (c) chat
  retrieval (#97), replacing keyword matching.
- **L3 near-coverage stays not adopted.** A near-miss between categories is a
  vocabulary problem, and ADR 0010's human-approved alias expansion fixes it at
  the source and inspectably. Embedding category labels would hide it.
- **L4 duplicate merging** for *cell* gaps is not an embedding problem: cell ids
  are unique pairs, and the real duplicates (a category vs its ancestor) are
  handled by L2. For limitation gaps it is (a) above.
- Without the extra installed, everything still works: grouping falls back to
  LLM-only and chat to keyword retrieval.

## 4. The sparse threshold (BRIEF decision −1)

**Measured.** The current rule (empty only under 25 Papers; empty or single at
25+) produced 287 cell candidates at N = 10 and 446 at N = 45. At N = 45, 106
of them are single-Paper cells, but only 8 of those had an expected count
above 1. The other 98 are cells where one Paper is as many as, or more than,
chance predicts: not under-coverage at all.

**Recommendation.** Replace the size switch with one rule at every N: a cell
is a candidate if it is **empty, or holds exactly one Paper while the expected
count is above 1**. That gives 287 + 4 at N = 10 and 340 + 8 at N = 45.
Everything else (ranking, hiding, surfacing) is done by confidence, as ADR 0008
item 2 already says. `SPARSE_CORPUS_SIZE` and `sparse_max_count` retire, and the
artifact records the rule instead of a number.

## 5. L5 prompt design

### GAPMAP figures, now checked against the full paper (arXiv:2510.25055 v1 HTML)

- **Verified:** in the full-paper pilot, "83.3% of participants agreed the
  model's identified knowledge gaps were factually true" and "only 65% of
  proposed future directions were deemed valid, while 35% were judged invalid
  mainly for feasibility reasons". **Caveat:** the respondents were **18
  corresponding authors**, and the model was GPT-4o. It's a small survey of
  each author's own paper, not a benchmark.
- **Verified, with nuance:** "Llama-3.3-70B best F1" holds for **explicit**
  gap extraction on IPBES **without a context limit** (F1 0.8307). With 1K-word
  chunks, GPT-4o mini leads (0.8143 vs 0.8138). For implicit gaps, GPT-5 was
  best.
- **Verified:** implicit-gap inference used **3-shot** prompting; with 0-shot,
  "even GPT-5's outputs degenerated into vague restatements or unsupported
  speculations".
- **Correction to our earlier reading:** TABI's **Bucket is binary**
  (`more_probable` / `least_probable`), used as a calibration check, and the
  models put **10–24 % of correct claims in the less-probable bucket**. The
  LLM's own bucket is therefore a weak confidence signal. L5 should **reject or
  keep**, and may lower the deterministic confidence, but should **not raise**
  it.
- **Licence:** `lhunter-lab/GAPMAP` has **no licence file** and publishes only
  the explicit-extraction prompt, not the TABI prompt. Nothing can be copied
  from it; the prompt below is ours. AIPOCH's rules (MIT) are adapted with
  attribution in the README only.

### Scope and cost

Run L5 only on cell candidates that are **Medium or High after L1, L2, bridges
and two witnesses**, and on all Unanswered Limitations. *Measured:* that is
roughly 13 of 287 cell candidates at N = 10 and 57 of 446 at N = 45 (before
two-witness boosts). Low candidates keep their deterministic card (no Audit
Basis or Minimal Study) and stay listed. Cached by inputs (ADR 0001).

### Draft prompt (`gap-review-v1`)

The prompt receives one candidate and a numbered list of Evidence passages
(each with an `evidence_id`). Grounds may only cite those ids, so the existing
quote verification (ADR 0004) checks them mechanically and nothing outside the
Corpus can enter.

```text
You review one candidate research gap found in a fixed corpus of papers.
Decide whether it is a real, specific, closable gap or a pseudo-gap, using
ONLY the evidence passages provided. You do not know the wider literature;
do not claim anything is unstudied outside this corpus.

Reject the candidate (verdict "reject") if any of these applies, and name it:
- generic_upgrade: it only asks for more data, validation, omics, or sample
  size, not an unanswered question;
- template_reuse: it would read just as true with the disease swapped for
  another;
- pseudo_firstness: its only argument is that few or no papers combine the
  two categories, with no reason the combination matters;
- non_closable: no single coherent study could answer it;
- low_value_replication: it would only repeat what the corpus already shows;
- taxonomy_artifact: the two categories overlap or one implies the other, so
  the empty combination says nothing;
- little_meaning: the combination has no plausible clinical or biological
  meaning.

Otherwise return verdict "keep" with:
- claim: the gap as one specific research question;
- grounds: the evidence_ids that establish what the corpus already covers;
- warrant: one sentence explaining why those grounds imply the gap;
- audit_basis: what the corpus covers around the gap, citing evidence_ids;
- minimal_study: the smallest study that could close it (a suggestion);
- bucket: "more_probable" or "least_probable".

Never write "more research is needed" or similar. Cite only the evidence_ids
given. Examples follow.

<example 1: keep>  ...  </example>
<example 2: reject, pseudo_firstness>  ...  </example>
<example 3: reject, generic_upgrade>  ...  </example>

Candidate:
{candidate}

Evidence passages:
{evidence}
```

The three examples are **synthetic** (fictional condition, fictional citation
keys such as `example2020a`), so no real paper can leak from the prompt into a
card. They cover one keep and the two rejection classes that are most common on
real Corpora. Output is a structured-output schema (ADR 0001) with `verdict`,
`rejection_class | null`, and the fields above.

**Recorded fixture.** One `RecordedLlmClient` JSON file per review prompt
version, keyed by `request_fingerprint`, holding three responses over the
committed fixture Corpus: one keep, one `pseudo_firstness` reject, and one keep
whose Grounds include an id **not** in the input. The test then asserts that
verification drops that ground. Record it once against the live API when the
prompt is final; re-record on every prompt-version bump.

## 6. Small-N power: ranking and showing the caveat

Ranking is Q1's probability, with ties broken by bridge strength (Q7). The
caveat is shown at three levels:

- **Tier 1 (card headline):** the one-line why names the deciding layer in
  plain words with counts, e.g. "If unrelated, about 2.6 of 45 Papers would
  combine these; none do. 3 bridging categories." It states counts, never "p".
- **Tier 3 (show the data):** the probability as a frequency ("a split this
  empty would happen by chance in about 4 of 100 Corpora this size"), expected
  vs observed, and the downgrade/rejection reasons. Label it "ranking score,
  not a significance test".
- **Corpus level (gap list header, next to the confidence toggle):** "With 45
  Papers, an empty combination can only rate High when both categories cover
  about 11 Papers or more." Computed from Q1's table for the Corpus's N, so the
  limit on what the tool can see stays visible (heuristic I2, denominator, in
  `docs/research/ux-heuristics-for-evidence-dashboards.md`).

## 7. Bridges (ABC / AnC)

**Sources.** Swanson (1986) linked two literatures through shared
intermediate terms. LBDiscover's `anc_model` (GPL-3; idea only, no code reuse)
requires **at least 2 B terms** (`length(valid_b_indices) >= 2`) and scores
`sqrt(mean(AB) × mean(BC)) × nB / n_b_terms`. Mine the Gap computed Common
Neighbours and Adamic–Adar scores as gap features (Peng et al. 2017);
Adamic–Adar (2003) weights each shared neighbour by `1 / log(degree)`, so
hubs count less.

**Measured: raw common neighbours make "≥ 2" meaningless here.** One Paper
links all of its categories to each other, so the category graph of a small
Corpus is dense. With "B co-occurs with A and with C in at least one Paper",
**100 %** of N = 10 candidates and **89 %** of N = 45 candidates have ≥ 2
bridges.

| Bridge definition | share of candidates with ≥ 2 bridges, N = 10 | N = 45 |
| --- | --- | --- |
| any category, links ≥ 1 Paper | 100 % | 89 % |
| any category, links ≥ 2 Papers, near-universal excluded | **5 %** | **12 %** |
| Topic only, links ≥ 2 Papers | 3 % | 0 % |

**Recommendation.** A bridge B for cell A×C is a category on any axis such
that **A–B and B–C are each supported by ≥ 2 Papers**, B is neither A nor C
nor their ancestor or descendant (L2), and B is not **near-universal** (in
≥ 90 % of the Corpus, e.g. `humans` or the Corpus's defining disease). Keep the
**≥ 2 bridges** bar for the boost. Weight bridges by Adamic–Adar only to
order them in Tier 3 and Audit Basis, not to decide the boost: at this scale
the link-support rule does the filtering.

## 8. Two-witness intersection and limitation-type buckets

**Measured: lexical matching finds nothing.** Matching limitation statements
against category labels and aliases (whole-word, case-insensitive) hit only
25 of 137 statements on takotsubo-50, mostly the Corpus's defining disease
("takotsubo syndrome"). **Zero** cell candidates had both their categories
named by one limitation group (takotsubo-15: also zero). Authors name designs
("prospective studies"), populations ("female") and settings, rarely
taxonomy labels.

**Recommendation: tag, then intersect.**
1. One cached LLM pass per Corpus (batched, cheap tier) tags each limitation
   group with (a) one **limitation-type bucket** and (b) the observed category
   ids it is about, chosen from the Corpus's observed categories, the same
   mapping job Aggregate already does for facts. The embedding shortlist (Q3)
   can preselect candidate categories but is not required.
2. Intersection is deterministic: a cell is **corroborated** when one group is
   tagged with both of its categories, or with one category and a bucket
   routed to the other category's axis (below).
3. **Corroborated → +1 level** and the label "also flagged by authors" with
   the statements in Tier 2. **One category only → no change**, listed in
   Tier 3 as a related limitation. **None → no change**, with "no author in the
   Corpus mentions this" in Tier 3.

ADR 0008 says an unmentioned empty cell is downgraded. On real data almost no
cell is mentioned, so that rule would push every candidate down one level and
make the two-witness layer a blanket penalty rather than a signal. Recommend
**boost-only**.

**Buckets.** Adapted from the self-acknowledged-limitation data model of Lan
et al. (2024), 15 top-level types annotated on 200 RCTs, pruned to what applies
to mixed clinical designs (case reports, registries, cohorts), plus one bucket
for future-work statements, which that model doesn't cover. Each bucket names
the axis it can corroborate:

| Bucket | Lan et al. types folded in | Corroborates axis |
| --- | --- | --- |
| `study_design` | StudyDesign, Randomization, Blinding, Control | Method |
| `population` | Population (incl. VerySpecificPopulation, DiagnosticCriteria), Generalization (population) | Population |
| `setting_data` | Setting (Unicentric), data-source limits | Dataset |
| `sample_size` | Underpowered, SampleSize | none (design-agnostic) |
| `measurement` | Outcome Measures, MissingData | Topic (outcome) |
| `follow_up` | StudyDuration, HighLossToFollowUp | none |
| `analysis` | Statistical Analysis, ConfoundingFactors | none |
| `open_question` | (future-work statements) | Topic |
| `other` | Funding, Other | none |

*Measured:* the recurring cross-Paper limitations on takotsubo-50 are mostly
`study_design` (retrospective, observational) and `sample_size`. Those are
real but generic. The bucket lets the dashboard show recurrence per kind
without letting a generic "small sample" note corroborate a specific cell gap.

## Combining the layers

For a cell candidate:

1. **L1** sets the starting level from `P(X ≤ observed)` (≤ 0.05 High,
   ≤ 0.20 Medium, else Low).
2. **L2** rejects ancestor × descendant cells and downgrades parent-only
   fills by one level.
3. **Bridges:** ≥ 2 bridges → +1 level.
4. **Two witnesses:** corroborated → +1 level.
5. Clamp to Low…High.
6. **L5** (only at Medium+) keeps or rejects; it may lower the level (its
   bucket `least_probable` → −1) but never raises it.

Every step records its layer and reason (ADR 0008 items 1 and 5). *Measured*
with steps 1 and 3 only: Medium+ is 13 of 287 candidates at N = 10 and 57 of
446 at N = 45, versus 1 and 6 under the current heuristic.

## Sources

- Peng Y, Bonifield G, Smalheiser NR. *Gaps within the Biomedical Literature:
  Initial Characterization and Assessment of Strategies for Discovery.* Front
  Res Metr Anal 2017. doi:10.3389/frma.2017.00003 (full text, PMC5736374).
- Salem et al. *GAPMAP.* arXiv:2510.25055 (full-text HTML v1); repository
  `github.com/lhunter-lab/GAPMAP` (README and `scripts/ex_gap_xtract.py`; no
  licence file).
- Swanson DR. *Fish Oil, Raynaud's Syndrome, and Undiscovered Public
  Knowledge.* Perspect Biol Med 1986;30:7–18. doi:10.1353/pbm.1986.0087
  (metadata verified).
- LBDiscover 0.1.0 (CRAN, GPL-3), `R/alternative_models.R` (`anc_model`) and
  `R/abc_model.R` (hypergeometric significance). doi:10.32614/CRAN.package.LBDiscover.
- Adamic LA, Adar E. *Friends and neighbors on the Web.* Social Networks
  2003;25:211–230. doi:10.1016/S0378-8733(03)00009-1 (metadata verified).
- Church KW, Hanks P. *Word Association Norms, Mutual Information, and
  Lexicography.* Computational Linguistics 1990;16(1):22–29 (ACL J90-1003).
- Cochran WG. *Some Methods for Strengthening the Common χ² Tests.* Biometrics
  1954;10:417. doi:10.2307/3001616 (abstract verified; the "≥ 5" guideline is
  quoted from SciPy's documentation).
- SciPy 1.18.1 docstrings: `scipy.stats.chi2_contingency`,
  `scipy.stats.fisher_exact`.
- Tarone RE. *A Modified Bonferroni Method for Discrete Data.* Biometrics
  1990;46:515. doi:10.2307/2531456 (abstract verified).
- Benjamini Y, Hochberg Y. *Controlling the False Discovery Rate.* JRSS B
  1995;57:289–300. doi:10.1111/j.2517-6161.1995.tb02031.x (metadata verified).
- Lan M, Cheng M, Hoang L, ter Riet G, Kilicoglu H. *Automatic categorization
  of self-acknowledged limitations in randomized controlled trial
  publications.* J Biomed Inform 2024. doi:10.1016/j.jbi.2024.104628
  (full text, PMC11807350, Table 1).
- aipoch/medical-research-skills (MIT):
  `medical-research-gap-finder/references/pseudo-gap-rejection-rules.md` and
  `gap-taxonomy-and-audit-standard.md`.
- Anthropic docs, "Embeddings" (Anthropic offers no embedding model).
- Hugging Face model cards and API metadata for the models in Q3 (licence
  tags, file sizes); NeuML evaluation table is self-reported.

Credit for borrowed ideas belongs in README acknowledgments; none of these
sources is cited in product output.
