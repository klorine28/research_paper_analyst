# 0003: In-app Extraction Review as an annotation overlay

## Status

Accepted (refines ADR 0002)

## Context

Extraction quality must be human-verified before its Candidate Gaps can be
trusted (ADR 0001: extraction is spot-checked; issue #9 makes this a gate). We
want that review to be a product feature inside the dashboard ("Extraction
review & curation"), not only a one-off test gate. ADR 0002 says the dashboard
"only reads that artifact and never runs pipeline logic" — yet it already
persists the researcher's accept/reject gap judgments to `judgments/`. The
open question was whether in-app corrections should change the pipeline's
Extraction.

## Decision

Extraction Review is **annotation-only**. The reviewer's per-field verdicts —
approve, edit, flag as wrong, remove a hallucinated fact, add a missing fact —
are written to a researcher-review overlay under the Corpus's `judgments/`
directory, exactly like gap judgments. The overlay never modifies
`extractions.json`, and `aggregate`/`detect` keep running off the raw
Extraction, so the pipeline stays deterministic and reproducible.

Corrections are held to the same grounding rule as the extract stage
(CODING_STANDARDS > Research integrity): any edited or added Evidence passage
must be a verbatim substring of the Paper's parsed text
(`paper-data/<key>.parsed.json`), re-verified before it is saved; an ungrounded
correction is refused. Because the dashboard may not import a pipeline stage
(ADR 0002), it owns its own copy of the verbatim-matching read logic, mirroring
the existing read-model pattern.

A human-verified Extraction can be promoted to a **Gold Extraction** — a
committed regression fixture — by a pipeline-side `promote-gold` step (not the
browser), which reads `extractions.json` plus the overlay. That step is what
closes issue #9.

## Consequences

- ADR 0002's boundary is refined, not broken: the dashboard writes only
  *researcher-review overlays* (gap judgments, extraction review), never
  *pipeline artifacts*, and still runs no pipeline logic.
- Detect/aggregate results are unaffected by review, preserving
  reproducibility; the overlay conveys human trust and produces gold fixtures,
  it does not silently change gaps.
- The verbatim matcher is duplicated between the pipeline and the dashboard;
  the two must normalise text identically, or a correction valid in one could
  be rejected by the other. This is a spec-level concern for the implementing
  issue.
