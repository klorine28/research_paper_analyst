# 0006: Surface, don't block — the needs-attention queue and manual corrections

## Status

Accepted (refines ADR 0002, ADR 0003; completes issue #46 Step 3)

## Context

Getting the text of every Paper is pivotal, but some PDFs will always resist: a
scan no OCR can read, a file docling crashes on, an extraction the LLM cannot
ground. The pipeline already refuses to abort on one bad Paper (parse and
extract record per-Paper failures and carry on; ADR 0004 drops unverifiable
facts rather than whole Papers). What was missing was making that fallout
*visible* and *fixable*:

- A researcher looking at the dashboard could mistake a partial Corpus for the
  whole one — a research-integrity hazard (CODING_STANDARDS: "show the
  denominator").
- Parse failures were only logged, never persisted, so nothing downstream could
  see them.
- There was no way to rescue a Paper whose PDF simply parsed badly.

The tension: issue #46 Step 3 says the researcher should "paste corrected full
text back into the parse artifact," but ADR 0002/0003 forbid the dashboard from
writing pipeline artifacts or running pipeline logic — and per the repo's rules
an ADR outranks the brief.

## Decision

Three parts, all "surface, don't block":

1. **Persist parse failures.** The parse stage writes an inspectable
   `artifacts/parse-report.json` (like every other stage's artifact), so the
   PDFs no parser could read are on disk for the dashboard to read.

2. **Surface incompleteness, read-only.** A dashboard read model
   (`dashboard.attention`, importing no pipeline stage per ADR 0002) reads the
   manifest, the parse report, and the Extractions artifact and produces:
   - a **loud incompleteness banner** shown on every page, stating how many of
     the N Papers reached the artifact, how many produced nothing, and how many
     are missing facts; and
   - a **needs-attention queue** grouping the fallout into unparsed PDFs,
     rejected extractions, and Papers with dropped facts, each with its reason.
   The CLI mirrors this: `parse` and `extract` still complete the run but exit
   with a distinct non-zero code (`EXIT_INCOMPLETE`) when any Paper fell out, so
   a human or CI notices without the run being hard-blocked.

3. **Corrections as an overlay, applied by the pipeline.** The dashboard's
   correction box writes the researcher's pasted full text to a `judgments/`
   overlay (`parse-corrections.json`), exactly like gap judgments and extraction
   review (ADR 0003) — never to `paper-data/`. A pipeline step,
   `apply-parse-corrections`, reads that overlay and writes the corrected
   `paper-data/<key>.parsed.json` (stamped with the `manual` parser), after
   which `extract` reruns on the fixed text. This honours Step 3's intent while
   keeping the browser out of the deterministic pipeline: the split is the same
   one `promote-gold` already uses.

## Consequences

- ADR 0002/0003's boundary holds: the dashboard still writes only
  researcher-review overlays and runs no pipeline logic. The round-trip that
  Step 3 asked for happens across the dashboard/pipeline seam, not inside the
  browser.
- The denominator is always visible; a partial Corpus can no longer masquerade
  as complete.
- A manually corrected Paper is stored as a single `other` section (a paste has
  no reliable headings). The extract stage reads full text, so facts are
  recovered; the funnel diagnostic still flags the flat structure, which is
  honest.
- `EXIT_INCOMPLETE` makes `parse`/`extract` exit non-zero on any fallout.
  Callers that chain stages should treat code 2 as "completed but incomplete,"
  distinct from code 1 ("failed to run").
- `apply-parse-corrections` is a new pipeline command; corrections only take
  effect after it plus a re-`extract`, keeping every pipeline artifact a product
  of a pipeline run.
