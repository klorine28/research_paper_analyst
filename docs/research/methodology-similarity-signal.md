# Research: methodology-similarity signal design

Resolves GitHub issue #52 (child of the v2 epic #44). This is a **design-before-
build** note: it scopes a **technique-level methodology-similarity** signal that
goes beyond the method **co-occurrence** the six-graph layer already ships, and
sets the honesty bar it must clear, before any extraction or graph code is
written. No pipeline code ships from this issue.

## The question

Graph 6 of the meta-analysis layer (#49) is **method co-occurrence**: two Papers
are linked when they share a normalized Method _category_ (RCT, cohort,
retrospective…). That is coarse. Two Papers can both be "cohort studies" yet use
very different techniques; two Papers in different categories can share a
technique. This task designs a signal that measures **how similar two Papers'
methods actually are at the technique level**, and decides what data it needs.

Two constraints frame every option:

- **No data exists today.** The signal has to be designed _and the data
  extracted_ first; only then can a graph render it. (This is why #52 is a
  research task, not a graph child of #49.)
- **It must stay honest and traceable.** Any similarity edge must be
  **explainable** — tied back to specific extracted method text in both Papers —
  not a black-box score (`CODING_STANDARDS.md` > Research integrity;
  `CONTEXT.md` > Coverage Matrix is "descriptive only", never a quality verdict).

## Sources

Primary sources used, all first-party:

- The method-normalisation path: `src/research_gap_dashboard/taxonomy.py` and
  `src/research_gap_dashboard/aggregate.py` (free-text method phrases → Method
  categories; unmapped terms surfaced, never dropped), and `docs/taxonomy.md`.
- The co-occurrence graph: `src/research_gap_dashboard/dashboard/meta_analysis.py`
  (`build_cooccurrence`, the Method axis) — the signal this task extends.
- The LLM seam `src/research_gap_dashboard/llm.py` (cached, prompt-versioned,
  structured output — the reproducibility contract from ADR 0001).
- The audit `docs/research/meta-analysis-graph-audit.md`, which found the Method
  axis thin on real corpora: on `takotsubo-15`, 52 method facts extracted but
  only 4 mapped to Method categories (the motivation for a richer signal, and
  context for issue #56).
- `CONTEXT.md` (Normalized Facts, Evidence, Coverage Matrix).

## Why co-occurrence is not enough, and what "technique-level" means

Co-occurrence operates on the shared **taxonomy category**. The extracted method
_facts_ underneath carry far more — "transthoracic echocardiography with strain
imaging", "left ventriculography", "cardiac MRI with late gadolinium
enhancement". A technique-level signal compares **those extracted method
statements**, so Papers that share a concrete technique are linked even when their
study-design category differs, and Papers in the same category are _not_ linked if
their techniques diverge. The audit's finding — most method facts never reach a
category — means this richer signal also _rescues_ information the category axis
currently discards.

## Options weighed

### Option A — Embedding similarity over extracted method statements

Embed each Paper's method statements (or a concatenated method profile) and link
Papers whose embeddings are close (cosine ≥ threshold).

- **Pros:** cheap at 10–75 Papers; fully deterministic given a fixed model;
  no per-pair LLM call.
- **Cons / honesty risk:** a cosine number is the archetypal black box. To meet
  the "explainable edge" bar it must be paired with the _specific_ method
  statements driving the match (e.g. nearest-statement pairs), or it fails the
  standard. Adds an embedding dependency and model-version pinning.

### Option B — LLM pairwise similarity judgment

Ask the LLM, per Paper pair, whether the two share a methodology and to **name the
shared technique and quote the supporting method statement from each Paper**.

- **Pros:** the explanation is native — the model returns the shared technique and
  the two verbatim statements, which _is_ the traceable edge. Handles synonymy and
  paraphrase well.
- **Cons:** O(n²) pairs (manageable at ≤75 Papers: ≤~2,775 pairs, cached per
  ADR 0001); must be constrained to **quote only extracted method text**, with
  every quote validated verbatim against the Paper's Normalized Facts/Evidence
  before an edge is drawn (same discipline as `analytics.py` citation grounding),
  or it will paraphrase and drift.

### Option C — Structured technique taxonomy (second axis)

Extend extraction to tag a controlled **technique** vocabulary (distinct from the
study-design Method axis), then co-occur on techniques — essentially the #56
"second clinical-procedure/technique axis" idea applied to similarity.

- **Pros:** fully explainable (shared controlled terms), reuses the existing
  co-occurrence machinery and unmapped-term surfacing.
- **Cons:** a controlled technique vocabulary is a large, domain-specific curation
  effort and will undercount novel techniques; overlaps with issue #56's scope and
  should be decided together with it.

## Recommendation

**Prefer Option B (LLM pairwise judgment with mandatory verbatim grounding) as the
primary signal, with Option A available only as an explainable variant.** Rationale:

- Option B's output _is_ the explanation the integrity standard demands (shared
  technique + two verbatim method quotes), so the edge is traceable by
  construction, not reconstructed after a score.
- At the Corpus sizes in scope (10–75 Papers), the O(n²) cost is bounded and the
  LLM cache makes reruns reproducible (ADR 0001).
- Option A is acceptable _only_ if shipped with nearest-statement evidence per
  edge; a bare cosine edge must not be rendered.
- Option C should not be pursued independently of issue #56; if a technique axis
  is built there, co-occurrence on it gives similarity for free and this task
  folds into it.

## Honesty requirements any implementation must meet

- **Every edge is explainable:** it carries the shared technique and the verbatim
  method statement from each Paper, each validated against that Paper's extracted
  facts before rendering. No un-grounded quote, no bare score.
- **Descriptive, not evaluative:** the signal says methods are _similar_, never
  that one is _better_ (`CONTEXT.md` > Coverage Matrix is descriptive only).
- **Reproducible:** model name, prompt version, and threshold recorded with each
  result; calls cached by input (ADR 0001).
- **Caveated like every graph:** a methodology-similarity graph names the question
  it answers and inherits the incomplete-Corpus caveat (#44 governing rule).
- **Regression fixtures:** a handful of hand-labelled Paper pairs (similar /
  not-similar, with the expected shared technique) committed as fixtures, tuned
  for precision over recall.

## Scope for implementation

Resolve this research task as: **two sequenced tickets, gated on the extraction
step existing first.**

1. **Extract a method profile per Paper** (the method statements that feed the
   signal) — a prerequisite with no graph yet.
2. **Build the pairwise similarity signal (Option B)** over those profiles, with
   verbatim grounding and fixtures, then **render it as a graph** that reuses the
   meta-analysis layer's network-figure and caveat machinery
   (`meta_analysis.py`).

Decide Option C jointly with issue #56 before starting, since a technique axis
there would change the recommendation.
