# 0004: Per-fact Evidence verification (drop-and-record, not whole-paper reject)

## Status

Accepted (refines ADR 0001)

## Context

The extract stage grounds every fact in Evidence: a verbatim passage the LLM
copied from the Paper, mechanically re-checked against the parsed text so no
invented quote reaches the artifact (CODING_STANDARDS > Research integrity).
The original rule was **all-or-nothing per Paper**: a single passage that did
not match discarded that Paper's *entire* Extraction.

The per-stage funnel diagnostic from issue #46 (Step 1) showed this rule, not
PDF parsing, was the dominant end-to-end failure on the real corpora. On
`cardiology`, parsing succeeded for 10/10 Papers but only 2/10 reached the
artifact. The eight rejections split into two kinds:

- **Parser artifacts** — the quote was faithful to the Paper, but the *parsed
  text* was corrupted: a glyph the parser could not decode became the Unicode
  replacement character `U+FFFD` (e.g. `≤` in `≤3mg/L`), or Markdown table
  cell delimiters (`|`) sat between words the LLM quoted without them.
- **Genuine LLM misquotes** — a transcription typo (`bradycardial` →
  `brachycardial`), two non-adjacent spans stitched together, or a page-header
  token injected mid-sentence by the parser.

Crucially, each rejected Paper failed on only 1–3 of its 25–40 facts. The old
rule threw away ~25 verified facts to reject one bad quote.

## Decision

Two changes, both preserving the integrity rule "no unverified quote is ever
shown as Evidence":

1. **Normalize away parser artifacts before matching, never LLM errors.**
   Verification canonicalizes both the quote and the parsed text (Unicode NFKC,
   Markdown table pipes to whitespace, collapsed whitespace) and treats each
   `U+FFFD` in the parsed text as a wildcard for exactly one character — the
   parser already declared that position undecodable. Matching still requires
   every *other* character to be identical, so a typo or stitched span, which
   has no lost glyph to excuse it, still fails. Normalization undoes only
   transformations the parser introduced.

2. **Verify per fact, not per Paper.** A fact whose Evidence still fails is
   *dropped* and recorded as an `UnverifiedFact` (citation key, field, the
   rejected passage, and why) in the `unverified` list of `extractions.json`.
   The Paper keeps its verified facts. A Paper is a failure only when it was
   never parsed or when *none* of its facts verify.

Promoting a human-reviewed Extraction to a **Gold Extraction** fixture stays
strict and all-or-nothing (`promote_gold` still refuses any unverifiable
quote), because a regression fixture must be exact.

## Consequences

- End-to-end extraction on `cardiology` rose from 2/10 to 10/10, clearing the
  issue #46 ≥90% target, with no loosening of what counts as verified Evidence.
- Nothing is hidden: dropped facts are recorded in the artifact and surfaced by
  the funnel diagnostic (and, per issue #46 Step 3, by the dashboard's
  incompleteness banner). This is the stage's "surface, don't block" posture.
- The displayed corpus is now slightly less complete per Paper (a few facts are
  absent) in exchange for far more Papers being present at all. Because gaps
  are detected across Papers, breadth matters more than per-Paper completeness.
- The `U+FFFD` wildcard is a bounded relaxation: it can only match positions the
  parser already marked as lost, one character each, inside an otherwise
  verbatim passage. It cannot admit a fabricated quote.
- Fixing the parser artifacts at the source (OCR / a better parser, issue #46
  Step 2) would shrink the dropped-fact tail further; this ADR handles the
  residue that any parser will leave.

## Amendment (2026-10): line-break hyphen joins

Normalization also joins a hyphen at a line break (`in- hospital` →
`in-hospital`, from a PDF wrapping a hyphenated word) in the parsed text before
matching. This undoes a parser transformation of the same class as the table
pipes and `U+FFFD` cases above, so it cannot admit a misquote. Token-overlap or
similarity-threshold matching stays **rejected**: it cannot tell parser damage
from an LLM misquote ("reduced mortality" vs "did not reduce mortality" share
most tokens). Text that multi-column layouts linearized out of order is a parse-quality
problem for the parser chain (ADR 0005), not something the matcher should
excuse.
